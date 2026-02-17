# =============================================================================
# worker-asr Dockerfile — ASR + VAD + Diarization (GPU 0)
#
# GPU 0 混合工作區:
#   Faster-Whisper (~3GB) + Silero VAD (~0.1GB) + Pyannote (~2.5GB) ≈ 5.6GB
# =============================================================================
FROM nvidia/cuda:12.1.1-runtime-ubuntu22.04

# 避免互動式 prompt
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV CUDA_VISIBLE_DEVICES=0

WORKDIR /app

# 系統依賴 + Python 3.10
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.10 \
    python3.10-venv \
    python3-pip \
    build-essential \
    ffmpeg \
    libsndfile1 \
    git \
    && ln -sf /usr/bin/python3.10 /usr/bin/python \
    && ln -sf /usr/bin/pip3 /usr/bin/pip \
    && rm -rf /var/lib/apt/lists/*

# 升級 pip
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Python 依賴
COPY workers/asr/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 共用模組
COPY core/ ./core/

# 應用程式碼
COPY workers/asr/ ./workers/asr/

# 模型下載腳本 (首次啟動時下載)
COPY workers/asr/download_models.py ./download_models.py

CMD ["python", "-m", "workers.asr.main"]
