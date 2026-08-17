# syntax=docker/dockerfile:1

FROM python:3.13-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies resolve from the lockfile in their own layer, so application
# edits do not force a reinstall on every build.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# collectstatic needs *a* key but not the real one; the runtime value arrives
# through the environment.
RUN SECRET_KEY=build-only python manage.py collectstatic --noinput

# Non-root, and owner of the data directory so SQLite can write its WAL
# sidecar files -- SQLite needs write access to the directory, not just the db.
RUN useradd --system --create-home --uid 1001 ioref \
    && mkdir -p /app/data \
    && chown -R ioref:ioref /app/data
USER ioref

VOLUME ["/app/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/').status==200 else 1)"

# Two workers: enough to keep a slow request from blocking the frontdoor, few
# enough that SQLite's single-writer lock stays uncontended.
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 60"]
