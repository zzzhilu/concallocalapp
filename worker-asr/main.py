"""
ConCall Local Model — worker-asr

GPU 0 混合工作區：
- Faster-Whisper (large-v3, float16) — 即時語音辨識
- Silero VAD — 語音活動偵測，過濾靜音
- Pyannote 3.1 — 說話者分離 (批次觸發，每 30 秒)

主迴圈：
1. BRPOP audio_queue 取出音訊 chunk
2. 累積 buffer → VAD 檢測 → Whisper 轉寫
3. PUBLISH 結果到 ch:transcriptions
4. 定時觸發 Pyannote diarization → PUBLISH ch:diarization
"""

import asyncio
import json
import io
import logging
import os
import time
from collections import defaultdict

import numpy as np
import redis.asyncio as aioredis
import torch
import opencc

# 共用模組
import sys
sys.path.insert(0, "/app")
from core.redis_keys import (
    AUDIO_QUEUE,
    AUDIO_BUFFER_PREFIX,
    SESSION_TRANSCRIPT_PREFIX,
    CHANNEL_TRANSCRIPTIONS,
    CHANNEL_DIARIZATION,
    CHANNEL_STATUS,
    SESSION_END_SIGNAL,
)
from core.audio_utils import (
    bytes_to_float32,
    float32_to_bytes,
    TARGET_SAMPLE_RATE,
    compute_rms,
)

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("worker-asr")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
ASR_MODEL_SIZE = os.getenv("ASR_MODEL_SIZE", "large-v3")
ASR_COMPUTE_TYPE = os.getenv("ASR_COMPUTE_TYPE", "float16")
HF_TOKEN = os.getenv("HF_TOKEN", "")
DIARIZATION_INTERVAL = int(os.getenv("DIARIZATION_INTERVAL", "30"))

# ASR buffer: 累積約 5 秒的音訊再一次轉寫
ASR_BUFFER_SECONDS = 5.0
ASR_BUFFER_SAMPLES = int(ASR_BUFFER_SECONDS * TARGET_SAMPLE_RATE)

# 靜音 RMS 閾值 (低於此值視為靜音)
SILENCE_RMS_THRESHOLD = 0.01


