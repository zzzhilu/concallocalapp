# =============================================================================
# app-gateway Dockerfile — Web UI + WebSocket 閘道 (CPU Only)
# =============================================================================
FROM python:3.10-slim

WORKDIR /app

# 系統依賴
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python 依賴
COPY app-gateway/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 共用模組
COPY core/ ./core/

# 應用程式碼
COPY app-gateway/ ./app-gateway/

EXPOSE 8000

CMD ["uvicorn", "app-gateway.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
