FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/logs
ENV APP_PORT=5001 LOG_DIR=/app/logs PYTHONUNBUFFERED=1
EXPOSE 5001
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5001/health')"
CMD ["python", "app.py"]