# ---------------------------------------------------------------------------
# 模型載入
# ---------------------------------------------------------------------------
class ModelManager:
    """管理所有 GPU 上的模型。"""

    def __init__(self):
        self.whisper_model = None
        self.vad_model = None
        self.vad_utils = None
        self.diarization_pipeline = None
        self.converter = opencc.OpenCC('s2t')  # 簡體到繁體轉換器

    def load_whisper(self):
        """載入 Faster-Whisper 模型到 GPU 0。"""
        logger.info(f"載入 Faster-Whisper {ASR_MODEL_SIZE} ({ASR_COMPUTE_TYPE})...")
        from faster_whisper import WhisperModel

        self.whisper_model = WhisperModel(
            ASR_MODEL_SIZE,
            device="cuda",
            compute_type=ASR_COMPUTE_TYPE,
        )
        logger.info("✅ Faster-Whisper 載入完成。")

    def load_vad(self):
        """載入 Silero VAD 模型。"""
        logger.info("載入 Silero VAD...")
        self.vad_model, self.vad_utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            onnx=True,
        )
        logger.info("✅ Silero VAD 載入完成。")

    def load_diarization(self):
        """載入 Pyannote 說話者分離 pipeline。"""
        # 在離線模式下 (HF_HUB_OFFLINE=1)，我們不需要 Token 也能載入掛載的模型
        if not HF_TOKEN and os.getenv("HF_HUB_OFFLINE") != "1":
            logger.warning("⚠️ 未設定 HF_TOKEN 且非離線模式，跳過 Pyannote 載入。")
            # 嘗試繼續，也許有本地 cache


        logger.info("載入 Pyannote 3.1 Speaker Diarization...")
        from pyannote.audio import Pipeline

        try:
            self.diarization_pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                use_auth_token=HF_TOKEN,
            )
            # 將模型移到 GPU 0
            if self.diarization_pipeline:
                self.diarization_pipeline.to(torch.device("cuda"))
                logger.info("✅ Pyannote 3.1 載入完成。")
            else:
                raise ValueError("Pipeline.from_pretrained returned None")
        except Exception as e:
            logger.error(f"⚠️ Pyannote 載入失敗: {e}。說話者分離功能將不可用。")
            self.diarization_pipeline = None

    def load_all(self):
        """載入所有模型。"""
        self.load_whisper()
        self.load_vad()
        self.load_diarization()

    def check_speech(self, audio: np.ndarray) -> bool:
        """使用 Silero VAD 檢測音訊中是否有語音活動。"""
        if self.vad_model is None:
            # 若 VAD 未載入，退回 RMS 能量檢測
            return compute_rms(audio) > SILENCE_RMS_THRESHOLD

        try:
            audio_tensor = torch.from_numpy(audio).float()
            # Silero VAD 需要 16kHz mono
            if len(audio_tensor.shape) > 1:
                audio_tensor = audio_tensor.mean(dim=-1)

            # Silero VAD 要求固定 chunk 大小：16kHz → 512 samples
            VAD_CHUNK_SIZE = 512
            self.vad_model.reset_states()  # 重置狀態避免跨 buffer 干擾

            # 將大 buffer 切成 512-sample 的小塊逐個偵測
            num_chunks = len(audio_tensor) // VAD_CHUNK_SIZE
            if num_chunks == 0:
                # 音訊太短，退回 RMS
                return compute_rms(audio) > SILENCE_RMS_THRESHOLD

            for i in range(num_chunks):
                chunk = audio_tensor[i * VAD_CHUNK_SIZE : (i + 1) * VAD_CHUNK_SIZE]
                speech_prob = self.vad_model(chunk, TARGET_SAMPLE_RATE).item()
                if speech_prob > 0.5:
                    return True  # 任何一個 chunk 有語音就算有

            return False
        except Exception as e:
            logger.warning(f"VAD 檢測失敗: {e}")
            return compute_rms(audio) > SILENCE_RMS_THRESHOLD

    def transcribe(self, audio: np.ndarray) -> list[dict]:
        """使用 Faster-Whisper 轉寫音訊。"""
        if self.whisper_model is None:
            logger.error("Whisper 模型未載入！")
            return []

        try:
            segments, info = self.whisper_model.transcribe(
                audio,
                language=None,  # 自動偵測語言
                beam_size=5,
                vad_filter=True,  # Whisper 內建 VAD 過濾
                vad_parameters=dict(
                    min_silence_duration_ms=500,
                    speech_pad_ms=200,
                ),
            )

            result = []
            for segment in segments:
                text = segment.text.strip()
                # 強制轉繁體
                text = self.converter.convert(text)

                result.append({
                    "start": round(segment.start, 3),
                    "end": round(segment.end, 3),
                    "text": text,
                    "language": info.language,
                    "language_probability": round(info.language_probability, 3),
                })

            return result
        except Exception as e:
            logger.error(f"Whisper 轉寫失敗: {e}", exc_info=True)
            return []

    def diarize(self, audio: np.ndarray) -> list[dict]:
        """使用 Pyannote 進行說話者分離。"""
        if self.diarization_pipeline is None:
            return []

        try:
            # Pyannote 需要 torch tensor 或 audio file
            # 使用 in-memory 方式
            waveform = torch.from_numpy(audio).unsqueeze(0).float()
            audio_input = {"waveform": waveform, "sample_rate": TARGET_SAMPLE_RATE}

            diarization = self.diarization_pipeline(audio_input)

            result = []
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                result.append({
                    "start": round(turn.start, 3),
                    "end": round(turn.end, 3),
                    "speaker": speaker,
                })

            return result
        except Exception as e:
            logger.error(f"Pyannote 分離失敗: {e}", exc_info=True)
            return []


