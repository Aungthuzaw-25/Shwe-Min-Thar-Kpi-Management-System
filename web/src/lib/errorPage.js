// Friendly HTML error page for browser navigations.
//
// When the backend is down or a page load fails, a person following a
// link should see a readable page with a way to retry — not the raw
// JSON body the API-style error responses use. Non-navigation
// requests (fetch/XHR calls, API clients) keep their JSON errors.

export function isNavigationRequest(request) {
  if (!request || request.method !== "GET") return false;
  const accept = typeof request.get === "function"
    ? request.get("accept") || ""
    : "";
  return accept.includes("text/html");
}

export function sendErrorPage(response, status) {
  response.status(status).type("html").send(
    "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
    + "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    + "<title>Temporarily unavailable</title>"
    + "<main style=\"max-width:32rem;margin:12vh auto;padding:2rem;"
    + "font:16px/1.5 system-ui,sans-serif;color:#171717\">"
    + "<h1>Something went wrong on our side</h1>"
    + "<p>The application service is temporarily unavailable. "
    + "Please wait a moment and try again.</p>"
    + "<p style=\"margin-top:1.5rem\">"
    + "<a href=\"\" style=\"display:inline-block;padding:.6rem 1.2rem;"
    + "border:1px solid #94a3b8;border-radius:.6rem;color:#171717;"
    + "text-decoration:none;font-weight:600\">Try again</a> "
    + "<a href=\"/dashboard\">Return to your dashboard</a></p></main></html>",
  );
  return true;
}
