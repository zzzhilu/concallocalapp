# =============================================================================
# app-core Dockerfile — Web UI + WebSocket Gateway + Translation/Summary (CPU Only)
# =============================================================================
FROM python:3.10-slim

WORKDIR /app

# 系統依賴
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python 依賴
COPY core/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 共用模組 + 應用程式碼
COPY core/ ./core/

EXPOSE 8000

CMD ["uvicorn", "core.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
