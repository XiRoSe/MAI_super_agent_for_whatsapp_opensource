# Mai — single image: builds the React dashboard, then the Python backend that
# serves both the API and the SPA (one service, one domain, no CORS).

# ---- Stage 1: build the frontend ----
FROM node:25-slim AS fe
WORKDIR /fe
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
# Empty API URL => the SPA calls the same origin it's served from.
ENV VITE_API_URL=""
RUN npm run build

# ---- Stage 2: the backend (serves API + the built SPA) ----
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY backend ./backend
COPY skills ./skills
RUN pip install --upgrade pip && pip install .

# Bundle the built dashboard; backend.app serves it from ./static.
COPY --from=fe /fe/dist ./static

EXPOSE 7777
CMD ["sh", "-c", "uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-7777}"]
