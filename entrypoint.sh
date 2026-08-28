#!/bin/bash
set -e

echo "🚀 1. Checking and Starting Database Migrations..."
alembic upgrade head
echo "✅ Migrations applied successfully!"

echo "🌱 2. Seeding Initial System Roles & Super Admin (if not exists)..."
python -m app.database.seed
echo "✅ Database seeded successfully!"

echo "🔥 3. Starting Production Gunicorn/Uvicorn Server..."
exec gunicorn app.main:app \
    --workers 4 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --timeout 120 \
    --keep-alive 5