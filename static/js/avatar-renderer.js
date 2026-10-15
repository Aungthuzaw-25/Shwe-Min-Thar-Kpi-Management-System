(() => {
  const html = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);

  function initialsFor(user, displayName) {
    const name = String(displayName || "").normalize("NFC").trim();
    if (!name) {
      const emailInitial = String(user?.email || "").trim().normalize("NFC");
      return emailInitial ? Array.from(new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(emailInitial))[0].segment.toLocaleUpperCase() : "?";
    }
    const words = name.split(/\s+/u);
    const segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });
    const firstCluster = (word) => Array.from(segmenter.segment(word))[0]?.segment || "";
    return `${firstCluster(words[0])}${words.length > 1 ? firstCluster(words.at(-1)) : ""}`.toLocaleUpperCase();
  }

  function colorIndexFor(user) {
    const identifier = String(user?.user_id || user?.id || "").replaceAll("-", "");
    if (/^[0-9a-f]+$/i.test(identifier)) return Number(BigInt(`0x${identifier}`) % 8n);
    if (/^\d+$/.test(identifier)) return Number(BigInt(identifier) % 8n);
    return 0;
  }

  window.kpiAvatarMarkup = (user, size = 32) => {
    const allowedSizes = new Set([24, 32, 40, 64, 96, 128]);
    const avatarSize = allowedSizes.has(Number(size)) ? Number(size) : 32;
    const displayName = user?.display_name || user?.full_name || user?.employee_name
      || user?.actor_name || user?.submitter_name || user?.name
      || [user?.first_name, user?.last_name].filter(Boolean).join(" ") || user?.email || "User";
    const initials = user?.initials || initialsFor(user, displayName);
    const colorIndex = Number.isInteger(user?.avatar_color_index)
      ? Math.min(7, Math.max(0, user.avatar_color_index))
      : colorIndexFor(user);
    const photoUrl = typeof user?.avatar_url === "string"
      && /^\/api\/v1\/account\/avatars\/[a-f0-9]{32}\.webp(?:\?v=\d+)?$/i.test(user.avatar_url)
      ? user.avatar_url
      : null;
    const classes = `kpi-avatar kpi-avatar--${avatarSize} kpi-avatar--color-${colorIndex}${photoUrl ? " kpi-avatar--photo" : ""}`;
    const image = photoUrl
      ? `<img class="kpi-avatar__image" src="${html(photoUrl)}" alt="${html(displayName)}" loading="lazy" width="${avatarSize}" height="${avatarSize}" data-kpi-avatar-image>`
      : "";
    const fallbackClass = photoUrl ? " kpi-avatar__fallback" : "";
    return `<span class="${classes}" role="img" aria-label="${html(displayName)}">${image}<span class="kpi-avatar__initials${fallbackClass}" aria-hidden="true">${html(initials)}</span></span>`;
  };
})();
