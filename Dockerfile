# syntax=docker/dockerfile:1
# v2 hosted image: the same MCP server over Streamable HTTP. Follows uv's Docker guide (docs.astral.sh/uv/guides/integration/docker):
# uv copied from its pinned image, bytecode compiled, dependencies in a cached layer before the project, multi-stage.
# Never bake secrets or client data into the image: .dockerignore lets in only src/, config/ and the project files.

ARG PYTHON_IMAGE=python:3.11.15-slim-bookworm

FROM ${PYTHON_IMAGE} AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 UV_NO_DEV=1
WORKDIR /app
# Dependencies first, so code changes reuse this layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project --no-editable --extra pbi-arrow --extra hosted
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --extra pbi-arrow --extra hosted

FROM ${PYTHON_IMAGE}
# git only for the start-up clone and the daily git pull --ff-only of the dbt repo. dbt itself is never installed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --system --gid 10001 dma \
    && useradd --system --uid 10001 --gid dma --no-create-home --shell /usr/sbin/nologin dma \
    && mkdir -p /data/output /data/dbt /data/smx /data/kb \
    && chown dma:dma /data /data/output /data/dbt
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY config ./config
ARG DMA_GIT_COMMIT=unknown
# Only /data (volumes) and /tmp (tmpfs) need to be writable, so the container runs with a read-only root filesystem.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    DMA_TRANSPORT=http \
    DMA_HTTP_HOST=0.0.0.0 \
    DMA_HTTP_PORT=8000 \
    DMA_DATA_DIR=/data \
    DMA_CONFIG_DIR=/app/config \
    DMA_GIT_COMMIT=${DMA_GIT_COMMIT}
USER dma
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, sys, urllib.request; port = os.environ.get('DMA_HTTP_PORT', '8000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=4).status == 200 else 1)"]
# Proxy headers: DMA_PROXY_HEADERS (1 or 0) and uvicorn's FORWARDED_ALLOW_IPS (the TLS proxy's address) at run time.
ENTRYPOINT ["dma-server"]
