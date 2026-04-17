# ──────────────────────────────────────────────────────────────────────────────
# Dockerfile: Flask Application (Log Generator + API)
# Maps to: Application/ directory in the original repo
# ──────────────────────────────────────────────────────────────────────────────

# Stage 1 – dependency builder (keeps the final image lean)
FROM public.ecr.aws/docker/library/python:3.11-slim AS builder

WORKDIR /build

# Install build tools needed for some Python C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# NOTE: If requirements.txt contains conflicting pins (e.g., boto3 vs botocore mismatch),
# pip will fail here. Fix requirements.txt accordingly.
RUN python -m pip install --upgrade pip && \
    python -m pip install --no-cache-dir --prefix=/install -r requirements.txt

# ──────────────────────────────────────────────────────────────────────────────
# Stage 2 – runtime image
FROM public.ecr.aws/docker/library/python:3.11-slim AS runtime

# Security: run as a non-root user
RUN groupadd --gid 1001 appgroup && \
    useradd  --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

# Copy installed dependencies from builder
COPY --from=builder /install /usr/local

# Copy application source
# Top-level structure:
#   /app/Application/   – Flask app
#   /app/Conversion/    – log parser + csv service (imported at runtime)
#   /app/Dashboard/     – dashboard blueprint + bedrock service
COPY Application/ ./Application/
COPY Conversion/  ./Conversion/
COPY Dashboard/   ./Dashboard/

# Create the logs directory that logger.py writes to
# In AWS this is supplemented by CloudWatch; the file log is kept as fallback.
RUN mkdir -p /app/Application/logs && \
    chown -R appuser:appgroup /app

USER appuser

# Expose the Flask port
EXPOSE 5000

# Health check used by ECS + ALB
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/api/status')"

# Environment defaults (override via ECS task definition)
ENV APP_PORT=5000 \
    APP_HOST=0.0.0.0 \
    FLASK_DEBUG=false \
    LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Start with Gunicorn for production; workers = 2 * CPU + 1
CMD ["sh", "-c", \
     "cd /app/Application && \
      gunicorn \
        --bind 0.0.0.0:${APP_PORT} \
        --workers 3 \
        --threads 2 \
        --timeout 120 \
        --access-logfile - \
        --error-logfile - \
        --log-level info \
        app:app"]
