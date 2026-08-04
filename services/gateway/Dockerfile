FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.4.20 /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
COPY packages/guardmem-core   packages/guardmem-core
COPY services/gateway         services/gateway
RUN uv sync --frozen --package gateway --no-dev

EXPOSE 8080
CMD ["uv", "run", "uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8080"]
