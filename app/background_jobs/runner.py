"""Dedicated singleton process for scheduled jobs."""

import asyncio
import logging

from app.background_jobs.scheduler import start_scheduler

logger = logging.getLogger(__name__)


async def run():
    scheduler = start_scheduler()
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
