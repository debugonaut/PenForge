# syntax=docker/dockerfile:1.8
# PenForge beta API + durable worker image.
#
# The frontend is built and served separately (Vercel), so this image carries only
# the FastAPI service, the scheduled job worker and their Python dependencies.
# Build from the repository root:
#
#   docker build -f infrastructure/deployment/beta/beta.Dockerfile -t penforge/beta .
#
# The runtime user is non-root (65532). Only the configured temp root is writable;
# all durable state lives in PostgreSQL and private object storage.

FROM python:3.12-slim-bookworm AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir "uv==0.13.1" && \
    uv sync --locked --no-dev --group worker

FROM python:3.12-slim-bookworm AS runtime

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libharfbuzz0b libfribidi0b libraqm0 libjpeg62-turbo zlib1g \
        fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Deterministic engineering fixtures: pinned Unicode data and the pinned Noto OFL
# font. Neither is a runtime secret.
COPY packages ./packages
COPY apps ./apps
COPY services ./services
COPY infrastructure/migrations ./infrastructure/migrations
COPY infrastructure/deployment ./infrastructure/deployment
COPY scripts ./scripts

RUN mkdir -p /tmp/penforge-worker-temp && \
    chown -R 65532:65532 /tmp/penforge-worker-temp

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH=/app/apps/api:/app/packages/contracts:/app/packages/font-engine:/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORKER_TEMP_ROOT=/tmp/penforge-worker-temp \
    PORT=8080

USER 65532:65532

EXPOSE 8080

# The API and the scheduled worker share this container; migrations and quota
# provisioning stay explicit operator steps (BETA_MIGRATE=upgrade, configure_quotas).
CMD ["python", "-B", "infrastructure/deployment/beta/start.py"]
