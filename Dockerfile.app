# ──────────────────────────────────────────────────────────────────────────────
# dockerfiles/Dockerfile.processor
# Log Processor service (Conversion/ module)
# Runs as a standalone worker that processes incoming log files.
# ──────────────────────────────────────────────────────────────────────────────

FROM public.ecr.aws/docker/library/python:3.11-slim AS builder

WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends gcc libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime image ─────────────────────────────────────────────────────────────
FROM public.ecr.aws/docker/library/python:3.11-slim AS runtime

RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

COPY --from=builder /install /usr/local
COPY Conversion/ ./Conversion/
COPY lambda/lambda_handler.py ./lambda_handler.py

RUN chown -R appuser:appgroup /app
USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOG_LEVEL=INFO

# Processor is event-driven (Lambda/SQS), not a long-running server.
# When run as a container, it processes a single batch then exits.
CMD ["python", "-u", "lambda_handler.py"]
