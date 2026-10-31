# qrcode-generator.js — licence note

Vendored file: `static/vendor/qrcode-generator.js`

- Source: npm package `qrcode-generator`, version 2.0.4
- File vendored: `dist/qrcode.js` — copied verbatim, unmodified
- Licence: MIT (see the MIT header comment at the top of the vendored file,
  and the package's `license: "MIT"` field)
- Why vendored: the Super Admin Authenticator setup card renders its QR
  code locally in the browser from the `otpauth://` URL. A vendored copy
  keeps that rendering fully offline — the TOTP secret never leaves the
  browser to any QR service or external host.
- Used by: `static/js/sa-mfa-setup.js` (guarded: if `window.qrcode` is
  missing, the card falls back to manual-key entry only)