# ---------------------------------------------------------------------------
# Session 管理
# ---------------------------------------------------------------------------
class SessionBuffer:
    """管理每個 session 的音訊 buffer。"""

    def __init__(self):
        self.asr_buffers: dict[str, list[np.ndarray]] = defaultdict(list)
        self.diarization_buffers: dict[str, list[np.ndarray]] = defaultdict(list)
        self.asr_sample_counts: dict[str, int] = defaultdict(int)
        self.diarization_sample_counts: dict[str, int] = defaultdict(int)
        self.segment_offsets: dict[str, float] = defaultdict(float)
        self.diarization_offsets: dict[str, float] = defaultdict(float)

    def add_audio(self, session_id: str, audio: np.ndarray):
        """新增音訊到 buffer。"""
        self.asr_buffers[session_id].append(audio)
        self.asr_sample_counts[session_id] += len(audio)
        self.diarization_buffers[session_id].append(audio)
        self.diarization_sample_counts[session_id] += len(audio)

    def is_asr_ready(self, session_id: str) -> bool:
        """ASR buffer 是否已累積足夠？"""
        return self.asr_sample_counts[session_id] >= ASR_BUFFER_SAMPLES

    def get_asr_audio(self, session_id: str) -> np.ndarray:
        """取出 ASR buffer 並清空。"""
        audio = np.concatenate(self.asr_buffers[session_id])
        offset = self.segment_offsets[session_id]
        self.segment_offsets[session_id] += len(audio) / TARGET_SAMPLE_RATE
        self.asr_buffers[session_id] = []
        self.asr_sample_counts[session_id] = 0
        return audio

    def get_diarization_audio(self, session_id: str) -> tuple[np.ndarray | None, float]:
        """取出 diarization buffer 並清空，返回 (audio, start_time_offset)。"""
        if not self.diarization_buffers[session_id]:
            return None, 0.0
        audio = np.concatenate(self.diarization_buffers[session_id])
        
        offset = self.diarization_offsets[session_id]
        self.diarization_offsets[session_id] += len(audio) / TARGET_SAMPLE_RATE
        
        self.diarization_buffers[session_id] = []
        self.diarization_sample_counts[session_id] = 0
        return audio, offset

    def get_offset(self, session_id: str) -> float:
        """取得當前 session 的時間偏移。"""
        return self.segment_offsets[session_id]

    def clear_session(self, session_id: str):
        """清理 session buffer。"""
        self.asr_buffers.pop(session_id, None)
        self.diarization_buffers.pop(session_id, None)
        self.asr_sample_counts.pop(session_id, None)
        self.diarization_sample_counts.pop(session_id, None)
        self.segment_offsets.pop(session_id, None)
        self.diarization_offsets.pop(session_id, None)


# ---------------------------------------------------------------------------
# 主迴圈
# ---------------------------------------------------------------------------
async def asr_loop(models: ModelManager, session_buf: SessionBuffer, redis_conn: aioredis.Redis):
    """
    ASR 主迴圈:
    1. BRPOP audio_queue
    2. 累積 buffer → VAD → Whisper
    3. PUBLISH 結果
    """
    logger.info("ASR 主迴圈啟動...")

    while True:
        try:
            # 從 Redis 取出音訊 chunk (blocking, timeout 1s)
            result = await redis_conn.brpop(AUDIO_QUEUE, timeout=1)
            if result is None:
                continue

            _, raw_data = result
            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                logger.warning("收到非 JSON 格式的音訊資料，跳過。")
                continue

            session_id = data.get("session_id", "unknown")
            audio_hex = data.get("audio", "")
            if not audio_hex:
                continue

            # hex → bytes → float32 numpy array
            audio_bytes = bytes.fromhex(audio_hex)
            audio_chunk = bytes_to_float32(audio_bytes)

            # 加入 session buffer
            session_buf.add_audio(session_id, audio_chunk)

            # 檢查是否累積足夠的音訊
            if not session_buf.is_asr_ready(session_id):
                continue

            # 取出 buffer
            audio_buffer = session_buf.get_asr_audio(session_id)
            time_offset = session_buf.get_offset(session_id) - (len(audio_buffer) / TARGET_SAMPLE_RATE)

            # VAD 檢測 (快速過濾靜音段)
            has_speech = models.check_speech(audio_buffer)
            if not has_speech:
                logger.debug(f"Session {session_id}: 靜音段，跳過轉寫。")
                continue

            # Whisper 轉寫
            logger.info(f"Session {session_id}: 轉寫 {len(audio_buffer)/TARGET_SAMPLE_RATE:.1f}s 音訊...")
            segments = models.transcribe(audio_buffer)

            if not segments:
                continue

            # 調整時間戳加上偏移量
            full_text_parts = []
            for seg in segments:
                seg["start"] += time_offset
                seg["end"] += time_offset
                full_text_parts.append(seg["text"])

            full_text = " ".join(full_text_parts)

            # 發布結果到 Redis
            result_data = {
                "session_id": session_id,
                "text": full_text,
                "segments": segments,
                "timestamp": time.time(),
                "is_final": True,
                "language": segments[0].get("language", "unknown") if segments else "unknown",
            }

            await redis_conn.publish(
                CHANNEL_TRANSCRIPTIONS,
                json.dumps(result_data, ensure_ascii=False),
            )

            # 同時累積完整轉寫記錄 (供摘要使用)
            await redis_conn.rpush(
                SESSION_TRANSCRIPT_PREFIX + session_id,
                json.dumps(result_data, ensure_ascii=False),
            )

            logger.info(f"Session {session_id}: [{segments[0].get('language', '?')}] {full_text[:80]}...")

        except asyncio.CancelledError:
            logger.info("ASR 迴圈取消。")
            break
        except Exception as e:
            logger.error(f"ASR 迴圈錯誤: {e}", exc_info=True)
            await asyncio.sleep(1)


