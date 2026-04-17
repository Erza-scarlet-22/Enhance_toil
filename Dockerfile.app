# ──────────────────────────────────────────────────────────────────────────────
# dockerfiles/Dockerfile.chatbot
# ──────────────────────────────────────────────────────────────────────────────

FROM public.ecr.aws/docker/library/python:3.11-slim AS builder

WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --upgrade pip && \
    python -m pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime image ─────────────────────────────────────────────────────────────
FROM public.ecr.aws/docker/library/python:3.11-slim

RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

COPY --from=builder /install /usr/local
COPY Application/ ./Application/
COPY Conversion/  ./Conversion/
COPY Dashboard/   ./Dashboard/

RUN mkdir -p /app/Application/logs && \
    chown -R appuser:appgroup /app

USER appuser

EXPOSE 5000

# ✅ Health check → /chatbot
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/chatbot')"

ENV APP_PORT=5000 \
    SERVICE_TYPE=chatbot \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/app

CMD ["sh", "-c", \
     "cd /app/Application && \
      exec gunicorn app:app \
        --bind 0.0.0.0:${APP_PORT} \
        --workers 2 \
        --threads 4 \
        --timeout 180 \
        --access-logfile - \
        --error-logfile - \
        --log-level info"]
