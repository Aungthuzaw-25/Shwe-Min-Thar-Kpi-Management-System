"""Real-user measurement (RUM) beacon endpoint.

One endpoint, ``POST /api/v1/system/rum``, that receives client timing
beacons (Performance API navigation + paint timings) and writes them to
the server log — log-only, no database table, no migration.

Why unauthenticated: the beacon is sent with ``navigator.sendBeacon``,
which cannot set custom headers — so it can carry neither a Bearer
token nor the ``X-CSRF-Token`` header. Cookie auth would therefore be
rejected by ``CSRFMiddleware`` (cookie present, no bearer, no CSRF
headers). A ``fetch(keepalive)`` beacon could carry the CSRF header
(``static/js/csrf.js``), but it would cost a full ``get_current_user``
database trip per page load and would fail exactly when the session is
expired — the case timing data is most interesting for. The payload
carries no identity and the endpoint changes no state, so CSRF
protection is meaningless here (an attacker can already POST to it
directly); the path is listed in the CSRF exemption set and in the
dashboard-cache invalidation exclusion for exactly that reason.
Rate limiting + strict validation are the real defenses.

Privacy: the accepted fields are timing numbers, a route template
(never a concrete URL — no IDs, no query string), a coarse connection
bucket and a navigation type. No user agent, no user id, no IP address
is ever written to the RUM log line (the IP is only the rate-limit
key, and the generic access log's client IP is pre-existing behaviour
for every endpoint).
"""

import logging
from typing import Literal

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["System Settings & Logs"])

# A beacon is a few hundred bytes of JSON; 4 KiB is generous and stops
# a single request from carrying a junk payload.
MAX_BEACON_BYTES = 4096

# One beacon per page load. 60/minute per client IP is generous for a
# single user and still bounds a flood; a 429 on a fire-and-forget
# beacon only loses one sample, never a page.
RUM_RATE_LIMIT = "60/minute"


def _rum_rate_limit_key(request: Request) -> str:
    """Rate-limit key: first X-Forwarded-For entry, else remote address.

    Behind the nginx proxy every client arrives from the proxy's own
    address, so plain ``get_remote_address`` would make the limit
    global instead of per client. The proxy (local nginx / Railway)
    sets X-Forwarded-For. Caveat: a client can spoof the header to
    rotate buckets — acceptable for a log-only endpoint that does no
    database work; validation + the size cap still bound the damage.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:64]
    return get_remote_address(request)


limiter = Limiter(key_func=_rum_rate_limit_key)

# Timings come from the Performance API in milliseconds. The cap is
# 10 minutes — far above any real page load, far below any junk value.
MAX_TIMING_MS = 600_000


class RumBeacon(BaseModel):
    """One client timing beacon. Unknown fields are dropped."""

    model_config = ConfigDict(extra="ignore")

    # Route template only, e.g. "/superadmin/dashboard" — never the
    # concrete URL, so no IDs or query strings can arrive here.
    route: str = Field(pattern=r"^/[A-Za-z0-9/_-]{1,127}$")

    @field_validator("route")
    @classmethod
    def _reject_uuid_segments(cls, value: str) -> str:
        """A UUID-shaped segment is a concrete ID, not a template.

        The no-IDs guarantee is a Frontend contract (send the
        registered route template), and this is the server-side
        backstop for the most likely leak — the app's IDs are UUIDs.
        """
        uuid_segment = re.compile(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
        )
        if any(uuid_segment.match(part) for part in value.split("/")):
            raise ValueError("route must be a template, not a URL with an ID")
        return value
    ttfb_ms: float | None = Field(default=None, ge=0, le=MAX_TIMING_MS)
    dom_content_loaded_ms: float | None = Field(default=None, ge=0, le=MAX_TIMING_MS)
    load_ms: float | None = Field(default=None, ge=0, le=MAX_TIMING_MS)
    first_paint_ms: float | None = Field(default=None, ge=0, le=MAX_TIMING_MS)
    first_contentful_paint_ms: float | None = Field(default=None, ge=0, le=MAX_TIMING_MS)
    largest_contentful_paint_ms: float | None = Field(default=None, ge=0, le=MAX_TIMING_MS)
    # Cumulative layout shift, unitless 0-1.
    cls: float | None = Field(default=None, ge=0, le=1)
    # navigator.connection.effectiveType — coarse network bucket.
    connection: Literal["slow-2g", "2g", "3g", "4g"] | None = None
    # PerformanceNavigationTiming.type.
    navigation_type: Literal["navigate", "reload", "back_forward", "prerender"] | None = None


async def _check_beacon_size(request: Request) -> None:
    """Reject oversized beacons before FastAPI parses the body.

    This runs as a dependency, so it executes before the body
    parameter is solved. sendBeacon always sends Content-Length; a
    chunked request without one still hits the field-level caps in
    RumBeacon (route length, timing ranges).
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            size = int(declared)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length.") from None
        if size > MAX_BEACON_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Beacon payload exceeds {MAX_BEACON_BYTES} bytes.",
            )


def _format_beacon(beacon: RumBeacon) -> str:
    """One structured log line; only fields the client sent."""
    parts = [f"route={beacon.route}"]
    for name in (
        "ttfb_ms",
        "dom_content_loaded_ms",
        "load_ms",
        "first_paint_ms",
        "first_contentful_paint_ms",
        "largest_contentful_paint_ms",
        "cls",
        "connection",
        "navigation_type",
    ):
        value = getattr(beacon, name)
        if value is not None:
            parts.append(f"{name}={value}")
    return "rum " + " ".join(parts)


@router.post("/rum", response_class=JSONResponse)
@limiter.limit(RUM_RATE_LIMIT)
async def receive_rum_beacon(
    request: Request,
    beacon: RumBeacon,
    _size_checked: None = Depends(_check_beacon_size),
):
    """Accept one client timing beacon and log it. Always 200 on success.

    Contract for the Frontend beacon (sendBeacon):
    ``navigator.sendBeacon("/api/v1/system/rum",
    new Blob([JSON.stringify(payload)], {type: "application/json"}))``
    with the payload shaped exactly like RumBeacon. Unknown fields are
    dropped; invalid values answer 422; an oversized body answers 413;
    over the rate limit answers 429. Failures are safe to ignore — the
    beacon is fire-and-forget and the page never depends on it.
    """
    logger.info(_format_beacon(beacon))
    return {"status": "ok"}
