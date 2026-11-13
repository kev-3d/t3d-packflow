# syntax=docker/dockerfile:1
#
# Multi-stage build for PackFlow.
#   - runtime (default, last stage): FastAPI serving the compiled React app.
#     Used by Render and by docker-compose.yml.
#   - backend-dev / frontend-dev: hot-reload images used by compose.dev.yml.
# BuildKit only builds the stages required by the selected target.

ARG NODE_VERSION=22
ARG PYTHON_VERSION=3.12

# ---------- Frontend ----------
FROM node:${NODE_VERSION}-bookworm-slim AS frontend-deps
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

# Vite dev server; source code is bind-mounted by compose.dev.yml.
FROM frontend-deps AS frontend-dev
ENV VITE_USE_POLLING=true
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]

FROM frontend-deps AS frontend-build
COPY frontend/ ./
RUN npm run build

# ---------- Backend ----------
FROM python:${PYTHON_VERSION}-slim-bookworm AS backend-base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PACKFLOW_DB_PATH=/app/data/packflow.db
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt \
    && useradd --create-home --uid 10001 packflow \
    && mkdir -p /app/data \
    && chown packflow:packflow /app/data

# API with auto-reload; backend/ is bind-mounted by compose.dev.yml.
FROM backend-base AS backend-dev
ENV WATCHFILES_FORCE_POLLING=true
USER packflow
EXPOSE 8001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--reload", "--reload-dir", "app"]

# ---------- Production (default target) ----------
FROM backend-base AS runtime
ENV PORT=10000 \
    PACKFLOW_FRONTEND_DIR=/app/frontend \
    PACKFLOW_SEED_REFERENCE_CATALOG=true
COPY backend/app/ ./app/
COPY --from=frontend-build /build/frontend/dist/ /app/frontend/
USER packflow
VOLUME ["/app/data"]
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT','10000'), timeout=4)" || exit 1
CMD ["sh", "-c", "exec uvicorn app.web:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
