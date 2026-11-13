# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.10.7 AS uv
FROM python:3.13.18-slim-bookworm AS builder

COPY --from=uv /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock .python-version ./
COPY src ./src
COPY README.md ./
RUN uv sync --locked --no-dev

FROM builder AS builder-dev
RUN uv sync --locked

FROM python:3.13.18-slim-bookworm AS runtime

RUN groupadd -r bore && useradd -r -g bore bore
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY src ./src
COPY README.md ./
COPY config ./config
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts

ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app/src
USER bore

EXPOSE 8000
CMD ["uvicorn", "bore.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM builder-dev AS test
WORKDIR /app
COPY pyproject.toml uv.lock .python-version alembic.ini compose.yaml ./
COPY src ./src
COPY README.md ./
COPY config ./config
COPY migrations ./migrations
COPY scripts ./scripts
COPY tests ./tests
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app/src
CMD ["pytest", "-q"]
