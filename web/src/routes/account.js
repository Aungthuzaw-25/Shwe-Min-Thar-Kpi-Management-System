import { BackendResponseError, sendBackendError } from "../lib/backendClient.js";
import { loadBaseContext } from "../lib/pageContext.js";

function wrapPage(handler) {
  return async (request, response, next) => {
    try {
      await handler(request, response);
    } catch (error) {
      if (error instanceof BackendResponseError && error.status === 401) {
        return response.redirect(307, "/auth/login");
      }
      if (!sendBackendError(error, response, request)) next(error);
    }
  };
}

export function registerAccountRoutes(app) {
  app.get("/account/profile", wrapPage(async (request, response) => {
    const backend = request.app.locals.backend;
    const [base, accountProfile] = await Promise.all([
      loadBaseContext(request, backend),
      backend.get(request, "/api/v1/account/me"),
    ]);
    if (!base.context.user_role) return response.redirect(307, "/auth/login");

    const template = ["Super Admin", "Admin"].includes(base.context.user_role)
      ? "dashboard/account_profile_admin.njk"
      : "dashboard/account_profile.njk";
    response.render(template, {
      ...base.context,
      account_profile: accountProfile,
      timezone: process.env.TIMEZONE || "Asia/Yangon",
    });
  }));
}
