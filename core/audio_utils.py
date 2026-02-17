"""
ConCall Local Model — 音訊工具函式

跨服務共用的音訊格式轉換與處理工具。
"""

import struct
from typing import Optional

import numpy as np

# Whisper 和 Pyannote 都需要 16kHz 取樣率
TARGET_SAMPLE_RATE = 16000


def bytes_to_float32(data: bytes) -> np.ndarray:
    """將 WebSocket 收到的 raw bytes 轉成 float32 numpy array。

    瀏覽器 AudioWorklet 輸出的 float32 PCM 資料，
    每個 sample 佔 4 bytes (little-endian)。
    """
    return np.frombuffer(data, dtype=np.float32)


def float32_to_int16(audio: np.ndarray) -> np.ndarray:
    """float32 [-1.0, 1.0] → int16 [-32768, 32767]。

    部分模型 (如 pyannote) 可能偏好 int16 輸入。
    """
    audio = np.clip(audio, -1.0, 1.0)
    return (audio * 32767).astype(np.int16)


def float32_to_bytes(audio: np.ndarray) -> bytes:
    """將 float32 numpy array 轉為 bytes 以便存入 Redis。"""
    return audio.astype(np.float32).tobytes()


def normalize_audio(audio: np.ndarray) -> np.ndarray:
    """正規化音訊振幅至 [-1.0, 1.0] 範圍。"""
    max_val = np.max(np.abs(audio))
    if max_val > 0:
        return audio / max_val
    return audio


def chunk_audio(audio: np.ndarray, chunk_size: int) -> list[np.ndarray]:
    """將音訊切割為固定長度的 chunks。

    Args:
        audio: 輸入音訊 array
        chunk_size: 每個 chunk 的 sample 數

    Returns:
        list of audio chunks
    """
    chunks = []
    for i in range(0, len(audio), chunk_size):
        chunk = audio[i:i + chunk_size]
        if len(chunk) == chunk_size:
            chunks.append(chunk)
        else:
            # 最後一個 chunk 用 0 補齊
            padded = np.zeros(chunk_size, dtype=audio.dtype)
            padded[:len(chunk)] = chunk
            chunks.append(padded)
    return chunks


def compute_rms(audio: np.ndarray) -> float:
    """計算音訊的 RMS (Root Mean Square) 能量值。

    用於簡易的靜音檢測。
    """
    return float(np.sqrt(np.mean(audio ** 2)))


def seconds_to_samples(seconds: float, sample_rate: int = TARGET_SAMPLE_RATE) -> int:
    """將秒數轉為 sample 數。"""
    return int(seconds * sample_rate)


def samples_to_seconds(samples: int, sample_rate: int = TARGET_SAMPLE_RATE) -> float:
    """將 sample 數轉為秒數。"""
    return samples / sample_rate
