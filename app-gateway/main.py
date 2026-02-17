"""
ConCall Local Model — app-gateway

Web UI 閘道服務：
- 提供靜態 Web UI 前端
- 透過 WebSocket 接收瀏覽器的即時音訊串流
- 將音訊推入 Redis audio_queue
- 訂閱 Redis channels 並推送結果回瀏覽器
"""

import asyncio
import json
import logging
import os
import time
import uuid
import docker
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import redis.asyncio as aioredis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# 共用模組
import sys
sys.path.insert(0, "/app")
from core.redis_keys import (
    AUDIO_QUEUE,
    AUDIO_BUFFER_PREFIX,
    SESSION_TRANSCRIPT_PREFIX,
    SESSION_LANG_PREFIX,
    CHANNEL_TRANSCRIPTIONS,
    CHANNEL_TRANSLATIONS,
    CHANNEL_DIARIZATION,
    CHANNEL_SUMMARY,
    CHANNEL_STATUS,
    SESSION_END_SIGNAL,
)
from core.audio_utils import bytes_to_float32, float32_to_bytes

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("app-gateway")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ---------------------------------------------------------------------------
# Docker Control
# ---------------------------------------------------------------------------
docker_client = docker.from_env()
VLLM_CONTAINER_NAME = "concall-vllm"

def manage_vllm_sync(action: str):
    """Sync function to manage vLLM container."""
    try:
        container = docker_client.containers.get(VLLM_CONTAINER_NAME)
        if action == "start":
            if container.status != "running":
                logger.info(f"Starting vLLM container ({VLLM_CONTAINER_NAME})...")
                container.start()
            else:
                logger.debug("vLLM container is already running.")
        elif action == "stop":
            if container.status == "running":
                logger.info(f"Stopping vLLM container ({VLLM_CONTAINER_NAME}) to release GPU...")
                container.stop()
            else:
                logger.debug("vLLM container is already stopped.")
    except Exception as e:
        logger.error(f"Docker control failed ({action}): {e}")

