"""
ConCall Local Model — worker-intelligence

翻譯/摘要編排器 (CPU Only)：
- 訂閱 ch:transcriptions → 即時翻譯 (中↔英自動偵測)
- 監聽 session 結束信號 → 生成會議摘要
- 透過 OpenAI SDK 呼叫 vLLM Server (http://vllm-server:8000/v1)

不直接載入任何 GPU 模型，所有推論透過 HTTP API 完成。
具備 Docker 控制能力，可根據需求啟動/停止 vLLM 容器以節省 GPU 資源。
"""

import asyncio
import json
import logging
import os
import time
from typing import Optional

import redis.asyncio as aioredis
from openai import AsyncOpenAI
import docker

# 共用模組
import sys
sys.path.insert(0, "/app")
from core.redis_keys import (
    SESSION_TRANSCRIPT_PREFIX,
    SESSION_LANG_PREFIX,
    CHANNEL_TRANSCRIPTIONS,
    CHANNEL_TRANSLATIONS,
    CHANNEL_SUMMARY,
    CHANNEL_STATUS,
    SESSION_END_SIGNAL,
)

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("worker-intelligence")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://vllm-server:8000/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "Qwen/Qwen2.5-32B-Instruct-AWQ")

# 翻譯/摘要設定 (ctx=16384)
TRANSLATE_MAX_TOKENS = 512
SUMMARY_MAX_TOKENS = 2048

# 重試設定
MAX_RETRIES = 3
RETRY_DELAY = 2

# ---------------------------------------------------------------------------
# Docker Control
# ---------------------------------------------------------------------------
docker_client = docker.from_env()
VLLM_CONTAINER_NAME = "concall-vllm"

def manage_vllm(action: str):
    """管理 vLLM 容器狀態 (start/stop)"""
    try:
        container = docker_client.containers.get(VLLM_CONTAINER_NAME)
        if action == "start":
            if container.status != "running":
                logger.info(f"啟動 vLLM 容器 ({VLLM_CONTAINER_NAME})...")
                container.start()
            else:
                logger.debug("vLLM 容器已在運行。")
        elif action == "stop":
            if container.status == "running":
                logger.info(f"停止 vLLM 容器 ({VLLM_CONTAINER_NAME}) 以釋放 GPU...")
                container.stop()
            else:
                logger.debug("vLLM 容器已停止。")
    except Exception as e:
        logger.error(f"Docker 控制失敗 ({action}): {e}")

# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------
llm_client: Optional[AsyncOpenAI] = None

def init_llm_client() -> AsyncOpenAI:
    """初始化 OpenAI-Compatible LLM Client。"""
    return AsyncOpenAI(
        base_url=LLM_BASE_URL,
        api_key="not-needed",  # 本地部署不需要 API key
        timeout=120.0,
        max_retries=MAX_RETRIES,
    )

async def ensure_llm_ready(timeout=120):
    """確保 vLLM 已就緒 (若容器未啟動則啟動它)。"""
    global llm_client
    
    # 1. 檢查並啟動容器
    manage_vllm("start")
    
    # 2. 初始化 Client
    if not llm_client:
        llm_client = init_llm_client()
    
    # 3. 等待 API 就緒
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            await llm_client.models.list()
            return True
        except Exception:
            await asyncio.sleep(2)
            
    logger.error("❌ vLLM Server 啟動超時。")
    return False

# ---------------------------------------------------------------------------
# 翻譯功能
# ---------------------------------------------------------------------------
TRANSLATE_SYSTEM_PROMPT = """你是一個專業的即時翻譯員。你的工作是：
1. 如果輸入是中文（包含繁體和簡體），翻譯成英文。
2. 如果輸入是英文，翻譯成繁體中文 (Traditional Chinese)。
3. 如果輸入包含混合語言，翻譯成繁體中文為主、英文為輔。

規則：
- 必須使用繁體中文 (Traditional Chinese)
- 只輸出翻譯結果，不要加任何說明或解釋
- 保持原文的語氣和風格
- 專有名詞保留原文
- 翻譯要自然、流暢"""

async def translate_text(text: str, source_lang: str = "auto") -> dict:
    """翻譯文字。"""
    global llm_client
    
    # 若 LLM 未就緒 (例如中文模式下 GPU 關閉)，直接回傳原文並標記未翻譯
    if not llm_client:
        # 嘗試初始化一次，如果容器是開的就能連上
        try:
           manage_vllm("start") # 確保容器是開的 (如果是翻譯模式)
           llm_client = init_llm_client()
        except:
           pass

    if not text.strip():
        return {"translated_text": "", "source_lang": source_lang, "target_lang": "unknown"}

    try:
        if not llm_client:
             return {"translated_text": "(翻譯未啟用)", "source_lang": source_lang, "target_lang": "unknown", "error": "LLM offline"}

        # 偵測語言方向
        if source_lang == "auto":
            # 簡易偵測: 包含 CJK 字符 → 中文
            has_cjk = any('\u4e00' <= c <= '\u9fff' for c in text)
            source_lang = "zh" if has_cjk else "en"

        target_lang = "en" if source_lang == "zh" else "zh"

        response = await llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": TRANSLATE_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            max_tokens=TRANSLATE_MAX_TOKENS,
            temperature=0.3,
        )

        translated = response.choices[0].message.content.strip()

        return {
            "translated_text": translated,
            "source_lang": source_lang,
            "target_lang": target_lang,
        }

    except Exception as e:
        logger.error(f"翻譯失敗: {e}")
        return {"translated_text": text, "error": str(e)}

