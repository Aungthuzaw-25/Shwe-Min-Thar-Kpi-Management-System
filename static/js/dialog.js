// ONE shared behavior controller for every custom (form) modal in the
// app — the counterpart to the appConfirm/appPrompt/appDialog family in
// notifications.js, which already owns its own focus/Escape/backdrop.
//
// The controller does NOT show, hide, or restyle a modal, and it never
// submits anything: each modal's existing open/close functions stay in
// charge of visibility and of every API call. Wiring is two lines:
//   open:  window.SharedDialog && window.SharedDialog.open(modalEl, closeFn);
//   close: window.SharedDialog && window.SharedDialog.close(modalEl);
// What the controller adds, uniformly:
//   - backdrop click (a click whose target IS the overlay) calls
//     onCancel — it can never confirm or submit;
//   - Escape calls onCancel — likewise never a confirm/submit;
//   - Tab is trapped inside the topmost open modal;
//   - focus moves into the modal on open (first field, else first
//     button — so a confirm-like modal lands on Cancel, never Confirm)
//     and returns to the trigger on close.
// Nested modals stack: Escape and the Tab trap always apply to the
// topmost entry only. closeAll() closes the whole stack top-first.
(function () {
  var stack = [];

  function focusables(el) {
    var list = el.querySelectorAll
      ? el.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')
      : [];
    var out = [];
    for (var i = 0; i < list.length; i++) {
      if (list[i].getAttribute && list[i].getAttribute('aria-hidden') !== 'true') out.push(list[i]);
    }
    return out;
  }

  function open(el, onCancel, initialFocus) {
    if (!el || typeof onCancel !== 'function') return null;
    for (var i = 0; i < stack.length; i++) {
      if (stack[i].el === el) return stack[i];
    }
    var entry = {
      el: el,
      onCancel: onCancel,
      previouslyFocused: document.activeElement,
      clickHandler: function (event) {
        if (event.target === el) onCancel();
      },
    };
    entry.keyHandler = function (event) {
      if (stack.length === 0 || stack[stack.length - 1] !== entry) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        onCancel();
        return;
      }
      if (event.key !== 'Tab') return;
      var items = focusables(el);
      if (items.length === 0) {
        event.preventDefault();
        if (el.focus) el.focus();
        return;
      }
      var first = items[0];
      var last = items[items.length - 1];
      if (event.shiftKey && (document.activeElement === first || !el.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !el.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    };
    el.addEventListener('click', entry.clickHandler);
    document.addEventListener('keydown', entry.keyHandler, true);
    stack.push(entry);
    var target = initialFocus || null;
    if (!target) {
      var items = focusables(el);
      for (var j = 0; j < items.length; j++) {
        var tag = (items[j].tagName || '').toUpperCase();
        if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') { target = items[j]; break; }
      }
      if (!target && items.length) target = items[0];
    }
    if (target && target.focus) target.focus();
    return entry;
  }

  function close(el) {
    var idx = -1;
    for (var i = 0; i < stack.length; i++) {
      if (stack[i].el === el) { idx = i; break; }
    }
    if (idx === -1) return;
    var entry = stack.splice(idx, 1)[0];
    el.removeEventListener('click', entry.clickHandler);
    document.removeEventListener('keydown', entry.keyHandler, true);
    var prev = entry.previouslyFocused;
    if (prev && prev.focus && prev.isConnected) prev.focus();
  }

  function closeAll() {
    while (stack.length) close(stack[stack.length - 1].el);
  }

  window.SharedDialog = { open: open, close: close, closeAll: closeAll };
})();
