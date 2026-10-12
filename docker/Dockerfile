# syntax=docker/dockerfile:1
# =============================================================================
# Planificateur de Gardes
#   target "dev"  : app + dev tooling (pytest, ruff, mypy), used by docker-compose.yml
#   target "prod" : minimal runtime image (no Poetry), published to ghcr.io
#
# Dependencies are installed by Poetry from poetry.lock into /opt/venv
# (outside /app so the dev bind mount does not hide it).
# =============================================================================

ARG PYTHON_VERSION=3.12

# --- Runtime: system libraries shared by every stage ---------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

# One layer: Debian security fixes (`upgrade`, picked up on every rebuild), WeasyPrint's
# runtime libraries (Pango + HarfBuzz) and fonts for PDF fallback (sorted), a current system
# pip (the base image's has known CVEs; wheels only, no setup scripts), and the runtime user.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
        libharfbuzz-subset0 \
        libharfbuzz0b \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --only-binary=:all: --upgrade "pip==26.2.1" \
    && groupadd --system app \
    && useradd --system --gid app --home /app app
WORKDIR /app

# --- Builder: Poetry + main dependencies -------------------------------------
FROM runtime AS builder

ARG POETRY_VERSION=2.5.1
# Poetry lives in its own venv; the app venv (/opt/venv) is the active one,
# so with virtualenvs.create=false Poetry installs into it.
# Wheels only (pip and Poetry): no third-party setup script is ever executed at build time.
ENV POETRY_HOME=/opt/poetry \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1 \
    POETRY_INSTALLER_ONLY_BINARY=:all: \
    POETRY_CACHE_DIR=/root/.cache/pypoetry \
    PIP_ONLY_BINARY=:all:
RUN python -m venv "$POETRY_HOME" \
    && "$POETRY_HOME/bin/pip" install --only-binary=:all: "poetry==${POETRY_VERSION}" \
    && ln -s "$POETRY_HOME/bin/poetry" /usr/local/bin/poetry \
    && python -m venv "$VIRTUAL_ENV" \
    && "$VIRTUAL_ENV/bin/pip" install --no-cache-dir --only-binary=:all: --upgrade "pip==26.2.1"

COPY pyproject.toml poetry.lock ./
RUN --mount=type=cache,target=/root/.cache/pypoetry \
    poetry install --no-root --only main

# --- Dev ----------------------------------------------------------------------
FROM builder AS dev
RUN --mount=type=cache,target=/root/.cache/pypoetry \
    poetry install --no-root --with dev
COPY . .
RUN chmod +x docker/entrypoint.sh
# Writable cache for `poetry run` as the non-root user.
ENV POETRY_CACHE_DIR=/tmp/pypoetry-cache
USER app
EXPOSE 8000
ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# --- Prod ---------------------------------------------------------------------
FROM runtime AS prod
# Commit SHA of the build, used as the Sentry release (app/observability.py).
ARG GIT_SHA=unknown
# Fontconfig (WeasyPrint) needs a writable cache; the prod container is read-only + tmpfs /tmp.
ENV XDG_CACHE_HOME=/tmp/.cache
COPY --from=builder /opt/venv /opt/venv
# pip is not needed at runtime: remove it from the app venv and from the system Python
# (its vendored urllib3 / msgpack copies are what image scanners flag), plus ensurepip.
RUN /opt/venv/bin/python -m pip uninstall --yes --quiet pip \
    && /usr/local/bin/python -m pip uninstall --yes --quiet pip \
    && rm -rf /usr/local/lib/python3.12/ensurepip \
    && echo "${GIT_SHA}" > /app/VERSION
# Code and config stay owned by root: the `app` user can read / execute them but never
# modify them (it must not be able to rewrite its own code). Nothing under /app is written
# at runtime (caches go to /tmp).
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY --chmod=755 docker/entrypoint.sh ./docker/entrypoint.sh
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
ENTRYPOINT ["docker/entrypoint.sh"]
# --proxy-headers: trust X-Forwarded-* from the reverse proxy (restricted via FORWARDED_ALLOW_IPS).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--workers", "1"]
