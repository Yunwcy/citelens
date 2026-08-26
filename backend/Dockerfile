FROM python:3.12-slim AS base

# ONNX Runtime 需要 libgomp；其餘相依皆為純 Python
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 把向量化模型烘進映像：不這麼做的話，容器每次首度啟動都要下載約 240MB，
# demo 現場的第一次上傳會多等一分鐘，且離線環境無法運作。
# 存放位置明確指定，不用預設的 /tmp —— 部分執行環境會清空 /tmp。
ENV MODEL_CACHE_DIR=/opt/models
RUN python -c "from fastembed import TextEmbedding; \
    TextEmbedding(model_name='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', \
                  threads=1, cache_dir='/opt/models')"

COPY backend/app ./app

ENV STORAGE_DIR=/data
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
