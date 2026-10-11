export function registerAuthRoutes(app) {
  app.get("/auth/login", (_request, response) => {
    response.render("auth/login.njk");
  });
  app.get("/auth/change-password", (_request, response) => {
    response.render("auth/change_password.njk");
  });
}
