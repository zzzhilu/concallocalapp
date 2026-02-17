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
import hashlib
import logging
import os
import re
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
    GLOSSARY_KEY,
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

# 翻譯/摘要設定
TRANSLATE_MAX_TOKENS = 512
SUMMARY_MAX_TOKENS = 2048
CHUNK_SUMMARY_MAX_TOKENS = 1024

# 分段摘要設定
CHUNK_SIZE = 5000          # 每段最大字元數（約 25-35 分鐘會議）
CHUNK_THRESHOLD = 10000    # 超過此字元數啟動分段摘要

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
TRANSLATE_PROMPT_EN2ZH = """你是即時口譯員。直接輸出繁體中文翻譯，不要任何說明、解釋或思考過程。忽略口語贅字，保留專有名詞原文。"""

TRANSLATE_PROMPT_ZH2EN = """You are a real-time translator. Output ONLY the English translation. No explanations, no thinking, no extra text."""

def strip_think_tags(text: str) -> str:
    """移除 LLM 回應中的 <think>...</think> 標籤及其內容。"""
    cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    return cleaned if cleaned else text


# ---------------------------------------------------------------------------
# 漸進式翻譯狀態
# ---------------------------------------------------------------------------
# 每個 session 追蹤最近的 segments，合併翻譯以產生更好的結果
_session_segments: dict[str, list[dict]] = {}  # session_id -> [{text, seg_id, timestamp}]
_last_revision_hash: dict[str, str] = {}       # session_id -> md5 hash (去重)
SEGMENT_MERGE_WINDOW = 5    # 最多合併最近 N 個 segments
REVISION_MIN_CHARS = 30     # 合併文字超過此長度才觸發修正翻譯
REVISION_MAX_CHARS = 200    # 合併文字超過此長度不再合併（避免過長句子）
SENTENCE_END_RE = re.compile(r'[.!?。！？；：\n]\s*$')  # 句尾標點偵測


# ---------------------------------------------------------------------------
# 詞彙表快取（避免每次翻譯都開新 Redis 連線）
# ---------------------------------------------------------------------------
_glossary_cache: list | None = None
_glossary_cache_ts: float = 0
GLOSSARY_CACHE_TTL = 30  # 快取 30 秒


async def get_glossary_terms(redis_conn: aioredis.Redis) -> list:
    """從 Redis 讀取詞彙表，帶 TTL 快取。"""
    global _glossary_cache, _glossary_cache_ts
    now = time.time()
    if _glossary_cache is not None and (now - _glossary_cache_ts) < GLOSSARY_CACHE_TTL:
        return _glossary_cache
    try:
        glossary_json = await redis_conn.get(GLOSSARY_KEY)
        if glossary_json:
            _glossary_cache = json.loads(glossary_json)
        else:
            _glossary_cache = []
        _glossary_cache_ts = now
    except Exception as e:
        logger.warning(f"Failed to load glossary from Redis: {e}")
        if _glossary_cache is None:
            _glossary_cache = []
    return _glossary_cache


def _build_glossary_suffix(terms: list, target_lang: str = "zh") -> str:
    """根據詞彙表建構 prompt 後綴。"""
    if not terms:
        return ""
    if target_lang == "zh":
        glossary_lines = "\n".join(f"- {t['en']} → {t['zh']}" for t in terms if t.get('en') and t.get('zh'))
    else:
        glossary_lines = "\n".join(f"- {t['zh']} → {t['en']}" for t in terms if t.get('en') and t.get('zh'))
    if not glossary_lines:
        return ""
    return f"\n專有名詞對照：\n{glossary_lines}"


async def translate_text(text: str, source_lang: str = "auto", redis_conn: aioredis.Redis = None) -> dict:
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

        # 根據翻譯方向選擇對應的 system prompt
        system_prompt = TRANSLATE_PROMPT_EN2ZH if target_lang == "zh" else TRANSLATE_PROMPT_ZH2EN

        # 注入自訂詞彙表（使用快取）
        if redis_conn:
            terms = await get_glossary_terms(redis_conn)
            system_prompt += _build_glossary_suffix(terms, target_lang)

        response = await llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            max_tokens=TRANSLATE_MAX_TOKENS,
            temperature=0.3,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )

        translated = response.choices[0].message.content.strip()
        # 防禦性過濾：移除任何 <think> 標籤
        translated = strip_think_tags(translated)

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

# ---------------------------------------------------------------------------
# 分段摘要 Prompts
# ---------------------------------------------------------------------------
CHUNK_SUMMARY_PROMPT = """你是會議紀錄整理專家。以下是一段會議逐字稿片段，請用繁體中文提取重點：

1. 列出所有討論的議題和關鍵觀點
2. 列出任何決議或待辦事項
3. 保留說話者標籤（如有）
4. 簡潔扼要，只保留重要資訊

直接輸出摘要，不需額外說明。"""

