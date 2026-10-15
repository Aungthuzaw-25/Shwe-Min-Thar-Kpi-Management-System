# Railway Performance Checklist

Use this checklist in Railway's dashboard. No production service or credential
was accessed during this profiling work.

## Service placement and private connectivity

- [ ] In the Railway project, open each web/API service and the PostgreSQL
  service. Record the configured region and compare it with users in Myanmar.
  Prefer Southeast Asia/Singapore when Railway offers it for every required
  service.
- [ ] Confirm the API and PostgreSQL service are deployed in the same region.
  Cross-region traffic adds network round trips to every database statement.
- [ ] In the Postgres service's **Connect** panel, identify the internal/private
  connection URL. Confirm the API's `DATABASE_URL` host is that private
  hostname, not a public proxy hostname or a URL marked for external access.
- [ ] Do not paste or share the full URL. To inspect only its host from a
  Railway shell attached to the API service, run:

  ```sh
  python -c 'import os; from urllib.parse import urlparse; print(urlparse(os.environ["DATABASE_URL"]).hostname)'
  ```

- [ ] Confirm API and Postgres are on a private network with no avoidable
  public-network hop. Keep TLS settings consistent with the configured
  Railway connection URL and application settings.

## Runtime availability and resources

- [ ] Check whether API replicas scale to zero or sleep when idle. Disable
  sleeping/serverless scale-to-zero for the interactive API if that option is
  enabled; cold starts otherwise affect the first request after inactivity.
- [ ] Keep at least one API replica warm. Verify it remains healthy and does
  not restart under normal use.
- [ ] Check CPU and memory graphs for throttling, saturation, restarts, or
  out-of-memory events during dashboard navigation and KPI imports.
- [ ] The repository starts scheduled jobs in a separate scheduler process in
  its Compose deployment. Confirm Railway runs exactly one intended scheduler
  instance and that CPU-heavy scheduler work is not competing with the web/API
  request process unexpectedly.
- [ ] Confirm the web/API replica count, scheduler count, and database
  connection limits are compatible. Do not add replicas without checking
  database pool capacity.

## Edge and browser delivery

- [ ] Confirm responses use HTTP/2 or HTTP/3 where supported, compression
  (gzip or Brotli) for text assets, and keep-alive connections.
- [ ] Check that browser DevTools shows static assets served from the same
  expected origin and that HTML/API responses are not incorrectly cached for
  authenticated users.
- [ ] Use Chrome DevTools with cache disabled and a Fast 4G profile to record
  navigation TTFB, FCP, LCP, and the LCP element. Inspect the critical path
  for render-blocking fonts, stylesheets, and scripts.
- [ ] Inspect request timing, not only overall page duration. Note whether
  browser time is waiting on the web service, API, font origin, or database.

## Measure database round-trip time safely

Open a Railway shell attached to the API service. Run a single query with a
fresh async engine created from the already-configured `DATABASE_URL`; do not
print or echo the URL:

```sh
python - <<'PY'
import asyncio
import time
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import settings

async def main():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=False)
    try:
        samples = []
        async with engine.connect() as connection:
            for _ in range(10):
                started = time.perf_counter()
                await connection.execute(text("SELECT 1"))
                samples.append((time.perf_counter() - started) * 1000)
        print("SELECT 1 RTT ms:", ", ".join(f"{sample:.1f}" for sample in samples))
    finally:
        await engine.dispose()

asyncio.run(main())
PY
```

Run this only as a diagnostic from the API container. It bypasses the
application pool's `pool_pre_ping` solely for the ten direct timing samples;
it does not alter service settings or production data. Compare the median and
tail to the API's request timings. A result consistently above roughly 5 ms
can make sequential database round trips a significant part of request time.

## Record and share findings

- [ ] Record service regions, private/public URL classification (host only),
  replica/sleep settings, resource utilization, and ten `SELECT 1` timings.
- [ ] Capture sanitized DevTools timing summaries and request paths. Remove
  cookies, authorization headers, query secrets, emails, and all credential
  material before sharing.
- [ ] Do not change migrations, indexes, caching, pool health checks, or auth
  behavior without reviewing the separate approval items in the performance
  report.
