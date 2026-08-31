# kg_engine — production image (multi-stage)
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    NODE_ENV=production

RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
        || sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null \
    && apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl ca-certificates gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl --retry 5 --retry-delay 5 --retry-all-errors -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Playwright + Chromium — pre-installed as a project dependency for PDF cover rendering
RUN npm install -g playwright && npx playwright install --with-deps chromium

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install -e ".[deepagents]" python-multipart

COPY src/ ./src/
COPY frontend-vue/ ./frontend-vue/

EXPOSE 8888

# FalkorDB graph store — optional but recommended for hybrid search.
# Set KG_FALKORDB_ENABLED=false to disable.
ENV KG_FALKORDB_HOST=falkordb \
    KG_FALKORDB_PORT=6379

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://localhost:8888/api/health || exit 1

CMD ["uvicorn", "src.api.server:app", "--host", "0.0.0.0", "--port", "8888"]