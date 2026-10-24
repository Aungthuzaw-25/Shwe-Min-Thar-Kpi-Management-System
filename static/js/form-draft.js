// Form draft autosave (draft-safety v1).
//
// While a person types into a covered form, the values are saved to
// localStorage on this device, sealed per user (key includes the
// signed-in user's id from the login record) and per form intent.
// Reopening the form offers "Restore / Discard" instead of silently
// overwriting anything. A successful submit clears the draft, and
// logout clears every draft on the device (see layouts).
//
// Hard rules:
//   * Password fields are NEVER saved. File inputs are NEVER saved
//     either — browsers cannot re-fill them; only the chosen file
//     NAME is remembered so the restore prompt can honestly say
//     "please choose this file again".
//   * Nothing here auto-submits or auto-restores: the user chooses.
(function (global) {
  "use strict";

  var PREFIX = "kpi-draft:";
  var DEBOUNCE_MS = 400;
  var registry = {};

  function ownerId() {
    try {
      var raw = global.localStorage.getItem("user");
      if (raw) {
        var user = JSON.parse(raw);
        var id = user && (user.user_id || user.id || user.email);
        if (id) return String(id);
      }
    } catch (error) {
      // Fall through to the anonymous bucket; logout still clears all.
    }
    return "anon";
  }

  function storageKey(formKey) {
    return PREFIX + ownerId() + ":" + formKey;
  }

  function fieldKey(el) {
    return el.id || el.name || "";
  }

  function isDraftable(el) {
    if (!el || el.disabled || el.readOnly) return false;
    var type = (el.type || "").toLowerCase();
    if (
      type === "password" ||
      type === "hidden" ||
      type === "submit" ||
      type === "button" ||
      type === "reset"
    ) {
      return false;
    }
    if (el.hasAttribute && el.hasAttribute("data-no-draft")) return false;
    return true;
  }

  function collect(container) {
    var fields = {};
    var files = {};
    var elements = container.querySelectorAll("input, select, textarea");
    Array.prototype.forEach.call(elements, function (el) {
      var key = fieldKey(el);
      if (!key) return;
      var type = (el.type || "").toLowerCase();
      if (type === "file") {
        if (el.files && el.files.length) files[key] = el.files[0].name;
        return;
      }
      if (!isDraftable(el)) return;
      if (type === "checkbox") {
        fields[key] = !!el.checked;
      } else if (type === "radio") {
        if (el.checked) fields[key] = el.value;
      } else {
        fields[key] = el.value;
      }
    });
    return { fields: fields, files: files, savedAt: Date.now() };
  }

  function apply(container, draft) {
    Object.keys(draft.fields || {}).forEach(function (key) {
      var escaped =
        typeof CSS !== "undefined" && CSS.escape
          ? CSS.escape(key)
          : key.replace(/[^a-zA-Z0-9_-]/g, "\\$&");
      var el =
        container.querySelector("#" + escaped) ||
        container.querySelector('[name="' + key + '"]');
      if (!el) return;
      var type = (el.type || "").toLowerCase();
      if (type === "checkbox") {
        el.checked = !!draft.fields[key];
      } else if (type === "radio") {
        var group = container.querySelectorAll('[name="' + key + '"]');
        Array.prototype.forEach.call(group, function (radio) {
          radio.checked = radio.value === draft.fields[key];
        });
      } else {
        el.value = draft.fields[key];
      }
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }

  function save(formKey, container) {
    try {
      var data = collect(container);
      if (
        !Object.keys(data.fields).length &&
        !Object.keys(data.files).length
      ) {
        clear(formKey);
        return;
      }
      global.localStorage.setItem(storageKey(formKey), JSON.stringify(data));
    } catch (error) {
      // Storage full/blocked: drafts are best-effort, never break typing.
    }
  }

  function load(formKey) {
    try {
      var raw = global.localStorage.getItem(storageKey(formKey));
      return raw ? JSON.parse(raw) : null;
    } catch (error) {
      return null;
    }
  }

  function clear(formKey) {
    try {
      global.localStorage.removeItem(storageKey(formKey));
    } catch (error) {
      // Nothing to clear then.
    }
  }

  function clearAll() {
    try {
      var doomed = [];
      for (var i = 0; i < global.localStorage.length; i++) {
        var key = global.localStorage.key(i);
        if (key && key.indexOf(PREFIX) === 0) doomed.push(key);
      }
      doomed.forEach(function (key) {
        global.localStorage.removeItem(key);
      });
    } catch (error) {
      // Logout proceeds regardless.
    }
  }

  // Attach autosave + the restore prompt to a form container.
  // options: { key: string | () => string, label?: string }
  // Returns a handle with refresh() (call when the form is opened —
  // the draft may belong to a different create/edit intent by then)
  // and clear() (call after a successful submit).
  function attach(container, options) {
    if (!container || !options || !options.key) return null;
    var keyOf =
      typeof options.key === "function" ? options.key : function () { return options.key; };
    var bar = null;
    var timer = null;

    function ensureBar() {
      if (bar) return bar;
      bar = document.createElement("div");
      bar.className = "kpi-draft-bar";
      bar.setAttribute("role", "status");
      bar.hidden = true;
      bar.innerHTML =
        '<span class="kpi-draft-text"></span>' +
        '<span class="kpi-draft-actions">' +
        '<button type="button" class="kpi-draft-restore">Restore draft</button>' +
        '<button type="button" class="kpi-draft-discard">Discard</button>' +
        "</span>" +
        '<span class="kpi-draft-files"></span>';
      container.insertBefore(bar, container.firstChild);
      bar.querySelector(".kpi-draft-restore").addEventListener("click", function () {
        var draft = load(keyOf());
        if (draft) apply(container, draft);
        bar.hidden = true;
      });
      bar.querySelector(".kpi-draft-discard").addEventListener("click", function () {
        clear(keyOf());
        bar.hidden = true;
      });
      return bar;
    }

    function refresh() {
      var draft = load(keyOf());
      var hasContent =
        draft &&
        (Object.keys(draft.fields || {}).length || Object.keys(draft.files || {}).length);
      if (!hasContent) {
        if (bar) bar.hidden = true;
        return;
      }
      var element = ensureBar();
      var when = draft.savedAt ? new Date(draft.savedAt) : null;
      element.querySelector(".kpi-draft-text").textContent =
        "You have an unsaved draft" +
        (when
          ? " from " +
            when.toLocaleString([], {
              day: "numeric",
              month: "short",
              hour: "numeric",
              minute: "2-digit",
            })
          : "") +
        ".";
      var fileNames = Object.keys(draft.files || {}).map(function (key) {
        return draft.files[key];
      });
      element.querySelector(".kpi-draft-files").textContent = fileNames.length
        ? "Please choose this file again: " + fileNames.join(", ")
        : "";
      element.hidden = false;
    }

    function scheduleSave() {
      if (timer) clearTimeout(timer);
      timer = setTimeout(function () {
        save(keyOf(), container);
      }, DEBOUNCE_MS);
    }

    container.addEventListener("input", scheduleSave);
    container.addEventListener("change", scheduleSave);
    // Modals open long after page load: re-check for a draft when the
    // user first interacts with the form, so the prompt is current
    // for this open (and this create/edit intent).
    container.addEventListener("focusin", refresh);

    var handle = {
      refresh: refresh,
      clear: function () {
        clear(keyOf());
        if (bar) bar.hidden = true;
      },
    };
    registry[keyOf()] = handle;
    refresh();
    return handle;
  }

  var api = {
    attach: attach,
    save: save,
    load: load,
    clear: clear,
    clearAll: clearAll,
    collect: collect,
    apply: apply,
    _ownerId: ownerId,
    _storageKey: storageKey,
  };
  global.FormDraft = api;
  global.kpiClearFormDrafts = clearAll;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
