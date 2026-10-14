# ============================================================
# Stage 1: Build Tailwind CSS (Optimized for Production Cache)
# ============================================================
FROM node:22-alpine AS tailwind-builder

WORKDIR /app

# Copy configuration and style dependency files first
COPY tailwind.config.js ./
COPY static/css/input.css ./static/css/
COPY templates/ ./templates/
COPY app/ ./app/ 

# Install an exact Tailwind CSS version and build the production stylesheet.
RUN npm install --global --ignore-scripts tailwindcss@3.4.17 && \
    tailwindcss -i ./static/css/input.css -o ./static/css/output.css --minify

# ============================================================
# Stage 2: FastAPI Production Application
# ============================================================
FROM python:3.11-slim

# Set production environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/code \
    PIP_NO_CACHE_DIR=1 \
    ENV=production \
    FASTAPI_ENV=production

WORKDIR /code

# Install system dependencies and clean up apt lists to keep image slim
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libmagic1 \
        curl && \
    rm -rf /var/lib/apt/lists/*

# Create a non-root system user for security compliance
RUN useradd --create-home --uid 1000 appuser

# Install Python dependencies leveraging layer caching
COPY requirements.lock .
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes --requirement requirements.lock

# Copy application source code
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY static/ ./static/
COPY templates/ ./templates/
COPY alembic.ini ./
COPY entrypoint.sh ./

# Copy minified production CSS from Stage 1
COPY --from=tailwind-builder /app/static/css/output.css /code/static/css/output.css

# Setup directory structure, permissions, and make entrypoint executable
RUN mkdir -p uploads logs static/css && \
    chmod +x /code/entrypoint.sh && \
    chown -R appuser:appuser /code

# Switch to non-root user for runtime security
USER appuser

# Document exposed ports (PaaS compatible)
EXPOSE 8000

# Container Health Check (evaluating default port 8000 or overridden env)
HEALTHCHECK --interval=30s \
            --timeout=5s \
            --start-period=20s \
            --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Define entrypoint script to execute pre-flight checks and migrations
ENTRYPOINT ["/code/entrypoint.sh"]