async def manage_vllm(action: str):
    """Async wrapper for manage_vllm_sync."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, manage_vllm_sync, action)

# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
app = FastAPI(title="ConCall Local Model", version="1.0.0")

# 靜態檔案 (Web UI)
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def root():
    """提供主頁面。"""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))



@app.get("/health")
async def health():
    """健康檢查端點。"""
    return {"status": "ok", "service": "app-gateway"}


@app.post("/shutdown")
async def shutdown_services():
    """
    關閉所有服務端點。
    透過 Docker Socket 關閉此專案的所有容器。
    """
    logger.info("Shutdown request received. Stopping containers...")
    
    def stop_containers():
        # 1. 取得當前容器 ID (Hostname)
        import socket
        current_container_id = socket.gethostname()
        
        # 2. 嘗試找出所屬的 Docker Compose Project
        try:
            current = docker_client.containers.get(current_container_id)
            project_name = current.labels.get("com.docker.compose.project")
            
            if not project_name:
                logger.warning("Cannot determine Docker Compose project name. Stopping by known names.")
                target_containers = ["concall-gateway", "concall-asr", "concall-intelligence", "concall-vllm", "concall-redis"]
                filters = {"name": target_containers}
            else:
                logger.info(f"Identified project: {project_name}")
                filters = {"label": f"com.docker.compose.project={project_name}"}

            containers = docker_client.containers.list(filters=filters)
            
            for c in containers:
                # 不要在這裡自殺，留到最後
                if c.id.startswith(current_container_id) or c.name == "concall-gateway":
                    continue
                logger.info(f"Stopping {c.name}...")
                c.stop()
            
            # 最後關閉自己
            logger.info("Stopping app-gateway...")
            current.stop()
            
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            raise

    # Run in background to allow response to return
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, stop_containers)
    
    return {"status": "shutdown_initiated", "message": "Services are stopping..."}


# ---------------------------------------------------------------------------
# WebSocket 連線管理
# ---------------------------------------------------------------------------
class ConnectionManager:
    """管理活動的 WebSocket 連線。"""

    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, session_id: str, websocket: WebSocket, accept: bool = True):
        if accept:
            await websocket.accept()
        self.active_connections[session_id] = websocket
        logger.info(f"Client connected: session={session_id}, total={len(self.active_connections)}")

    def disconnect(self, session_id: str):
        self.active_connections.pop(session_id, None)
        logger.info(f"Client disconnected: session={session_id}, total={len(self.active_connections)}")

    async def send_json(self, session_id: str, data: dict):
        ws = self.active_connections.get(session_id)
        if ws:
            try:
                await ws.send_json(data)
            except Exception as e:
                logger.warning(f"Failed to send to {session_id}: {e}")

    async def broadcast_json(self, data: dict):
        """廣播 JSON 給所有連線。"""
        disconnected = []
        for sid, ws in self.active_connections.items():
            try:
                await ws.send_json(data)
            except Exception:
                disconnected.append(sid)
        for sid in disconnected:
            self.disconnect(sid)


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Redis 訂閱 → WebSocket 推送
# ---------------------------------------------------------------------------
async def redis_subscriber():
    """後台任務：訂閱 Redis channels，將結果推送給所有 WebSocket client。"""
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    pubsub = r.pubsub()

    await pubsub.subscribe(
        CHANNEL_TRANSCRIPTIONS,
        CHANNEL_TRANSLATIONS,
        CHANNEL_DIARIZATION,
        CHANNEL_SUMMARY,
        CHANNEL_STATUS,
    )
    logger.info("Redis subscriber started, listening on channels...")

    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue

            channel = message["channel"]
            try:
                data = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                data = {"raw": message["data"]}

            # 根據 channel 分類事件類型
            event_type_map = {
                CHANNEL_TRANSCRIPTIONS: "transcription",
                CHANNEL_TRANSLATIONS: "translation",
                CHANNEL_DIARIZATION: "diarization",
                CHANNEL_SUMMARY: "summary",
                CHANNEL_STATUS: "status",
            }
            event_type = event_type_map.get(channel, "unknown")

            payload = {
                "event": event_type,
                "data": data,
                "timestamp": time.time(),
            }

            # 若有 session_id，發送給特定 client；否則廣播
            session_id = data.get("session_id") if isinstance(data, dict) else None
            if session_id and session_id in manager.active_connections:
                await manager.send_json(session_id, payload)
            else:
                await manager.broadcast_json(payload)

    except asyncio.CancelledError:
        logger.info("Redis subscriber cancelled.")
    finally:
        await pubsub.unsubscribe()
        await r.aclose()


@app.on_event("startup")
async def startup():
    """啟動 Redis 訂閱後台任務。"""
    app.state.subscriber_task = asyncio.create_task(redis_subscriber())
    logger.info("app-gateway started.")


@app.on_event("shutdown")
async def shutdown():
    """關閉後台任務。"""
    task = getattr(app.state, "subscriber_task", None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    logger.info("app-gateway shutdown.")


# ---------------------------------------------------------------------------
# WebSocket 端點: 接收音訊串流
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket 端點：接收瀏覽器的即時音訊串流。

    Protocol:
    - Client 連線後先發送 JSON: {"action": "start", "session_id": "...", "language": "zh|en|bilingual"}
    - 之後持續發送 binary frames (float32 PCM audio)
    - 結束時發送 JSON: {"action": "stop"}
    """
    session_id = str(uuid.uuid4())
    r = aioredis.from_url(REDIS_URL)

    try:
        await manager.connect(session_id, websocket)

        # 發送 session ID 給 client
        await websocket.send_json({
            "event": "connected",
            "data": {"session_id": session_id},
        })

        chunk_count = 0

        while True:
            message = await websocket.receive()

            # 處理文字訊息 (控制指令)
            if "text" in message:
                try:
                    cmd = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue

                action = cmd.get("action", "")

                if action == "start":
                    # 可選：client 提供自訂 session_id
                    custom_sid = cmd.get("session_id")
                    if custom_sid:
                        manager.disconnect(session_id)
                        session_id = custom_sid
                        await manager.connect(session_id, websocket, accept=False)

                    # 語言選擇與 Docker 控制
                    language = cmd.get("language", "zh")
                    logger.info(f"Recording started: session={session_id}, language={language}")
                    
                    # 將語言偏好存入 Redis，供 worker-intelligence 讀取
                    await r.set(SESSION_LANG_PREFIX + session_id, language)
                    
                    if language in ["en", "bilingual", "en-translate"]:
                        # 啟動 vLLM (非同步)
                        logger.info("Mode: English/Bilingual -> Starting vLLM...")
                        asyncio.create_task(manage_vllm("start"))
                    else:
                        # 停止 vLLM (節省資源)
                        logger.info("Mode: Chinese -> Stopping vLLM...")
                        asyncio.create_task(manage_vllm("stop"))

                    await websocket.send_json({
                        "event": "status",
                        "data": {"message": "recording_started", "session_id": session_id, "language": language},
                    })

                elif action == "stop":
                    logger.info(f"Recording stopped: session={session_id}")
                    # 發送結束信號到 Redis
                    await r.set(SESSION_END_SIGNAL, session_id)
                    await r.publish(CHANNEL_STATUS, json.dumps({
                        "session_id": session_id,
                        "status": "session_ended",
                    }))
                    await websocket.send_json({
                        "event": "status",
                        "data": {"message": "recording_stopped", "session_id": session_id},
                    })

            # 處理二進位訊息 (音訊資料)
            elif "bytes" in message:
                audio_bytes = message["bytes"]
                if len(audio_bytes) == 0:
                    continue

                # 將音訊 chunk 推入 Redis 佇列
                # 格式: JSON 包裹 session_id + base64 audio (或直接 bytes)
                audio_data = {
                    "session_id": session_id,
                    "audio": audio_bytes.hex(),  # hex 編碼以便 JSON 序列化
                    "timestamp": time.time(),
                    "chunk_index": chunk_count,
                }
                await r.lpush(AUDIO_QUEUE, json.dumps(audio_data))
                chunk_count += 1

                if chunk_count % 100 == 0:
                    logger.debug(f"Session {session_id}: pushed {chunk_count} chunks")

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: session={session_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
    finally:
        manager.disconnect(session_id)
        # 若連線意外中斷，也發送結束信號
        try:
            await r.set(SESSION_END_SIGNAL, session_id)
            await r.publish(CHANNEL_STATUS, json.dumps({
                "session_id": session_id,
                "status": "session_disconnected",
            }))
        except Exception:
            pass
        await r.aclose()
