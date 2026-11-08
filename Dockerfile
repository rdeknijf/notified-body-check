FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN useradd -m -u 1000 app

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/
RUN uv sync --frozen --no-dev && mkdir -p /data && chown -R app:app /app /data

USER app

ENV DATABASE_PATH=/data/nando.db \
    EXPORT_DIR=/data/exports \
    PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "nando_api.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
