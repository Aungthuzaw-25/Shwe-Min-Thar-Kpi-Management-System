"""Run independent dashboard queries concurrently on separate sessions.

Why separate sessions: one AsyncSession serves one database operation at
a time, so asyncio.gather over a single session would serialize (or fail)
instead of parallelizing. Each job therefore gets its own session from the
request session's engine, and results come back in job order.

Read-only dashboard builders only: no writes, no shared transaction, so
each session simply reads committed state. The semaphore caps dashboard
parallel sessions per worker at 12: the pool is 10 + overflow 10 per
worker, and a dashboard page load also holds its route sessions, so an
uncapped fan-out could starve the pool when two dashboards load at once.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

T = TypeVar("T")

# Dashboard-parallel sessions per worker (pool is 10 + overflow 10).
_MAX_PARALLEL_SESSIONS = 12

# One semaphore per event loop, not one module-global. An asyncio
# Semaphore binds to the first loop that ever WAITS on it (its fast
# path skips the loop check, so a bind only shows up once 12 permits
# are held and a job has to wait) — a module-global semaphore then
# raises "bound to a different event loop" on any second loop in the
# same process, which is exactly what a test suite with one TestClient
# per test does. Production runs one loop per worker, so one semaphore
# per loop behaves identically there; tests get a fresh 12-permit
# semaphore per loop instead of a timing-dependent RuntimeError.
_semaphores: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}


def _get_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    semaphore = _semaphores.get(loop)
    if semaphore is None:
        for old in [item for item in _semaphores if item.is_closed()]:
            del _semaphores[old]
        semaphore = asyncio.Semaphore(_MAX_PARALLEL_SESSIONS)
        _semaphores[loop] = semaphore
    return semaphore


async def gather_on_sessions(
    db: AsyncSession,
    jobs: Sequence[Callable[[AsyncSession], Awaitable[T]]],
) -> list[T]:
    """Run each job(session) concurrently and return results in order.

    Sessions are derived from the request session's own engine, so test
    sessions bound to a test engine stay on that engine and production
    sessions stay on the production pool. If the session has no engine
    (never happens for request sessions), jobs run sequentially on the
    given session rather than failing.
    """
    engine = getattr(db, "bind", None)
    if engine is None or not jobs:
        # No engine (e.g. a stub session in tests): stay correct by
        # running the jobs sequentially on the given session.
        return [await job(db) for job in jobs]

    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async def run(job: Callable[[AsyncSession], Awaitable[T]]) -> T:
        async with _get_semaphore():
            async with factory() as session:
                return await job(session)

    return list(await asyncio.gather(*(run(job) for job in jobs)))