MERGE_SUMMARY_PROMPT = """你是專業的會議紀錄整理專家。以下是同一場會議不同時段的分段摘要。
請將它們整合為一份完整的結構化會議紀錄，用繁體中文按以下 Markdown 格式輸出：

# [會議標題]

**日期**：[從對話推斷或標記今日日期]
**參與者**：[從說話者標籤列出，若無標籤寫「未標註」]

## 重點討論

### [議題 1]
- 討論摘要（1-2句）
- 關鍵觀點

## 決議事項
- ✅ [決議 1]

## 待辦事項

| 事項 | 負責人 | 期限 | 狀態 |
|------|--------|------|------|
| 任務描述 | 人名 | 日期 | [ ] 待辦 |

## 後續步驟
- [下一步行動]

規則：
1. 合併相同議題，去除重複內容
2. 必須使用繁體中文
3. 簡潔扼要
4. 決議和待辦必須具體、可追蹤"""


async def summarize_chunk(chunk_text: str, chunk_index: int, total_chunks: int, glossary_suffix: str = "") -> str:
    """對單段逐字稿生成精簡摘要。"""
    try:
        system_prompt = CHUNK_SUMMARY_PROMPT + glossary_suffix
        response = await llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"以下是會議第 {chunk_index}/{total_chunks} 段逐字稿：\n\n{chunk_text}"},
            ],
            max_tokens=CHUNK_SUMMARY_MAX_TOKENS,
            temperature=0.3,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        result = response.choices[0].message.content.strip()
        return strip_think_tags(result)
    except Exception as e:
        logger.error(f"段落 {chunk_index} 摘要失敗: {e}")
        return f"（第 {chunk_index} 段摘要失敗）"


