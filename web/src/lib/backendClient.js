export class BackendResponseError extends Error {
  constructor(status, contentType, body) {
    super(`Backend request failed with status ${status}`);
    this.name = "BackendResponseError";
    this.status = status;
    this.contentType = contentType;
    this.body = body;
  }
}

export function createBackendClient({
  baseUrl = process.env.API_BASE_URL || "http://127.0.0.1:8000",
  fetchImpl = fetch,
} = {}) {
  return {
    async get(request, path) {
      const headers = new Headers({ accept: "application/json" });
      for (const name of ["cookie", "authorization"]) {
        const value = request.get(name);
        if (value) headers.set(name, value);
      }

      const response = await fetchImpl(new URL(path, baseUrl), {
        method: "GET",
        headers,
        redirect: "manual",
      });
      const contentType = response.headers.get("content-type") || "";
      const text = await response.text();
      let body = text;
      if (text && contentType.includes("application/json")) {
        try {
          body = JSON.parse(text);
        } catch (error) {
          throw new Error(`Backend returned invalid JSON for ${path}`, { cause: error });
        }
      } else if (!text) {
        body = null;
      }

      if (!response.ok) {
        throw new BackendResponseError(response.status, contentType, body);
      }
      return body;
    },
  };
}

export function sendBackendError(error, response) {
  if (!(error instanceof BackendResponseError)) {
    return false;
  }

  response.status(error.status);
  if (error.body === null) {
    response.end();
  } else if (typeof error.body === "string") {
    response.type("text/plain");
    response.send(error.body);
  } else {
    response.type("application/json").send(JSON.stringify(error.body));
  }
  return true;
}
