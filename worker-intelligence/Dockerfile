# =============================================================================
# worker-intelligence Dockerfile — 翻譯/摘要編排器 (CPU Only)
# 透過 HTTP 呼叫 vLLM OpenAI API，不直接載入 GPU 模型
# =============================================================================
FROM python:3.10-slim

WORKDIR /app

# 系統依賴
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python 依賴
COPY worker-intelligence/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 共用模組
COPY core/ ./core/

# 應用程式碼
COPY worker-intelligence/ ./worker-intelligence/

CMD ["python", "-m", "worker-intelligence.main"]
