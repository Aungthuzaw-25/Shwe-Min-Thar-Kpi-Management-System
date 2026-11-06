import path from "node:path";
import { fileURLToPath } from "node:url";
import express from "express";
import nunjucks from "nunjucks";
import { createAssetHelper } from "./lib/assets.js";
import { createBackendClient } from "./lib/backendClient.js";
import { isNavigationRequest, sendErrorPage } from "./lib/errorPage.js";
import { registerFilters } from "./lib/nunjucks_filters.js";
import { cspMiddleware } from "./middleware/csp.js";
import { registerActivityRoutes } from "./routes/activity.js";
import { registerAccountRoutes } from "./routes/account.js";
import { registerAuthRoutes } from "./routes/auth.js";
import { registerDashboardRoutes } from "./routes/dashboard.js";
import { registerOfflineRoutes } from "./routes/offline.js";

function sanitizeLogField(value) {
  return String(value).replaceAll(/[\r\n\t]/g, "_");
}

const sourceDirectory = path.dirname(fileURLToPath(import.meta.url));
const templatesDirectory = path.resolve(sourceDirectory, "../../templates");

export function createApp({
  backend = createBackendClient(),
  templatesPath = templatesDirectory,
  environment = process.env.ENV || "development",
  s3Endpoint = process.env.S3_ENDPOINT,
  // Phase 5b asset bundles: default OFF; set ASSET_BUNDLES=true to serve the
  // hashed bundles built by tools/build_asset_bundles.mjs. The flag-off path
  // renders the original individual links, byte-identical to before.
  assetBundles = process.env.ASSET_BUNDLES === "true",
  assetSpecPath,
  assetManifestPath,
} = {}) {
  const app = express();
  app.disable("x-powered-by");
  app.locals.backend = backend;
  app.use(cspMiddleware({ env: environment, s3Endpoint }));

  const nunjucksEnvironment = nunjucks.configure(templatesPath, {
    autoescape: true,
    express: app,
    noCache: environment.toLowerCase() !== "production",
    trimBlocks: false,
    lstripBlocks: false,
  });
  registerFilters(nunjucksEnvironment, nunjucks);
  const assetHelper = createAssetHelper({
    specPath: assetSpecPath,
    manifestPath: assetManifestPath,
  });
  nunjucksEnvironment.addGlobal("asset_bundles", assetBundles);
  nunjucksEnvironment.addGlobal("asset", (name) => assetHelper.asset(name));

  app.get("/", (_request, response) => {
    response.redirect(307, "/auth/login");
  });
  // Railway healthcheck (and the Docker HEALTHCHECK): a cheap 200 that
  // proves this process is up, with no API call and no session — the
  // API has its own /health, and a slow API must never mark this tier
  // unhealthy. The public entry still routes /health to the API (see
  // proxy/default.conf); this route answers only direct checks of the
  // web service itself.
  app.get("/health", (_request, response) => {
    response.status(200).type("application/json").json({ status: "ok" });
  });
  registerOfflineRoutes(app);
  registerAuthRoutes(app);
  registerAccountRoutes(app);
  registerDashboardRoutes(app);
  registerActivityRoutes(app);

  app.use((error, request, response, _next) => {
    console.error(JSON.stringify({
      level: "error",
      message: "Web request failed",
      method: sanitizeLogField(request.method),
      path: sanitizeLogField(request.path),
      error: sanitizeLogField(error instanceof Error ? error.message : error),
    }));
    if (response.headersSent) return;
    // A person following a link gets a readable error page with a
    // retry; API-style requests keep the JSON error.
    if (isNavigationRequest(request)) {
      sendErrorPage(response, 502);
      return;
    }
    response.status(502).json({ detail: "The application service is temporarily unavailable." });
  });

  return app;
}

const isMainModule = process.argv[1]
  && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMainModule) {
  const port = Number.parseInt(process.env.PORT || "3000", 10);
  createApp().listen(port, "0.0.0.0", () => {
    console.log(`Node UI listening on ${port}`);
  });
}