# ---------------------------------------------------------------------------
# 摘要功能
# ---------------------------------------------------------------------------
SUMMARY_SYSTEM_PROMPT = """你是專業的會議紀錄整理專家。根據會議逐字稿，用繁體中文按以下 Markdown 格式產出結構化會議紀錄。直接填寫，不要加額外說明。若資訊不足可省略該區塊。

# [會議標題]

**日期**：[從對話推斷或標記今日日期]
**參與者**：[從說話者標籤列出，若無標籤寫「未標註」]

## 重點討論

### [議題 1]
- 討論摘要（1-2句）
- 關鍵觀點

### [議題 2]
- ...（依實際議題數量展開）

## 決議事項
- ✅ [決議 1]
- ✅ [決議 2]

## 待辦事項

| 事項 | 負責人 | 期限 | 狀態 |
|------|--------|------|------|
| 任務描述 | 人名 | 日期 | [ ] 待辦 |

## 後續步驟
- [下一步行動]

## 待議事項
- [延後討論的項目]

規則：
1. 必須使用繁體中文
2. 簡潔扼要，每項限1-2句
3. 重點在結果和行動，非過程
4. 若有說話者標籤請保留
5. 決議和待辦必須具體、可追蹤"""

async def generate_summary(session_id: str, redis_conn: aioredis.Redis) -> str:
    """生成會議摘要（串流模式）。"""
    
    # 1. 確保 vLLM 已啟動 (為了生成摘要，必須強制啟動)
    logger.info(f"Session {session_id}: 準備生成摘要，正在喚醒 GPU...")
    ready = await ensure_llm_ready()
    if not ready:
        return "❌ GPU 喚醒失敗，無法生成摘要。"

    # 從 Redis 取出所有轉寫紀錄
    transcript_key = SESSION_TRANSCRIPT_PREFIX + session_id
    records = await redis_conn.lrange(transcript_key, 0, -1)

    if not records:
        manage_vllm("stop")
        return "⚠️ 此會議沒有轉寫紀錄。"

    # 組合完整的轉寫文本
    full_transcript_parts = []
    for record_str in records:
        try:
            record = json.loads(record_str)
            text = record.get("text", "")
            timestamp = record.get("timestamp", 0)
            
            if timestamp:
                from datetime import datetime
                time_str = datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
                full_transcript_parts.append(f"[{time_str}] {text}")
            else:
                full_transcript_parts.append(text)
        except json.JSONDecodeError:
            continue

    full_transcript = "\n".join(full_transcript_parts)

    if not full_transcript.strip():
        manage_vllm("stop")
        return "⚠️ 轉寫紀錄為空。"

    logger.info(f"Session {session_id}: 生成摘要 (轉寫長度: {len(full_transcript)} chars)...")

    # 截斷過長內容
    max_chars = 10000
    if len(full_transcript) > max_chars:
        truncated = full_transcript[:max_chars]
        last_newline = truncated.rfind('\n')
        if last_newline > 0:
            truncated = truncated[:last_newline]
        full_transcript = truncated + "\n...(內容過長已截斷)..."
        logger.warning(f"Session {session_id}: 已截斷至 {len(full_transcript)} 字元。")

    try:
        # 串流模式生成摘要
        stream = await llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": f"以下是會議轉寫紀錄：\n\n{full_transcript}"},
            ],
            max_tokens=SUMMARY_MAX_TOKENS,
            temperature=0.3,
            stream=True,
        )

        full_summary = ""
        chunk_buffer = ""
        
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                chunk_buffer += delta.content
                full_summary += delta.content
                
                # 每收到一段有意義的內容就推送（遇到換行或累積 >= 20 字元）
                if '\n' in chunk_buffer or len(chunk_buffer) >= 20:
                    await redis_conn.publish(
                        CHANNEL_SUMMARY,
                        json.dumps({
                            "session_id": session_id,
                            "type": "summary_chunk",
                            "chunk": chunk_buffer,
                            "timestamp": time.time(),
                        }, ensure_ascii=False),
                    )
                    chunk_buffer = ""

        # 發送剩餘的 buffer
        if chunk_buffer:
            await redis_conn.publish(
                CHANNEL_SUMMARY,
                json.dumps({
                    "session_id": session_id,
                    "type": "summary_chunk",
                    "chunk": chunk_buffer,
                    "timestamp": time.time(),
                }, ensure_ascii=False),
            )

        # 發送完成信號
        await redis_conn.publish(
            CHANNEL_SUMMARY,
            json.dumps({
                "session_id": session_id,
                "type": "summary_done",
                "summary": full_summary,
                "timestamp": time.time(),
            }, ensure_ascii=False),
        )

        logger.info(f"Session {session_id}: 摘要生成完成 ({len(full_summary)} chars)")
        manage_vllm("stop")
        return full_summary

    except Exception as e:
        logger.error(f"摘要生成失敗: {e}", exc_info=True)
        manage_vllm("stop")
        return f"❌ 摘要生成失敗: {e}"


