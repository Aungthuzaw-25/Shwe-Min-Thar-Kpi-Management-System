import { loadPageContext } from "../lib/pageContext.js";
import { sendBackendError } from "../lib/backendClient.js";

export function registerActivityRoutes(app) {
  app.get("/notifications", async (request, response, next) => {
    try {
      const backend = request.app.locals.backend;
      const { base, context } = await loadPageContext(request, backend, { employee: true });
      if (!base.context.user_role) {
        return response.redirect(307, "/auth/login");
      }
      context.active_page = "notifications";
      context.layout_template = ["Super Admin", "Admin"]
        .includes(context.user_role)
        ? "layouts/base.njk"
        : "layouts/employee_base.njk";
      response.render("dashboard/notifications.njk", context);
    } catch (error) {
      if (!sendBackendError(error, response, request)) next(error);
    }
  });
}
