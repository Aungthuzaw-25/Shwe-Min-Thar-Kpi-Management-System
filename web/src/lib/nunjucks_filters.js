export function registerFilters(environment, nunjucks) {
  environment.addFilter("tojson", (value) => {
    const json = JSON.stringify(value)
      .replaceAll("<", String.raw`\u003c`)
      .replaceAll(">", String.raw`\u003e`)
      .replaceAll("&", String.raw`\u0026`)
      .replaceAll("\u2028", String.raw`\u2028`)
      .replaceAll("\u2029", String.raw`\u2029`);
    return new nunjucks.runtime.SafeString(json);
  });

  environment.addFilter("strftime", (value, format) => {
    if (value === null || value === undefined || value === "") {
      return "";
    }

    // A pure calendar date ("2024-02-03") is not an instant: render it
    // exactly as written so it can never shift a day.
    if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value.trim())) {
      const [year, month, day] = value.trim().split("-");
      return String(format)
        .replaceAll("%Y", year)
        .replaceAll("%m", month)
        .replaceAll("%d", day);
    }

    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) {
      throw new TypeError(`Cannot format invalid date value: ${value}`);
    }

    // Instants are shown on the application's panel timezone — the same
    // standard the audit logs use (their routes pass
    // process.env.TIMEZONE || "Asia/Yangon" as the render timezone).
    // Slicing the date out of the ISO string would show the UTC date,
    // which is a day early for evening-UTC timestamps.
    const parts = {};
    for (const part of new Intl.DateTimeFormat("en-US", {
      timeZone: process.env.TIMEZONE || "Asia/Yangon",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(date)) {
      parts[part.type] = part.value;
    }

    return String(format)
      .replaceAll("%Y", parts.year)
      .replaceAll("%m", parts.month)
      .replaceAll("%d", parts.day);
  });

  environment.addFilter("items", (value) => (
    value && typeof value === "object" ? Object.entries(value) : []
  ));
}
