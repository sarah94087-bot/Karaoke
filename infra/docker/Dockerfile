# The API image. Chapter 10: slim Python base, dependencies in a layer of their
# own so that a code change does not reinstall them, run with Uvicorn.
#
# Build context is the repository root:
#   docker build -f infra/docker/Dockerfile .
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # apps/ is imported as a namespace package (apps.api.main); packages/ holds
    # core, audio, lyrics, providers, which are imported by their bare names.
    PYTHONPATH=/app:/app/packages

WORKDIR /app

# --- dependency layer -------------------------------------------------------
# Only pyproject.toml is copied, so this layer is rebuilt when the dependency
# list changes and not when a router does. The deps are read out of the `api`
# optional-dependencies group rather than duplicated into a requirements file,
# which is the usual place those two drift apart.
COPY pyproject.toml infra/docker/requirements.py ./
RUN python requirements.py api > requirements-api.txt && pip install -r requirements-api.txt

# --- code layer -------------------------------------------------------------
COPY packages/ ./packages/
COPY apps/api/ ./apps/api/
# Chapter 10 runs migrations as a separate deploy step, ahead of the new code.
# That step needs a vehicle, and it is this image:
#   docker compose run --rm api alembic upgrade head
COPY alembic.ini ./
COPY migrations/ ./migrations/

# Nothing here needs root, and a free PaaS tier is not a place to find out
# otherwise.
RUN useradd --create-home --uid 10001 karuki && chown -R karuki:karuki /app
USER karuki

EXPOSE 8000

# The keep-alive path, deliberately the unprefixed one: it does not move when
# the API version prefix does. No curl in the slim image, so urllib it is.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/system/health', timeout=3)"

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
