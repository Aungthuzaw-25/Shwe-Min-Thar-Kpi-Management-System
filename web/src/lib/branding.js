// Company trademark (branding) for display: company name, tagline, logo.
//
// Source of truth: GET /api/v1/system/branding/public — the backend's
// unauthenticated brand read (it also reports which OAuth providers are
// configured, for the login page). Values are cached for 60 seconds:
// branding changes rarely and every page render needs it.
//
// Fail-open by design: any error (API down, unexpected shape) returns
// the built-in Shwe Min Thar defaults, so a settings problem can never
// break a page — least of all the login page.

export const DEFAULT_BRAND = {
  company_name: "Shwe Min Thar",
  company_tagline: "Performance Matters",
  logo_url: "/static/image/smt-logo.jpg",
  has_custom_logo: false,
};

const BRAND_PATH = "/api/v1/system/branding/public";
const CACHE_TTL_MS = 60_000;

let cached = null; // { at, brand, oauthProviders } — successes only.

function sanitizeBrand(raw) {
  const brand = { ...DEFAULT_BRAND };
  if (raw && typeof raw === "object") {
    if (typeof raw.company_name === "string" && raw.company_name.trim()) {
      brand.company_name = raw.company_name;
    }
    // A saved empty tagline is meaningful ("" hides the subtitle), so
    // only a non-string value falls back to the default.
    if (typeof raw.company_tagline === "string") {
      brand.company_tagline = raw.company_tagline;
    }
    if (typeof raw.logo_url === "string" && raw.logo_url.startsWith("/")) {
      brand.logo_url = raw.logo_url;
    }
    brand.has_custom_logo = raw.has_custom_logo === true;
  }
  return brand;
}

function sanitizeProviders(raw) {
  const providers = raw && typeof raw === "object" ? raw.oauth_providers : null;
  return {
    google: providers?.google === true,
    microsoft: providers?.microsoft === true,
  };
}

export async function loadPublicBrand(request, backend) {
  if (cached && Date.now() - cached.at < CACHE_TTL_MS) {
    return cached;
  }
  try {
    const raw = await backend.get(request, BRAND_PATH);
    cached = {
      at: Date.now(),
      brand: sanitizeBrand(raw),
      oauthProviders: sanitizeProviders(raw),
    };
    return cached;
  } catch {
    return {
      brand: { ...DEFAULT_BRAND },
      oauthProviders: { google: false, microsoft: false },
    };
  }
}
