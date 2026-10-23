// Shared page-data fetch helper.
//
// A plain fetch() has no timeout: on a stalled connection the page's
// loading state spins forever. fetchWithTimeout adds a hard ceiling
// (15s — the same timeout the web tier uses for its own backend
// calls) so a stalled load fails and the page can show its error
// state with a Retry button instead.
//
// Use for page-data loads (GETs). Never for uploads or long-running
// actions, which legitimately take longer.
(function () {
  var DEFAULT_TIMEOUT_MS = 15000;

  function timeoutSignal(ms) {
    return typeof AbortSignal !== "undefined" && AbortSignal.timeout
      ? AbortSignal.timeout(ms)
      : undefined;
  }

  function combineSignals(callerSignal, ms) {
    var timeout = timeoutSignal(ms);
    if (!timeout) return callerSignal;
    if (!callerSignal) return timeout;
    // Keep the caller's cancellation (e.g. a dashboard superseding a
    // stale load) and add the timeout alongside it. When the timeout
    // fires, the fetch rejects with a TimeoutError, so callers can
    // tell a stall apart from their own abort.
    return typeof AbortSignal.any === "function"
      ? AbortSignal.any([callerSignal, timeout])
      : callerSignal;
  }

  window.fetchWithTimeout = function (url, options, timeoutMs) {
    var ms = typeof timeoutMs === "number" ? timeoutMs : DEFAULT_TIMEOUT_MS;
    var merged = Object.assign({}, options || {});
    var signal = combineSignals(merged.signal, ms);
    if (signal) merged.signal = signal;
    return fetch(url, merged);
  };
})();
