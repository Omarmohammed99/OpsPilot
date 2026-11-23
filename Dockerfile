FROM ghcr.io/astral-sh/uv:0.10.9 AS uv-bin
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_PROJECT_ENVIRONMENT=/opt/opspilot-venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    OPSPILOT_DATA_DIR=/app/data \
    PATH="/opt/opspilot-venv/bin:${PATH}"

COPY --from=uv-bin /uv /uvx /bin/

WORKDIR /app
RUN addgroup --system opspilot && adduser --system --ingroup opspilot opspilot
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --locked --no-dev --extra checkpoint --no-install-project
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY data/seed/public ./data/seed/public
RUN uv sync --locked --no-dev --no-editable --extra checkpoint \
    && mkdir -p /app/data/chroma \
    && chown -R opspilot:opspilot /app/data/chroma
USER opspilot
EXPOSE 7860
CMD ["sh", "-c", "uvicorn opspilot.main:app --host 0.0.0.0 --port ${PORT:-7860}"]

