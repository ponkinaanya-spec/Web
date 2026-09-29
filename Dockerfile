FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DXA_MODEL_DIR=/app/models \
    DXA_DEVICE=cpu

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN python -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
      torch==2.11.0 torchvision==0.26.0
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY app /app/app
COPY dxa_ml /app/dxa_ml
COPY static /app/static
COPY templates /app/templates
COPY models /app/models
COPY README.md LOCAL_PIPELINE.md MODEL_WEIGHTS.md /app/

RUN python -c "from dxa_ml.model_paths import model_paths; model_paths()" && \
    useradd --system --uid 10001 --create-home dxa && \
    mkdir -p /app/storage/uploads /app/storage/results /app/storage/archive /app/storage/logs && \
    chown -R 10001:10001 /app

USER dxa

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).close()" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