def split_transcript_into_chunks(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    """將逐字稿按行切分為多個不超過 chunk_size 字元的段落。"""
    lines = text.split("\n")
    chunks = []
    current_chunk = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1  # +1 for newline
        if current_len + line_len > chunk_size and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_len = line_len
        else:
            current_chunk.append(line)
            current_len += line_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


async def generate_summary(session_id: str, redis_conn: aioredis.Redis) -> str:
    """生成會議摘要（串流模式）。超過 CHUNK_THRESHOLD 字元自動啟動分段摘要。"""
    
    # 1. 確保 vLLM 已啟動
    logger.info(f"Session {session_id}: 準備生成摘要，正在喚醒 GPU...")
    ready = await ensure_llm_ready()
    if not ready:
        return "❌ GPU 喚醒失敗，無法生成摘要。"

    # 2. 從 Redis 取出所有轉寫紀錄
    transcript_key = SESSION_TRANSCRIPT_PREFIX + session_id
    records = await redis_conn.lrange(transcript_key, 0, -1)

    if not records:
        manage_vllm("stop")
        return "⚠️ 此會議沒有轉寫紀錄。"

    # 3. 組合完整的轉寫文本
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

    transcript_len = len(full_transcript)
    logger.info(f"Session {session_id}: 生成摘要 (轉寫長度: {transcript_len} chars)...")

    # 4. 讀取詞彙表並建構後綴（摘要也注入專有名詞）
    terms = await get_glossary_terms(redis_conn)
    glossary_suffix = _build_glossary_suffix(terms, "zh")

    # 5. 判斷是否需要分段摘要
    use_chunked = transcript_len > CHUNK_THRESHOLD

    if use_chunked:
        # === MapReduce 分段摘要 ===
        chunks = split_transcript_into_chunks(full_transcript)
        total_chunks = len(chunks)
        logger.info(f"Session {session_id}: 啟動分段摘要 — {total_chunks} 段")

        # 通知前端進入分段模式
        await redis_conn.publish(
            CHANNEL_SUMMARY,
            json.dumps({
                "session_id": session_id,
                "type": "summary_chunk",
                "chunk": f"📋 逐字稿較長（{transcript_len} 字），啟動分段摘要（{total_chunks} 段）...\n\n",
                "timestamp": time.time(),
            }, ensure_ascii=False),
        )

        # Map: 逐段摘要
        chunk_summaries = []
        for i, chunk in enumerate(chunks, 1):
            await redis_conn.publish(
                CHANNEL_SUMMARY,
                json.dumps({
                    "session_id": session_id,
                    "type": "summary_chunk",
                    "chunk": f"⏳ 正在處理第 {i}/{total_chunks} 段...\n",
                    "timestamp": time.time(),
                }, ensure_ascii=False),
            )
            summary = await summarize_chunk(chunk, i, total_chunks, glossary_suffix)
            chunk_summaries.append(f"### 第 {i} 段摘要\n{summary}")
            logger.info(f"Session {session_id}: 段 {i}/{total_chunks} 摘要完成 ({len(summary)} chars)")

        # Reduce: 合併所有段落摘要
        merged_input = "\n\n".join(chunk_summaries)
        logger.info(f"Session {session_id}: 合併 {total_chunks} 段摘要 ({len(merged_input)} chars)...")

        await redis_conn.publish(
            CHANNEL_SUMMARY,
            json.dumps({
                "session_id": session_id,
                "type": "summary_chunk",
                "chunk": f"\n🔄 正在整合所有段落摘要...\n\n",
                "timestamp": time.time(),
            }, ensure_ascii=False),
        )

        # 用 MERGE prompt 生成最終摘要（串流）
        summary_input = merged_input
        summary_system_prompt = MERGE_SUMMARY_PROMPT + glossary_suffix
    else:
        # === 短文直接摘要 ===
        summary_input = full_transcript
        summary_system_prompt = SUMMARY_SYSTEM_PROMPT + glossary_suffix

    try:
        # 串流模式生成摘要
        stream = await llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": summary_system_prompt},
                {"role": "user", "content": f"以下是會議轉寫紀錄：\n\n{summary_input}"},
            ],
            max_tokens=SUMMARY_MAX_TOKENS,
            temperature=0.3,
            stream=True,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
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
    """即時翻譯迴圈：訂閱 ch:transcriptions，翻譯後發布到 ch:translations。
    
    支援漸進式翻譯修正：追蹤最近的 segments，當句子更完整時自動重新翻譯。
    """
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

            # --- 漸進式翻譯：追蹤 segments ---
            seg_id = f"{session_id}_{int(time.time() * 1000)}"
            if session_id not in _session_segments:
                _session_segments[session_id] = []
            
            _session_segments[session_id].append({
                "text": text,
                "seg_id": seg_id,
                "timestamp": time.time(),
            })
            
            # 保持窗口大小
            if len(_session_segments[session_id]) > SEGMENT_MERGE_WINDOW:
                _session_segments[session_id] = _session_segments[session_id][-SEGMENT_MERGE_WINDOW:]

            # 1. 先即時翻譯當前 segment（快速回應）
            result = await translate_text(text, source_lang, redis_conn=redis_conn)
            
            if "error" in result:
                continue

            # 發布即時翻譯結果
            translation_data = {
                "session_id": session_id,
                "original_text": text,
                "translated_text": result.get("translated_text", ""),
                "source_lang": result.get("source_lang", source_lang),
                "target_lang": result.get("target_lang", ""),
                "timestamp": time.time(),
                "seg_id": seg_id,
                "is_revision": False,
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

            # 2. 漸進式修正：偵測句尾才觸發合併翻譯
            recent = _session_segments[session_id]
            has_sentence_end = bool(SENTENCE_END_RE.search(text.strip()))
            merged_text = " ".join(s["text"] for s in recent)
            merged_len = len(merged_text)

            should_revise = (
                len(recent) >= 2
                and merged_len >= REVISION_MIN_CHARS
                and merged_len <= REVISION_MAX_CHARS
                and has_sentence_end  # 只在句尾才觸發修正
            )

            if should_revise:
                # 去重：檢查是否和上次合併的內容相同
                text_hash = hashlib.md5(merged_text.encode()).hexdigest()
                if text_hash != _last_revision_hash.get(session_id):
                    _last_revision_hash[session_id] = text_hash
                    revision_result = await translate_text(merged_text, source_lang, redis_conn=redis_conn)
                    if "error" not in revision_result:
                        revision_data = {
                            "session_id": session_id,
                            "original_text": merged_text,
                            "translated_text": revision_result.get("translated_text", ""),
                            "source_lang": revision_result.get("source_lang", source_lang),
                            "target_lang": revision_result.get("target_lang", ""),
                            "timestamp": time.time(),
                            "seg_ids": [s["seg_id"] for s in recent],
                            "is_revision": True,
                        }
                        await redis_conn.publish(
                            CHANNEL_TRANSLATIONS,
                            json.dumps(revision_data, ensure_ascii=False),
                        )
                        logger.info(
                            f"Session {session_id}: [修正翻譯] "
                            f"合併 {len(recent)} 段 → {revision_result.get('translated_text','')[:60]}..."
                        )

                # 句子完成 → 清空 pending，開始新句子
                _session_segments[session_id] = []
            elif merged_len > REVISION_MAX_CHARS:
                # 超長但未斷句 → 強制清空避免無限堆積
                _session_segments[session_id] = recent[-1:]

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
