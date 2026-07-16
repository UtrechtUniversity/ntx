# Multi-stage Containerfile for NTX project
# Usage:
#   Local dev:   docker build --target local -t ntx:dev .
#   Production:  docker build --target prod  -t ntx:prod .

# ──────────────────────────────────────────────
# Stage 1: Build frontend assets (Tailwind CSS + esbuild)
# ──────────────────────────────────────────────
FROM docker.io/library/node:24-slim AS frontend-builder

# The build scripts output to ../static/ relative to the frontend dir,
# so we set up the same directory structure:
#   /build/frontend/   ← package.json, src/, scripts/
#   /build/static/     ← where build:css and build:js write output
WORKDIR /build/frontend

COPY app/frontend/package.json app/frontend/package-lock.json* ./
RUN npm ci

COPY app/frontend/src ./src
COPY app/frontend/scripts ./scripts
COPY app/templates /build/templates

# Creates /build/static/css/tailwind.css and /build/static/ntx/app.js
RUN npm run build


# ──────────────────────────────────────────────
# Stage 2: Python base (shared by local + prod)
# ──────────────────────────────────────────────
FROM docker.io/library/python:3.14-slim AS python-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# System dependency for PostgreSQL
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/*

# Create a conventional local user while keeping the image compatible with
# OpenShift platforms that may replace it with an arbitrary UID in group 0.
RUN useradd -m -r -u 1000 appuser

# Build a root-owned virtualenv. Runtime still switches to appuser, but image
# dependencies stay immutable and independent of a user's home directory.
RUN python -m venv "$VIRTUAL_ENV" && \
    pip install --upgrade pip


# ──────────────────────────────────────────────
# Stage 3: Local development
# ──────────────────────────────────────────────
FROM python-base AS local

COPY app/requirements ./requirements
RUN pip install -r requirements/dev.txt

COPY app .
COPY --from=frontend-builder /build/static/ ./static/

# Keep application code immutable at runtime. Only media needs to be writable
# for local uploads/imports, including when OpenShift runs an arbitrary UID in
# the root group.
RUN mkdir -p /app/media && \
    chown -R 1000:0 /app && \
    chmod -R g+rwx /app

RUN mkdir /.gunicorn && \
    chmod g+rwx /.gunicorn

USER 1000
EXPOSE 8000

ENV DJANGO_SETTINGS_MODULE=ntxconfig.settings.container_local

# Dev server with auto-reload (source code mounted via docker-compose)
CMD ["sh", "-c", "python manage.py migrate --noinput && exec python manage.py runserver 0.0.0.0:8000"]


# ──────────────────────────────────────────────
# Stage 4: Production (OpenShift)
# ──────────────────────────────────────────────
FROM python-base AS cloud

COPY app/requirements ./requirements
RUN pip install -r requirements/prod.txt

COPY app .
COPY --from=frontend-builder /build/static/ ./static/

ENV DJANGO_SETTINGS_MODULE=ntxconfig.settings.production

# Collect static files — let it fail loudly if something is wrong
RUN DJANGO_SECRET_KEY=build-time-collectstatic \
    ALLOWED_HOSTS=localhost \
    DATABASE_URL=sqlite:////tmp/collectstatic.sqlite3 \
    python manage.py collectstatic --noinput

# Keep application code immutable at runtime. Only media needs to be writable
# for local uploads/imports, including when OpenShift runs an arbitrary UID in
# the root group.
RUN mkdir -p /app/media && \
    chown -R 1000:0 /app && \
    chmod -R g+rwx /app

RUN mkdir /.gunicorn && \
    chmod g+rwx /.gunicorn

EXPOSE 8000


# TODO: Remove the inline migrate step once production migrations are
# handled by ArgoCD/Kubernetes as a separate job.
CMD ["sh", "-c", \
     "python manage.py migrate --noinput && \
      exec gunicorn ntxconfig.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers 4 \
      --timeout 60 \
      --access-logfile -"]
