// Real-user measurement beacon (web-perf-gaps item 1).
//
// Sends one small timing payload per page load to
// POST /api/v1/system/rum (Backend feat/rum-endpoint, log-only —
// no table, no user id, no IP/UA stored server-side).
//
// What is sent (and nothing else — no PII):
//   route                    the page's route template (location.pathname
//                            with id-like segments replaced by "id" —
//                            never a raw id, never a query string or
//                            fragment). The replacement is the bare word
//                            "id" (no colon): the endpoint's route
//                            pattern ^/[A-Za-z0-9/_-]{1,127}$ rejects
//                            colons, so ":id" would come back 422 and
//                            the page's timing would never be recorded.
//   ttfb_ms                  navigation.responseStart
//   dom_content_loaded_ms    navigation.domContentLoadedEventEnd
//   load_ms                  navigation.loadEventEnd
//   first_paint_ms           first-paint entry
//   first_contentful_paint_ms  first-contentful-paint entry
//   largest_contentful_paint_ms  largest-contentful-paint start time
//   cls                      accumulated layout-shift score (0-1)
//   connection               navigator.connection.effectiveType, when present
//   navigation_type          navigate / reload / back_forward / prerender
//
// How it stays out of the way: the payload goes with
// navigator.sendBeacon as a JSON Blob (fire-and-forget, never blocks
// the page or a navigation), on every page load — no sampling, the
// server already rate-limits (60/min per IP) and the log is one line
// per load. Every step is guarded — if any API is missing or
// anything throws, the page is unaffected and nothing is sent.
//
// The endpoint drops unknown fields, 422s malformed values and 429s
// over 60/min per IP, so each field is range-checked here before
// sending (out-of-range fields are omitted, not clamped to a lie).
// The Server-Timing header on dashboard API responses has no field
// in the endpoint's payload, so it is deliberately not sent.
(function () {
  "use strict";

  var RUM_ENDPOINT = "/api/v1/system/rum";
  // LCP can still update right after load; wait this long after the
  // load event before sending so the number has settled. pagehide
  // sends whatever we have sooner if the user leaves first.
  var RUM_SEND_DELAY_MS = 1500;
  var MAX_MS = 600000;

  // --- pure helpers (unit-tested in web/tests/rumBeacon.test.js) ---

  function isIdSegment(segment) {
    // A path segment is an id when it is all digits, a UUID, or a
    // long hex token (session ids, hashes). Anything else is part of
    // the route template and is kept verbatim.
    return (
      /^\d+$/.test(segment) ||
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(segment) ||
      /^[0-9a-f]{16,}$/i.test(segment)
    );
  }

  function sanitizeRoute(pathname) {
    var route = String(pathname || "/")
      .split("?")[0]
      .split("#")[0]
      .split("/")
      .filter(function (part) { return part.length > 0; })
      .map(function (part) {
        try {
          part = decodeURIComponent(part);
        } catch (e) {
          // An undecodable segment is not a readable route part.
          return "id";
        }
        return isIdSegment(part) ? "id" : part;
      })
      .join("/");
    route = "/" + route;
    // The endpoint caps the route at 128 chars; truncate rather
    // than send a value it would reject.
    return route.length > 128 ? route.slice(0, 128) : route;
  }

  function validMs(value) {
    return (
      typeof value === "number" &&
      isFinite(value) &&
      value >= 0 &&
      value <= MAX_MS
    );
  }

  function buildPayload(input) {
    var payload = { route: sanitizeRoute(input.route || "/") };
    var msFields = [
      "ttfb_ms",
      "dom_content_loaded_ms",
      "load_ms",
      "first_paint_ms",
      "first_contentful_paint_ms",
      "largest_contentful_paint_ms",
    ];
    for (var i = 0; i < msFields.length; i++) {
      var key = msFields[i];
      if (validMs(input[key])) payload[key] = Math.round(input[key]);
    }
    if (
      typeof input.cls === "number" &&
      isFinite(input.cls) &&
      input.cls >= 0 &&
      input.cls <= 1
    ) {
      payload.cls = input.cls;
    }
    if (
      input.connection === "slow-2g" ||
      input.connection === "2g" ||
      input.connection === "3g" ||
      input.connection === "4g"
    ) {
      payload.connection = input.connection;
    }
    if (
      input.navigation_type === "navigate" ||
      input.navigation_type === "reload" ||
      input.navigation_type === "back_forward" ||
      input.navigation_type === "prerender"
    ) {
      payload.navigation_type = input.navigation_type;
    }
    return payload;
  }

  function installBeacon() {
    try {
      if (
        typeof navigator === "undefined" ||
        typeof navigator.sendBeacon !== "function" ||
        typeof performance === "undefined"
      ) {
        return;
      }
      var state = { lcp: null, cls: 0, sent: false };

      if (typeof PerformanceObserver !== "undefined") {
        try {
          var lcpObserver = new PerformanceObserver(function (entries) {
            var list = entries.getEntries();
            for (var i = 0; i < list.length; i++) {
              var start = list[i].renderTime || list[i].startTime;
              if (typeof start === "number") state.lcp = start;
            }
          });
          lcpObserver.observe({ type: "largest-contentful-paint", buffered: true });
        } catch (e) {
          // LCP unavailable — the payload simply omits it.
        }
        try {
          var clsObserver = new PerformanceObserver(function (entries) {
            var list = entries.getEntries();
            for (var i = 0; i < list.length; i++) {
              if (!list[i].hadRecentInput) state.cls += list[i].value || 0;
            }
          });
          clsObserver.observe({ type: "layout-shift", buffered: true });
        } catch (e) {
          // CLS unavailable — the payload simply omits it.
        }
      }

      function send() {
        if (state.sent) return;
        state.sent = true;
        try {
          var nav = performance.getEntriesByType("navigation")[0];
          var paint = {};
          var paints = performance.getEntriesByType("paint");
          for (var i = 0; i < paints.length; i++) {
            paint[paints[i].name] = paints[i].startTime;
          }
          var connection =
            navigator.connection && navigator.connection.effectiveType;
          var payload = buildPayload({
            route: window.location.pathname,
            ttfb_ms: nav ? nav.responseStart : null,
            dom_content_loaded_ms: nav ? nav.domContentLoadedEventEnd : null,
            load_ms: nav ? nav.loadEventEnd : null,
            first_paint_ms: paint["first-paint"],
            first_contentful_paint_ms: paint["first-contentful-paint"],
            largest_contentful_paint_ms: state.lcp,
            cls: state.cls,
            connection: connection,
            navigation_type: nav ? nav.type : null,
          });
          // The endpoint wants application/json; a plain string would
          // arrive as text/plain, so send a JSON Blob.
          navigator.sendBeacon(
            RUM_ENDPOINT,
            new Blob([JSON.stringify(payload)], { type: "application/json" }),
          );
        } catch (e) {
          // Never let measurement break the page.
        }
      }

      var sendTimer = null;
      function sendSoon() {
        if (sendTimer === null) {
          sendTimer = window.setTimeout(send, RUM_SEND_DELAY_MS);
        }
      }
      if (document.readyState === "complete") {
        sendSoon();
      } else {
        window.addEventListener("load", sendSoon);
      }
      window.addEventListener("pagehide", send);
    } catch (e) {
      // Measurement must never break the page.
    }
  }

  // --- browser entry point (unit tests evaluate only the code above
  // this marker, with the IIFE wrapper stripped) ---
  if (typeof window !== "undefined" && typeof document !== "undefined") {
    installBeacon();
  }
})();
