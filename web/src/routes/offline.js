// Offline support routes (read-only offline v1).
//
// GET /sw.js     — the service worker, served from the web tier root
//                  so its scope is "/" (a worker under /static/ could
//                  never control an app page — the old bug). Sent with
//                  no-cache so a new worker version is picked up
//                  promptly; the worker itself versions its caches.
// GET /offline   — the offline fallback page the worker precaches.
//                  Public on purpose: it is a static notice with no
//                  data, and the worker must be able to cache it.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { sendOfflinePage } from "../lib/offlinePage.js";

const sourceDirectory = path.dirname(fileURLToPath(import.meta.url));
const serviceWorkerPath = path.resolve(sourceDirectory, "../service-worker.js");
const serviceWorkerSource = fs.readFileSync(serviceWorkerPath, "utf8");

export function registerOfflineRoutes(app) {
  app.get("/sw.js", (_request, response) => {
    response
      .status(200)
      .type("application/javascript")
      .set("Cache-Control", "no-cache")
      .send(serviceWorkerSource);
  });

  app.get("/offline", (_request, response) => {
    sendOfflinePage(response);
  });
}
