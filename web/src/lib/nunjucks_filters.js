export function registerFilters(environment, nunjucks) {
  environment.addFilter("tojson", (value) => {
    const json = JSON.stringify(value)
      .replaceAll("<", "\\u003c")
      .replaceAll(">", "\\u003e")
      .replaceAll("&", "\\u0026")
      .replaceAll("\u2028", "\\u2028")
      .replaceAll("\u2029", "\\u2029");
    return new nunjucks.runtime.SafeString(json);
  });

  environment.addFilter("strftime", (value, format) => {
    if (value === null || value === undefined || value === "") {
      return "";
    }

    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) {
      throw new TypeError(`Cannot format invalid date value: ${value}`);
    }

    const match = typeof value === "string"
      ? /^(\d{4})-(\d{2})-(\d{2})/.exec(value)
      : null;
    const parts = match
      ? { year: match[1], month: match[2], day: match[3] }
      : {
          year: String(date.getFullYear()).padStart(4, "0"),
          month: String(date.getMonth() + 1).padStart(2, "0"),
          day: String(date.getDate()).padStart(2, "0"),
        };

    return String(format)
      .replaceAll("%Y", parts.year)
      .replaceAll("%m", parts.month)
      .replaceAll("%d", parts.day);
  });

  environment.addFilter("items", (value) => (
    value && typeof value === "object" ? Object.entries(value) : []
  ));
}
