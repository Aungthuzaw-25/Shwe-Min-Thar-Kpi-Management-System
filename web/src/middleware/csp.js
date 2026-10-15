import { randomBytes } from "node:crypto";

function storageOrigins(endpoint, isProduction) {
  let origins = "";
  if (endpoint) {
    let parsed;
    try {
      parsed = new URL(endpoint);
    } catch (error) {
      throw new TypeError("S3_ENDPOINT must be an absolute URL.", { cause: error });
    }
    origins += ` ${parsed.protocol}//${parsed.hostname}`;
    if (parsed.port) origins += `:${parsed.port}`;
  }
  if (!isProduction) origins += " http://localhost:9000 http://minio:9000";
  return origins;
}

export function rewriteHtml(html, nonce) {
  return html
    .replaceAll(/<script(?![^>]*\bsrc=)([^>]*)>/gi, `<script nonce="${nonce}"$1>`)
    .replace(/<style([^>]*)>/gi, `<style nonce="${nonce}"$1>`)
    .replace(
      /\s(on(?:click|submit|change|keydown))\s*=\s*(["'])((?:(?!\2).)*)\2/gi,
      (_match, handler, _quote, value) => ` data-csp-${handler}="${value.replace(/"/g, "&quot;")}"`,
    );
}

export function cspMiddleware({
  env = process.env.ENV || "development",
  s3Endpoint = process.env.S3_ENDPOINT,
} = {}) {
  const isProduction = env.toLowerCase() === "production";
  const additionalStorageOrigins = storageOrigins(s3Endpoint, isProduction);

  return function applySecurityHeaders(request, response, next) {
    const nonce = randomBytes(16).toString("base64url");
    request.cspNonce = nonce;

    response.set({
      "X-Content-Type-Options": "nosniff",
      "X-XSS-Protection": "1; mode=block",
      "X-Frame-Options": "DENY",
      "Referrer-Policy": "strict-origin-when-cross-origin",
      "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
      "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
      "Content-Security-Policy": [
        "default-src 'self';",
        `script-src 'self' 'nonce-${nonce}';`,
        `style-src 'self' 'nonce-${nonce}' https://fonts.googleapis.com;`,
        "font-src 'self' https://fonts.gstatic.com;",
        `img-src 'self' data: blob:${additionalStorageOrigins};`,
        `connect-src 'self'${additionalStorageOrigins};`,
        "frame-ancestors 'none';",
      ].join(" "),
    });

    const render = response.render.bind(response);
    response.render = function renderWithCsp(view, locals, callback) {
      let context = locals;
      let done = callback;
      if (typeof context === "function") {
        done = context;
        context = {};
      }
      return render(view, context || {}, (error, html) => {
        if (error) {
          if (done) return done(error);
          return next(error);
        }
        const rewritten = rewriteHtml(html, nonce);
        if (done) return done(null, rewritten);
        response.type("html").send(rewritten);
      });
    };

    next();
  };
}
