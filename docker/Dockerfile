# syntax=docker/dockerfile:1
# =============================================================================
# Planificateur de Gardes
#   target "dev"  : app + dev tooling (pytest, ruff, mypy), used by docker-compose.yml
#   target "prod" : minimal runtime image, published to ghcr.io
# =============================================================================

ARG PYTHON_VERSION=3.12

# --- Base: runtime system libraries ------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# WeasyPrint (PDF) needs Pango + HarfBuzz; fonts for PDF fallback.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libharfbuzz-subset0 \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Extract dependency lists from pyproject.toml so the layer is cached
# independently of application code.
COPY pyproject.toml ./
RUN python - <<'EOF'
import tomllib
project = tomllib.load(open("pyproject.toml", "rb"))["project"]
open("/tmp/requirements.txt", "w").write("\n".join(project["dependencies"]))
open("/tmp/requirements-dev.txt", "w").write("\n".join(project["optional-dependencies"]["dev"]))
EOF
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r /tmp/requirements.txt

RUN groupadd --system app && useradd --system --gid app --home /app app

# --- Dev ----------------------------------------------------------------------
FROM base AS dev
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r /tmp/requirements-dev.txt
COPY . .
RUN chmod +x docker/entrypoint.sh
USER app
EXPOSE 8000
ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# --- Prod ---------------------------------------------------------------------
FROM base AS prod
ARG APP_RELEASE=dev
ENV APP_RELEASE=${APP_RELEASE}
COPY --chown=app:app app/ ./app/
COPY --chown=app:app alembic/ ./alembic/
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app --chmod=755 docker/entrypoint.sh ./docker/entrypoint.sh
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
ENTRYPOINT ["docker/entrypoint.sh"]
# --proxy-headers: trust X-Forwarded-* from the reverse proxy (restricted via FORWARDED_ALLOW_IPS).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--workers", "1"]
