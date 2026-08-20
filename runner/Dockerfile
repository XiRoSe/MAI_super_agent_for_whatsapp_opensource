FROM python:3.11-slim
WORKDIR /app
# Build context is the runner/ directory (this service is linked to runner/).
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY . /app/runner/
ENV PYTHONUNBUFFERED=1
# Railway provides $PORT
CMD ["sh", "-c", "uvicorn runner.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
