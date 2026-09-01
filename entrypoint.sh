#!/usr/bin/env bash
set -euo pipefail

# ==========================================
# Dynamic Port & Worker Configuration
# ==========================================
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"
WORKERS="${GUNICORN_WORKERS:-2}"
TIMEOUT="${GUNICORN_TIMEOUT:-120}"
LOG_LEVEL="${LOG_LEVEL:-info}"

echo "=================================================="
echo "🚀 Initializing Application Deployment on Railway"
echo "⚙️  Binding to ${HOST}:${PORT} with ${WORKERS} worker(s)"
echo "=================================================="

# ==========================================
# 1. Pre-Flight Database Connectivity Check
# ==========================================
echo "⏳ Step 1: Checking PostgreSQL Connectivity..."

python - << 'EOF'
import os
import sys
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("❌ ERROR: DATABASE_URL environment variable is not defined.")
    sys.exit(1)

# Ensure the protocol is compliant with asyncpg. For Neon, sslmode is not accepted by
# asyncpg; we need to enable SSL via connect_args instead.
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif db_url.startswith("postgresql://") and not db_url.startswith("postgresql+asyncpg://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

# Remove unsupported asyncpg query parameters before connection.
for token in ("?sslmode=require", "&sslmode=require", "?channel_binding=require", "&channel_binding=require"):
    db_url = db_url.replace(token, "")

clean_url = db_url
connect_args = {"ssl": True} if clean_url.startswith("postgresql+asyncpg://") else {}

async def verify_connection():
    max_retries = 5
    retry_interval = 2
    for attempt in range(1, max_retries + 1):
        try:
            engine = create_async_engine(clean_url, pool_pre_ping=True, connect_args=connect_args)
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            await engine.dispose()
            print("✅ Database is reachable and accepting connections.")
            return True
        except Exception as e:
            print(f"⚠️  Database connection attempt {attempt}/{max_retries} failed: {e}")
            if attempt < max_retries:
                await asyncio.sleep(retry_interval)
            else:
                print("❌ ERROR: Could not establish database connection within timeout.")
                sys.exit(1)

asyncio.run(verify_connection())
EOF

# ==========================================
# 2. Database Migrations
# ==========================================
echo "🚀 Step 2: Running Database Schema Migrations..."
alembic upgrade head
echo "✅ Migrations completed successfully."

# ==========================================
# 3. Server Startup
# ==========================================
echo "🔥 Step 3: Starting Gunicorn / Uvicorn Server..."
exec gunicorn app.main:app \
    --workers "$WORKERS" \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind "${HOST}:${PORT}" \
    --timeout "$TIMEOUT" \
    --keep-alive 5 \
    --log-level "$LOG_LEVEL" \
    --access-logfile - \
    --error-logfile - \
    --forwarded-allow-ips "*" \
    --proxy-allow-from "*"