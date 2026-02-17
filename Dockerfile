# Use official Python 3.14 image
FROM python:3.14-slim

# Install system packages + Node.js + build tools
RUN apt-get update && apt-get install -y \
    curl \
    nodejs \
    npm \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Set Django settings module
ENV DJANGO_SETTINGS_MODULE=ntxconfig.settings.local

# Copy Python requirements
COPY app/requirements/dev.txt requirements.txt

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy frontend package files (for caching)
COPY app/frontend/package*.json frontend/

# Install frontend dependencies
WORKDIR /app/frontend
RUN npm install

# Copy full Django project
WORKDIR /app
COPY app .

# Build frontend assets (if exists)
WORKDIR /app/frontend
RUN npm run build || echo "No frontend build step"

# Back to Django app
WORKDIR /app

# Collect static files
RUN python manage.py collectstatic --noinput || true

# Expose port used by OpenShift
EXPOSE 8080

# Start Django (migrate + gunicorn)
CMD python manage.py migrate && \
    gunicorn ntxconfig.wsgi:application \
    --bind 0.0.0.0:8080
