(function () {
    function readCookie(name) {
        const prefix = `${name}=`;
        return document.cookie
            .split("; ")
            .find((cookie) => cookie.startsWith(prefix))
            ?.slice(prefix.length);
    }

    const originalFetch = window.fetch;
    window.fetch = function (input, init) {
        const options = init ? { ...init } : {};
        const method = (options.method || "GET").toUpperCase();
        const url = typeof input === "string" ? input : input.url;
        const sameOrigin = url.startsWith("/") || url.startsWith(window.location.origin);
        if (sameOrigin && !["GET", "HEAD", "OPTIONS"].includes(method)) {
            const headers = new Headers(options.headers || {});
            const token = readCookie("csrf_token");
            if (token) {
                headers.set("X-CSRF-Token", token);
            }
            options.headers = headers;
        }
        return originalFetch.call(this, input, options);
    };
})();
