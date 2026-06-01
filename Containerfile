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

# Creates /build/static/css/tailwind.css and /build/static/ntx/app.js
RUN npm run build


# ──────────────────────────────────────────────
# Stage 2: Python base (shared by local + prod)
# ──────────────────────────────────────────────
FROM python:3.11-slim AS python-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System dependency for PostgreSQL
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -r -u 1000 appuser

# Install Python dependencies
COPY app/requirements ./requirements
RUN pip install --upgrade pip && \
    pip install -r requirements/base.txt && \
    pip install psycopg[binary]

# Copy project source
COPY app .

# Copy built frontend assets from stage 1
# npm run build outputs to /build/static/ (one level up from /build/frontend/)
COPY --from=frontend-builder /build/static/ ./static/

# Set ownership
RUN chown -R appuser:appuser /app
# OpenShift runs as a random UID in the root group,
# so ensure group write access as well
RUN chmod -R g+rw /app


# ──────────────────────────────────────────────
# Stage 3: Local development
# ──────────────────────────────────────────────
FROM python-base AS local

# Install dev dependencies if you have a separate file
# RUN pip install -r requirements/dev.txt

USER appuser
EXPOSE 8000

ENV DJANGO_SETTINGS_MODULE=ntxconfig.settings.docker

# Dev server with auto-reload (source code mounted via docker-compose)
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]


# ──────────────────────────────────────────────
# Stage 4: Production (OpenShift)
# ──────────────────────────────────────────────
FROM python-base AS prod

# Install production extras if needed (e.g. gunicorn if not in base.txt)
# RUN pip install gunicorn

ENV DJANGO_SETTINGS_MODULE=ntxconfig.settings.docker

# Collect static files — let it fail loudly if something is wrong
RUN python manage.py collectstatic --noinput

USER appuser
EXPOSE 8000


CMD ["gunicorn", "ntxconfig.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--timeout", "60", \
     "--access-logfile", "-"]