async def diarization_loop(
    models: ModelManager,
    session_buf: SessionBuffer,
    redis_conn: aioredis.Redis,
):
    """
    說話者分離定時任務:
    每 DIARIZATION_INTERVAL 秒，對各 session 累積的音訊執行 Pyannote pipeline。
    """
    if models.diarization_pipeline is None:
        logger.warning("Pyannote 未載入，說話者分離功能停用。")
        return

    logger.info(f"Diarization 定時任務啟動 (每 {DIARIZATION_INTERVAL}s)...")

    while True:
        try:
            await asyncio.sleep(DIARIZATION_INTERVAL)

            # 遍歷所有活動的 session
            session_ids = list(session_buf.diarization_buffers.keys())
            # 遍歷所有活動的 session
            session_ids = list(session_buf.diarization_buffers.keys())
            for session_id in session_ids:
                audio, time_offset = session_buf.get_diarization_audio(session_id)
                if audio is None or len(audio) < TARGET_SAMPLE_RATE:
                    continue  # 不足 1 秒，跳過

                logger.info(
                    f"Session {session_id}: 執行說話者分離 ({len(audio)/TARGET_SAMPLE_RATE:.1f}s, offset={time_offset:.1f}s)..."
                )

                # Pyannote 推論 (同步，使用 executor 避免阻塞)
                loop = asyncio.get_event_loop()
                speakers = await loop.run_in_executor(
                    None, models.diarize, audio
                )

                if speakers:
                    # 加上時間偏移量 (因為 audio 只是這個 chunk)
                    for s in speakers:
                        s["start"] += time_offset
                        s["end"] += time_offset

                    result_data = {
                        "session_id": session_id,
                        "speakers": speakers,
                        "timestamp": time.time(),
                        "audio_duration": len(audio) / TARGET_SAMPLE_RATE,
                    }

                    await redis_conn.publish(
                        CHANNEL_DIARIZATION,
                        json.dumps(result_data, ensure_ascii=False),
                    )

                    speaker_names = set(s["speaker"] for s in speakers)
                    logger.info(f"Session {session_id}: 偵測到 {len(speaker_names)} 位說話者: {speaker_names}")

        except asyncio.CancelledError:
            logger.info("Diarization 迴圈取消。")
            break
        except Exception as e:
            logger.error(f"Diarization 迴圈錯誤: {e}", exc_info=True)
            await asyncio.sleep(5)


async def session_monitor(session_buf: SessionBuffer, redis_conn: aioredis.Redis):
    """監控 session 結束信號，清理 buffer。"""
    pubsub = redis_conn.pubsub()
    await pubsub.subscribe(CHANNEL_STATUS)

    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        try:
            data = json.loads(message["data"])
            if data.get("status") in ("session_ended", "session_disconnected"):
                sid = data.get("session_id")
                if sid:
                    logger.info(f"Session {sid} 結束，清理 buffer...")
                    session_buf.clear_session(sid)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 進入點
# ---------------------------------------------------------------------------
async def main():
    """主入口。"""
    logger.info("=" * 60)
    logger.info("ConCall worker-asr 啟動中...")
    logger.info(f"  GPU: {os.getenv('CUDA_VISIBLE_DEVICES', 'N/A')}")
    logger.info(f"  Model: {ASR_MODEL_SIZE} ({ASR_COMPUTE_TYPE})")
    logger.info(f"  Diarization Interval: {DIARIZATION_INTERVAL}s")
    logger.info("=" * 60)

    # 載入模型
    models = ModelManager()
    models.load_all()

    # 初始化 Redis 連線
    redis_conn = aioredis.from_url(REDIS_URL, decode_responses=True)

    # 初始化 session buffer
    session_buf = SessionBuffer()

    # 啟動迴圈
    try:
        await asyncio.gather(
            asr_loop(models, session_buf, redis_conn),
            diarization_loop(models, session_buf, redis_conn),
            session_monitor(session_buf, redis_conn),
        )
    except KeyboardInterrupt:
        logger.info("收到中斷信號。")
    finally:
        await redis_conn.aclose()
        logger.info("worker-asr 已關閉。")


if __name__ == "__main__":
    asyncio.run(main())