# ---------------------------------------------------------------------------
# 主迴圈
# ---------------------------------------------------------------------------
async def translation_loop(redis_conn: aioredis.Redis):
    """即時翻譯迴圈：訂閱 ch:transcriptions，翻譯後發布到 ch:translations。"""
    pubsub = redis_conn.pubsub()
    await pubsub.subscribe(CHANNEL_TRANSCRIPTIONS)
    logger.info("翻譯迴圈啟動，訂閱 ch:transcriptions...")

    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue

            try:
                data = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                continue

            text = data.get("text", "")
            session_id = data.get("session_id", "unknown")
            source_lang = data.get("language", "auto")
            
            # 檢查 session 語言偏好：中文模式直接跳過翻譯
            session_lang = await redis_conn.get(SESSION_LANG_PREFIX + session_id)
            if session_lang and session_lang == "zh":
                continue  # 中文會議模式，不需要翻譯

            if not text.strip():
                continue

            # 翻譯
            result = await translate_text(text, source_lang)
            
            if "error" in result:
                continue

            # 發布翻譯結果
            translation_data = {
                "session_id": session_id,
                "original_text": text,
                "translated_text": result.get("translated_text", ""),
                "source_lang": result.get("source_lang", source_lang),
                "target_lang": result.get("target_lang", ""),
                "timestamp": time.time(),
            }

            await redis_conn.publish(
                CHANNEL_TRANSLATIONS,
                json.dumps(translation_data, ensure_ascii=False),
            )

            logger.info(
                f"Session {session_id}: "
                f"[{result.get('source_lang','?')}→{result.get('target_lang','?')}] "
                f"{text[:40]}... → {result.get('translated_text','')[:40]}..."
            )

    except asyncio.CancelledError:
        logger.info("翻譯迴圈取消。")
    finally:
        await pubsub.unsubscribe(CHANNEL_TRANSCRIPTIONS)


async def summary_monitor(redis_conn: aioredis.Redis):
    """監控 session 結束信號，觸發摘要生成。"""
    pubsub = redis_conn.pubsub()
    await pubsub.subscribe(CHANNEL_STATUS)
    logger.info("摘要監控啟動，監聯 session 結束信號...")

    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue

            try:
                data = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                continue

            status = data.get("status", "")
            session_id = data.get("session_id", "")

            if status in ("session_ended",) and session_id:
                logger.info(f"Session {session_id}: 會議結束，開始生成摘要...")

                # 等待片刻，確保最後的轉寫結果已處理完
                await asyncio.sleep(3)

                # 生成摘要（內部已做串流發布）
                summary = await generate_summary(session_id, redis_conn)

                # 如果是錯誤訊息（非串流成功），發佈一次性結果
                if summary.startswith("❌") or summary.startswith("⚠️"):
                    await redis_conn.publish(
                        CHANNEL_SUMMARY,
                        json.dumps({
                            "session_id": session_id,
                            "type": "summary_done",
                            "summary": summary,
                            "timestamp": time.time(),
                        }, ensure_ascii=False),
                    )

                logger.info(f"Session {session_id}: 摘要流程結束。")

    except asyncio.CancelledError:
        logger.info("摘要監控取消。")
    finally:
        await pubsub.unsubscribe(CHANNEL_STATUS)


# ---------------------------------------------------------------------------
# 進入點
# ---------------------------------------------------------------------------
async def main():
    """主入口。"""
    global llm_client

    logger.info("=" * 60)
    logger.info("ConCall worker-intelligence 啟動中...")
    logger.info("  具備 Docker 控制能力：支援自動釋放 GPU")
    logger.info("=" * 60)

    # 初始化 Redis 連線
    redis_conn = aioredis.from_url(REDIS_URL, decode_responses=True)

    # 啟動迴圈
    try:
        await asyncio.gather(
            translation_loop(redis_conn),
            summary_monitor(redis_conn),
        )
    except KeyboardInterrupt:
        logger.info("收到中斷信號。")
    finally:
        await redis_conn.aclose()
        logger.info("worker-intelligence 已關閉。")


if __name__ == "__main__":
    asyncio.run(main())
