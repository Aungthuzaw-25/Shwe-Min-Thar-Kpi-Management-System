# Inter font files (self-hosted)

- **What:** Inter variable font, one woff2 file per unicode subset
  (latin, latin-ext, cyrillic, cyrillic-ext, greek, greek-ext, vietnamese).
- **Source:** the files Google Fonts serves for Inter v20
  (`fonts.gstatic.com/s/inter/v20/…`), downloaded 2026-10-30 and stored here
  so the login page and layouts make zero requests to Google Fonts.
- **Licence:** SIL Open Font License 1.1. Inter is by Rasmus Andersson.
  Full licence text: https://github.com/rsms/inter/blob/master/LICENSE.txt
  The full licence text is also vendored in this folder as `OFL.txt`.
- **Used by:** `static/css/inter-fonts.css`, linked from
  `templates/auth/login.njk`, `templates/auth/change_password.njk`,
  `templates/layouts/base.njk`, `templates/layouts/employee_base.njk`.
  No other page loads Inter from anywhere else.
