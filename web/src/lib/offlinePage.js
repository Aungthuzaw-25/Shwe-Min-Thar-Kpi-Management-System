// Offline fallback page, served by the web tier at GET /offline and
// precached by the service worker. It is shown when a navigation fails
// offline and no cached copy of that page exists. Static HTML on
// purpose — it must render with no network, no API and no session.

export function sendOfflinePage(response) {
  response.status(200).type("html").send(
    "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
    + "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    + "<title>Offline — Shwe Min Thar KPI</title>"
    + "<main style=\"max-width:32rem;margin:12vh auto;padding:2rem;"
    + "font:16px/1.6 system-ui,sans-serif;color:#1c2b3a\">"
    + "<h1 style=\"font-size:1.4rem;margin:0 0 .75rem\">You are offline</h1>"
    + "<p>Internet connection is not available right now. "
    + "Dashboard pages and data you already viewed on this device "
    + "remain available while you are offline — they are labelled "
    + "with the time they were last updated.</p>"
    + "<p>This page was not viewed before, so there is no saved copy "
    + "of it yet. Reconnect and try again.</p>"
    + "<p style=\"margin-top:1.5rem\">"
    + "<a href=\"\" style=\"display:inline-block;padding:.6rem 1.2rem;"
    + "background:#3d5a80;border-radius:.6rem;color:#fff;"
    + "text-decoration:none;font-weight:600\">Try again</a> "
    + "<a href=\"/dashboard\" style=\"color:#3d5a80;font-weight:600\">"
    + "Go to your dashboard</a></p></main></html>",
  );
}
