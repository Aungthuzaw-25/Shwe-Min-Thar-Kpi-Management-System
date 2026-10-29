// Reusable 6-box OTP input (shadcn Input OTP behavior, re-implemented
// in the project's own stack — no React, no new dependency).
//
//   const otp = window.OtpInput.create(container);
//   otp.getValue(); // "123456" ("" while incomplete)
//   otp.clear();    // empty every box
//   otp.focus();    // focus the first box
//
// Behavior: one digit per box, auto-advance on input, Backspace on an
// empty box moves to the previous box and clears it, paste fills from
// the focused box onward, digits only (anything else is stripped),
// each box labeled for screen readers, mobile numeric keyboard via
// inputmode. The component never submits anything itself: the hosting
// form reads getValue() when its own submit runs. First box carries
// autocomplete="one-time-code" so authenticator/SMS autofill can offer
// the code; the rest are autocomplete="off".
(function () {
  function digitsOnly(text) {
    return String(text == null ? '' : text).replace(/[^0-9]/g, '');
  }

  function create(container, options) {
    var opts = options || {};
    var length = opts.length || 6;
    var boxes = [];
    var doc = container.ownerDocument || document;

    for (var i = 0; i < length; i++) {
      var input = doc.createElement('input');
      input.type = 'text';
      input.className = 'otp-box';
      input.inputMode = 'numeric';
      input.maxLength = 1;
      input.autocomplete = i === 0 ? 'one-time-code' : 'off';
      input.setAttribute('aria-label', 'Digit ' + (i + 1) + ' of ' + length);
      input.setAttribute('pattern', '[0-9]*');
      container.appendChild(input);
      boxes.push(input);
    }

    function focusBox(index) {
      if (index < 0 || index >= boxes.length) return;
      boxes[index].focus();
    }

    function fillFrom(start, digits) {
      var placed = 0;
      for (var k = 0; k < digits.length && start + k < boxes.length; k++) {
        boxes[start + k].value = digits[k];
        placed++;
      }
      return placed;
    }

    boxes.forEach(function (box, index) {
      box.addEventListener('input', function () {
        var digits = digitsOnly(box.value);
        if (!digits) {
          box.value = '';
          return;
        }
        if (digits.length === 1) {
          box.value = digits;
          if (index + 1 < boxes.length) focusBox(index + 1);
        } else {
          // Autofill or multi-char input: distribute from this box.
          var placed = fillFrom(index, digits);
          focusBox(Math.min(index + placed, boxes.length - 1));
        }
        if (typeof opts.onChange === 'function') opts.onChange(getValue());
      });
      box.addEventListener('keydown', function (event) {
        if (event.key === 'Backspace') {
          event.preventDefault();
          if (box.value) {
            box.value = '';
          } else if (index > 0) {
            boxes[index - 1].value = '';
            focusBox(index - 1);
          }
          if (typeof opts.onChange === 'function') opts.onChange(getValue());
        } else if (event.key === 'Delete') {
          event.preventDefault();
          box.value = '';
          if (typeof opts.onChange === 'function') opts.onChange(getValue());
        } else if (event.key === 'ArrowLeft' && index > 0) {
          event.preventDefault();
          focusBox(index - 1);
        } else if (event.key === 'ArrowRight' && index + 1 < boxes.length) {
          event.preventDefault();
          focusBox(index + 1);
        }
      });
      box.addEventListener('paste', function (event) {
        event.preventDefault();
        var text = '';
        if (event.clipboardData && event.clipboardData.getData) {
          text = event.clipboardData.getData('text');
        }
        var digits = digitsOnly(text);
        if (!digits) return;
        var placed = fillFrom(index, digits);
        focusBox(Math.min(index + placed, boxes.length - 1));
        if (typeof opts.onChange === 'function') opts.onChange(getValue());
      });
      box.addEventListener('focus', function () {
        if (box.select) box.select();
      });
    });

    function getValue() {
      return boxes.map(function (box) { return box.value; }).join('');
    }

    return {
      boxes: boxes,
      getValue: getValue,
      clear: function () {
        boxes.forEach(function (box) { box.value = ''; });
      },
      focus: function () { focusBox(0); },
    };
  }

  window.OtpInput = { create: create };
})();
