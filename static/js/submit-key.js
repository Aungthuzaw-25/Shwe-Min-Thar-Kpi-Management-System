// Idempotency-Key helper for form submits (Backend contract:
// feat/submit-idempotency).
//
// One key per submit INTENT: a key is created when a form/dialog
// opens or on the first submit attempt, REUSED on every retry of
// that same intent (so a retry after a network drop cannot create a
// duplicate), and cleared after success so the next intent gets a
// fresh key. Keys also survive a page reload via sessionStorage —
// reloading mid-submit must not mint a new intent.
//
// Server responses: a replayed duplicate returns the original
// response with `Idempotency-Replayed: true` — callers treat it as
// success (see outcome()). 409 = the first attempt is still being
// processed (keep the key, ask the user to wait/check). 422 = the
// key was already used with a DIFFERENT body (the key is poisoned:
// clear it and explain).
(function (global) {
  "use strict";

  var SS_PREFIX = "kpi-submit-key:";
  var memory = {};

  function newKey() {
    if (global.crypto && typeof global.crypto.randomUUID === "function") {
      return global.crypto.randomUUID();
    }
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      var v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function session() {
    try {
      return global.sessionStorage || null;
    } catch (error) {
      return null;
    }
  }

  function get(scope) {
    if (memory[scope]) return memory[scope];
    var ss = session();
    var saved = null;
    try {
      saved = ss ? ss.getItem(SS_PREFIX + scope) : null;
    } catch (error) {
      saved = null;
    }
    if (saved) {
      memory[scope] = saved;
      return saved;
    }
    var key = newKey();
    memory[scope] = key;
    try {
      if (ss) ss.setItem(SS_PREFIX + scope, key);
    } catch (error) {
      // Memory alone still covers in-page retries.
    }
    return key;
  }

  function clear(scope) {
    delete memory[scope];
    var ss = session();
    try {
      if (ss) ss.removeItem(SS_PREFIX + scope);
    } catch (error) {
      // Nothing else to do — memory is already cleared.
    }
  }

  function headers(scope, base) {
    var merged = {};
    var source = base || {};
    Object.keys(source).forEach(function (name) {
      merged[name] = source[name];
    });
    merged["Idempotency-Key"] = get(scope);
    return merged;
  }

  // Classify a submit response for honest UI states:
  // 'success' | 'replayed' (also success) | 'in-progress' (409) |
  // 'key-conflict' (422) | 'unknown' (no response — network drop:
  // the submit may or may not have landed; never claim failure).
  function outcome(response) {
    if (!response) return "unknown";
    if (response.status === 409) return "in-progress";
    if (response.status === 422) return "key-conflict";
    if (response.ok) {
      var replayed =
        response.headers &&
        typeof response.headers.get === "function" &&
        response.headers.get("Idempotency-Replayed") === "true";
      return replayed ? "replayed" : "success";
    }
    return "error";
  }

  var api = { get: get, clear: clear, headers: headers, outcome: outcome, _newKey: newKey };
  global.SubmitKey = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
