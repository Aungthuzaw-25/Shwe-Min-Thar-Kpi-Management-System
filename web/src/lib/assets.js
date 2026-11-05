import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Phase 5b asset() helper for the web tier (perf/lcp).
 *
 * `asset(name)` resolves one CSS bundle name (from static/css/bundles.json)
 * to the list of <link> hrefs the template should emit:
 *   - manifest hit  -> [ "/static/css/<name>.<hash>.css" ] (hashed, immutable)
 *   - manifest miss -> the bundle's original hrefs from bundles.json
 *                       (flag-on still renders correctly, just unbundled)
 *   - unknown name  -> [] plus one console warning (never a broken URL)
 *
 * Loading is lazy (first asset() call) and cached; a missing or malformed
 * manifest/spec never throws into a request, it only degrades to the
 * original hrefs. Paths default to the repo layout (web/src/lib/ ->
 * ../../../static) and are overridable via createApp options for tests.
 */
const sourceDirectory = path.dirname(fileURLToPath(import.meta.url));

export function createAssetHelper({ specPath, manifestPath } = {}) {
  const resolvedSpec = specPath || path.resolve(sourceDirectory, "../../../static/css/bundles.json");
  const resolvedManifest = manifestPath || path.resolve(sourceDirectory, "../../../static/asset-manifest.json");
  let loaded = false;
  let bundles = {};
  let manifest = {};

  function load() {
    if (loaded) return;
    loaded = true;
    try {
      const spec = JSON.parse(fs.readFileSync(resolvedSpec, "utf8"));
      bundles = spec && spec.bundles && typeof spec.bundles === "object" ? spec.bundles : {};
    } catch {
      bundles = {};
    }
    try {
      const data = JSON.parse(fs.readFileSync(resolvedManifest, "utf8"));
      manifest = data && typeof data === "object" ? data : {};
    } catch {
      manifest = {};
    }
  }

  function asset(name) {
    load();
    if (typeof manifest[name] === "string" && manifest[name].length > 0) {
      return [manifest[name]];
    }
    const bundle = bundles[name];
    if (bundle && Array.isArray(bundle.hrefs) && bundle.hrefs.length > 0) {
      return [...bundle.hrefs];
    }
    console.warn(`asset(): unknown bundle "${name}"`);
    return [];
  }

  return { asset };
}
