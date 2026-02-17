"""
ConCall Local Model — 全模型下載腳本 (本地執行)

下載所有需要的模型到 HuggingFace 快取目錄。
Docker 容器會掛載同一目錄，避免重複下載。

模型清單:
1. Faster-Whisper large-v3 (CTranslate2 格式, ~3GB)
2. Silero VAD (via torch.hub, ~50MB) — 需額外安裝 torch
3. Pyannote speaker-diarization-3.1 (~200MB, 需 HF Token + 接受條款)
4. Qwen2.5-32B-Instruct-GPTQ-Int4 (~18GB)
"""

import os
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("model-downloader")

HF_TOKEN = os.getenv("HF_TOKEN", "")


def download_whisper_model():
    """下載 Faster-Whisper large-v3 (CTranslate2 格式)。

    faster-whisper 使用 CTranslate2 優化的模型格式。
    Repo: Systran/faster-whisper-large-v3
    """
    logger.info("=" * 60)
    logger.info("📥 [1/4] 下載 Faster-Whisper large-v3...")
    logger.info("    Repo: Systran/faster-whisper-large-v3")
    logger.info("    Size: ~3 GB")
    logger.info("=" * 60)

    try:
        from huggingface_hub import snapshot_download

        path = snapshot_download(
            repo_id="Systran/faster-whisper-large-v3",
            token=HF_TOKEN if HF_TOKEN else None,
        )
        logger.info(f"✅ Faster-Whisper large-v3 下載完成: {path}")
        return True
    except Exception as e:
        logger.error(f"❌ Faster-Whisper 下載失敗: {e}")
        return False


def download_silero_vad():
    """下載 Silero VAD 模型。

    Silero VAD 透過 torch.hub 下載，但我們也可以
    直接從 HuggingFace/GitHub 下載 ONNX 版本。
    """
    logger.info("=" * 60)
    logger.info("📥 [2/4] 下載 Silero VAD...")
    logger.info("    Repo: snakers4/silero-vad")
    logger.info("    Size: ~50 MB")
    logger.info("=" * 60)

    try:
        from huggingface_hub import hf_hub_download

        # 下載 ONNX 版本的 Silero VAD
        path = hf_hub_download(
            repo_id="snakers4/silero-vad",
            filename="silero_vad.onnx",
            token=HF_TOKEN if HF_TOKEN else None,
        )
        logger.info(f"✅ Silero VAD 下載完成: {path}")
        return True
    except Exception as e:
        logger.error(f"❌ Silero VAD 下載失敗: {e}")
        logger.info("   (Silero VAD 也會在 worker-asr 首次啟動時自動下載)")
        return False


def download_pyannote():
    """下載 Pyannote speaker-diarization-3.1。

    ⚠️ 需要:
    1. HF_TOKEN
    2. 先到以下頁面接受使用條款:
       - https://huggingface.co/pyannote/speaker-diarization-3.1
       - https://huggingface.co/pyannote/segmentation-3.0
    """
    logger.info("=" * 60)
    logger.info("📥 [3/4] 下載 Pyannote speaker-diarization-3.1...")
    logger.info("    Repo: pyannote/speaker-diarization-3.1")
    logger.info("    Size: ~200 MB")
    logger.info("=" * 60)

    if not HF_TOKEN:
        logger.warning("⚠️ 未設定 HF_TOKEN，跳過 Pyannote 下載。")
        logger.warning("   請設定環境變數: set HF_TOKEN=hf_xxxxx")
        return False

    try:
        from huggingface_hub import snapshot_download

        # 下載主 pipeline 配置
        path = snapshot_download(
            repo_id="pyannote/speaker-diarization-3.1",
            token=HF_TOKEN,
        )
        logger.info(f"✅ Pyannote diarization config 下載完成: {path}")

        # 下載 segmentation 模型 (pipeline 的依賴)
        logger.info("   下載 Pyannote segmentation-3.0 模型...")
        path2 = snapshot_download(
            repo_id="pyannote/segmentation-3.0",
            token=HF_TOKEN,
        )
        logger.info(f"✅ Pyannote segmentation 下載完成: {path2}")

        # 下載 embedding 模型 (wespeaker)
        logger.info("   下載 speaker embedding 模型...")
        path3 = snapshot_download(
            repo_id="pyannote/wespeaker-voxceleb-resnet34-LM",
            token=HF_TOKEN,
        )
        logger.info(f"✅ Speaker embedding 下載完成: {path3}")

        return True
    except Exception as e:
        logger.error(f"❌ Pyannote 下載失敗: {e}")
        if "401" in str(e) or "403" in str(e):
            logger.error("   ❗ 請確認:")
            logger.error("   1. HF_TOKEN 是否正確")
            logger.error("   2. 是否已到以下頁面接受使用條款:")
            logger.error("      https://huggingface.co/pyannote/speaker-diarization-3.1")
            logger.error("      https://huggingface.co/pyannote/segmentation-3.0")
        return False


def download_qwen_llm():
    """下載 Qwen2.5-32B-Instruct-GPTQ-Int4。

    這是最大的模型 (~18GB)，下載需要較長時間。
    """
    logger.info("=" * 60)
    logger.info("📥 [4/4] 下載 Qwen2.5-32B-Instruct-GPTQ-Int4...")
    logger.info("    Repo: Qwen/Qwen2.5-32B-Instruct-GPTQ-Int4")
    logger.info("    Size: ~18 GB ⚠️ 需要較長時間")
    logger.info("=" * 60)

    try:
        from huggingface_hub import snapshot_download

        path = snapshot_download(
            repo_id="Qwen/Qwen2.5-32B-Instruct-GPTQ-Int4",
            token=HF_TOKEN if HF_TOKEN else None,
        )
        logger.info(f"✅ Qwen2.5-32B-GPTQ 下載完成: {path}")
        return True
    except Exception as e:
        logger.error(f"❌ Qwen2.5-32B 下載失敗: {e}")
        return False


def main():
    logger.info("🚀" + "=" * 58)
    logger.info("  ConCall Local Model — 全模型下載")
    logger.info("  HF Cache: " + os.path.expanduser("~/.cache/huggingface"))
    logger.info("  HF Token: " + ("已設定 ✅" if HF_TOKEN else "未設定 ❌"))
    logger.info("=" * 60)

    results = {}

    results["Faster-Whisper large-v3"] = download_whisper_model()
    results["Silero VAD"] = download_silero_vad()
    results["Pyannote 3.1"] = download_pyannote()
    results["Qwen2.5-32B-GPTQ"] = download_qwen_llm()

    # 總結
    logger.info("")
    logger.info("=" * 60)
    logger.info("📊 下載結果總結:")
    logger.info("=" * 60)
    for name, success in results.items():
        status = "✅ 成功" if success else "❌ 失敗"
        logger.info(f"  {status}  {name}")
    logger.info("=" * 60)

    all_success = all(results.values())
    if all_success:
        logger.info("🎉 所有模型下載完成！可以執行 docker compose up -d")
    else:
        logger.warning("⚠️ 部分模型下載失敗，請檢查上方錯誤訊息。")

    return 0 if all_success else 1


if __name__ == "__main__":
    sys.exit(main())
