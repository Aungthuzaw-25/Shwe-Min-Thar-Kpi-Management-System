import { loadPublicBrand } from "../lib/branding.js";

export function registerAuthRoutes(app) {
  app.get("/auth/login", async (request, response) => {
    // The login page shows the company trademark and only the OAuth
    // providers that are actually configured. loadPublicBrand never
    // rejects (built-in defaults on any failure), so the login page
    // always renders.
    const { brand, oauthProviders } = await loadPublicBrand(
      request,
      request.app.locals.backend,
    );
    response.render("auth/login.njk", { brand, oauth_providers: oauthProviders });
  });
  app.get("/auth/change-password", (_request, response) => {
    response.render("auth/change_password.njk");
  });
}
