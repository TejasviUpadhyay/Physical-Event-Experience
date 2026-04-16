FROM python:3.12-slim

# Prevent .pyc files and enable unbuffered stdout/stderr for Cloud Run logging.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies in a separate layer so they are cached across rebuilds
# when only application code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Copy application source after dependencies to maximise layer cache reuse.
COPY app/ ./app/

# Cloud Run injects PORT at runtime; default to 8080 if not set.
ENV PORT=8080

# Use exec-form CMD via sh -c so that $PORT is expanded by the shell while
# uvicorn still runs as PID 1 (via exec) and receives SIGTERM directly.
# This ensures graceful shutdown within Cloud Run's termination window.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --log-level info"]
