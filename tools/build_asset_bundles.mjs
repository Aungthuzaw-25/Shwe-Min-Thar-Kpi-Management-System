#!/usr/bin/env node
/**
 * Build hashed CSS bundles for Phase 5b asset bundling (perf/lcp).
 *
 * Reads static/css/bundles.json (the single source of truth for bundle order
 * and fallback hrefs), concatenates each bundle's files IN ORDER, minifies
 * the result with esbuild (no --bundle: sources have no @import and their
 * relative url() references, e.g. fonts.css -> ../fonts/, must survive
 * verbatim), and writes:
 *   static/css/<name>.<hash12>.css   (hash = first 12 hex of SHA-256 of the
 *                                     minified bytes, so the name changes
 *                                     whenever the content changes)
 *   static/asset-manifest.json       {"<name>": "/static/css/<name>.<hash>.css"}
 *
 * FAILS LOUDLY (exit 1, listing the missing file) when any source file is
 * missing (e.g. output.css was not built first) or when esbuild is not on
 * PATH. It never silently skips a bundle: a half-built manifest would make
 * the flag-on path fall back to individual links for that bundle, which is
 * safe but must not happen by accident in CI.
 *
 * Usage (run from the repo root / Docker build WORKDIR /app):
 *   node tools/build_asset_bundles.mjs [--css-dir static/css]
 *                                      [--manifest static/asset-manifest.json]
 *                                      [--spec static/css/bundles.json]
 * Dockerfiles invoke it AFTER the Tailwind build (output.css must exist).
 * The esbuild binary is expected on PATH (Dockerfile installs it globally);
 * no esbuild dependency is added to any package.json.
 */
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";

function parseArgs(argv) {
  const opts = {
    cssDir: "static/css",
    manifest: "static/asset-manifest.json",
    spec: "static/css/bundles.json",
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--css-dir") opts.cssDir = argv[++i];
    else if (arg === "--manifest") opts.manifest = argv[++i];
    else if (arg === "--spec") opts.spec = argv[++i];
    else throw new Error(`Unknown argument: ${arg}`);
  }
  return opts;
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  const cssDir = resolve(opts.cssDir);
  const manifestPath = resolve(opts.manifest);
  const specPath = resolve(opts.spec);

  let spec;
  try {
    spec = JSON.parse(readFileSync(specPath, "utf8"));
  } catch (err) {
    console.error(`asset bundles: cannot read spec ${specPath}: ${err.message}`);
    process.exit(1);
  }
  const bundles = spec.bundles;
  if (!bundles || typeof bundles !== "object") {
    console.error(`asset bundles: spec ${specPath} has no "bundles" object`);
    process.exit(1);
  }

  const manifest = {};
  for (const [name, bundle] of Object.entries(bundles)) {
    if (!Array.isArray(bundle.files) || bundle.files.length === 0) {
      console.error(`asset bundles: bundle "${name}" has no files`);
      process.exit(1);
    }
    // Read in spec order; missing source = hard failure, never a skip.
    const chunks = [];
    for (const file of bundle.files) {
      const filePath = join(cssDir, file);
      try {
        chunks.push(readFileSync(filePath, "utf8"));
      } catch {
        console.error(`asset bundles: missing source ${filePath} for bundle "${name}"`);
        process.exit(1);
      }
    }
    // Newline-terminate every chunk so concatenation can never merge two
    // rules across a file boundary (a file without a trailing newline would
    // otherwise glue its last rule to the next file's first rule).
    const concatenated = chunks.map((c) => (c.endsWith("\n") ? c : `${c}\n`)).join("");
    const tmpInput = join(tmpdir(), `asset-bundle-${name}-${process.pid}.css`);
    const tmpOutput = join(tmpdir(), `asset-bundle-${name}-${process.pid}.min.css`);
    writeFileSync(tmpInput, concatenated);
    try {
      execFileSync("esbuild", [tmpInput, "--minify", `--outfile=${tmpOutput}`], { stdio: "pipe" });
    } catch (err) {
      console.error(`asset bundles: esbuild failed for "${name}" (is esbuild on PATH?): ${err.message}`);
      process.exit(1);
    }
    const minified = readFileSync(tmpOutput);
    const hash = createHash("sha256").update(minified).digest("hex").slice(0, 12);
    const outName = `${name}.${hash}.css`;
    writeFileSync(join(cssDir, outName), minified);
    manifest[name] = `/static/css/${outName}`;
    console.log(`asset bundles: ${name} -> ${manifest[name]} (${minified.length} bytes)`);
  }

  mkdirSync(dirname(manifestPath), { recursive: true });
  writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
  console.log(`asset bundles: manifest written to ${manifestPath}`);
}

main();
