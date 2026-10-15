import path from "node:path";
import { fileURLToPath } from "node:url";
import express from "express";
import nunjucks from "nunjucks";
import { createBackendClient } from "./lib/backendClient.js";
import { registerFilters } from "./lib/nunjucks_filters.js";
import { cspMiddleware } from "./middleware/csp.js";
import { registerActivityRoutes } from "./routes/activity.js";
import { registerAccountRoutes } from "./routes/account.js";
import { registerAuthRoutes } from "./routes/auth.js";
import { registerDashboardRoutes } from "./routes/dashboard.js";

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

  app.get("/", (_request, response) => {
    response.redirect(307, "/auth/login");
  });
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
