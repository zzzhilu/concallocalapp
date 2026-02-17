"""
ConCall Local Model — 模型預下載腳本

在 Docker build 階段或首次啟動前執行，
預先下載所有需要的模型到快取目錄。

用法:
    python download_models.py
"""

import os
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("model-downloader")

HF_TOKEN = os.getenv("HF_TOKEN", "")


def download_whisper():
    """下載 Faster-Whisper large-v3 模型 (CTranslate2 格式)。"""
    logger.info("📥 下載 Faster-Whisper large-v3 模型...")
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(
            "large-v3",
            device="cpu",  # 下載時用 CPU 即可
            compute_type="int8",  # 下載不需要 float16
        )
        del model
        logger.info("✅ Faster-Whisper large-v3 下載完成。")
    except Exception as e:
        logger.error(f"❌ Faster-Whisper 下載失敗: {e}")


def download_silero_vad():
    """下載 Silero VAD 模型。"""
    logger.info("📥 下載 Silero VAD 模型...")
    try:
        import torch
        model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            onnx=True,
        )
        del model
        logger.info("✅ Silero VAD 下載完成。")
    except Exception as e:
        logger.error(f"❌ Silero VAD 下載失敗: {e}")


def download_pyannote():
    """下載 Pyannote 說話者分離模型 (需要 HF_TOKEN)。"""
    if not HF_TOKEN:
        logger.warning("⚠️ 未設定 HF_TOKEN，跳過 Pyannote 下載。")
        logger.warning("   請至 https://huggingface.co/settings/tokens 取得 token")
        logger.warning("   並在 .env 中設定 HF_TOKEN=hf_xxxxx")
        return

    logger.info("📥 下載 Pyannote 3.1 說話者分離模型...")
    try:
        from pyannote.audio import Pipeline
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=HF_TOKEN,
        )
        del pipeline
        logger.info("✅ Pyannote 3.1 下載完成。")
    except Exception as e:
        logger.error(f"❌ Pyannote 下載失敗: {e}")
        logger.error("   請確認: 1) HF_TOKEN 是否正確  2) 是否已接受模型使用條款")
        logger.error("   前往 https://huggingface.co/pyannote/speaker-diarization-3.1 接受條款")


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("ConCall Local Model - 模型預下載")
    logger.info("=" * 60)

    download_whisper()
    download_silero_vad()
    download_pyannote()

    logger.info("=" * 60)
    logger.info("所有模型下載流程完成！")
    logger.info("=" * 60)
