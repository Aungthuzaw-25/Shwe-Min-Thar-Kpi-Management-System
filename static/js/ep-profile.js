(() => {
  const page = document.querySelector(".ep-page");
  if (!page) return;

  const permissionList = page.querySelector("#epPermissionList");
  const searchInput = page.querySelector("#epPermissionSearch");
  const clearButton = page.querySelector("#epPermissionSearchClear");
  const announcement = page.querySelector("#epPermissionSearchAnnouncement");
  const noPermissions = page.querySelector("#epNoPermissions");
  const noSearchResults = page.querySelector("#epNoSearchResults");
  const allowedCount = page.querySelector("#epAllowedCount");
  const unavailableCount = page.querySelector("#epUnavailableCount");

  if (permissionList && searchInput && clearButton && announcement) {
    const items = Array.from(
      permissionList.querySelectorAll(":scope > .ep-permission-item"),
    );
    const groups = new Map();

    items.forEach((item) => {
      const key = item.dataset.permissionKey || "";
      const separator = key.indexOf(".");
      const groupKey = separator > -1 ? key.slice(0, separator) : "General";
      const rawAction = separator > -1 ? key.slice(separator + 1) : key;
      const action = rawAction
        .split(/[._-]+/)
        .filter(Boolean)
        .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
        .join(" ") || "General";
      const actionNode = item.querySelector("[data-permission-action]");
      if (actionNode) actionNode.textContent = action;

      if (!groups.has(groupKey)) {
        const group = document.createElement("li");
        group.className = "ep-permission-group";
        group.dataset.permissionGroup = groupKey;

        const heading = document.createElement("div");
        heading.className = "ep-permission-group-heading";
        const title = document.createElement("span");
        title.textContent = groupKey === "General"
          ? "General"
          : groupKey.charAt(0).toUpperCase() + groupKey.slice(1);
        const count = document.createElement("span");
        count.className = "ep-permission-group-count";
        heading.append(title, count);

        const groupList = document.createElement("ul");
        groupList.className = "ep-permission-group-list";
        group.append(heading, groupList);
        groups.set(groupKey, { element: group, list: groupList, count });
      }
      groups.get(groupKey).list.append(item);
      item.dataset.permissionActionText = action;
      item.hidden = false;
    });

    permissionList.replaceChildren(...Array.from(groups.values(), ({ element }) => element));
    if (noPermissions) noPermissions.hidden = items.length > 0;

    const updatePermissionView = () => {
      const query = searchInput.value.trim().toLocaleLowerCase();
      let visibleCount = 0;
      let allowedTotal = 0;
      let unavailableTotal = 0;

      items.forEach((item) => {
        const key = (item.dataset.permissionKey || "").toLocaleLowerCase();
        const action = (item.dataset.permissionActionText || "").toLocaleLowerCase();
        const matches = !query || key.includes(query) || action.includes(query);
        item.hidden = !matches;
        if (matches) visibleCount += 1;
        if (item.dataset.permissionAllowed === "true") allowedTotal += 1;
        else unavailableTotal += 1;
      });

      groups.forEach(({ element, list, count }) => {
        const visibleItems = Array.from(list.children).filter((item) => !item.hidden);
        element.hidden = visibleItems.length === 0;
        count.textContent = String(visibleItems.length);
      });

      if (allowedCount) allowedCount.textContent = String(allowedTotal);
      if (unavailableCount) unavailableCount.textContent = String(unavailableTotal);
      if (noPermissions) noPermissions.hidden = items.length > 0;
      if (noSearchResults) noSearchResults.hidden = visibleCount > 0 || items.length === 0;
      permissionList.hidden = visibleCount === 0;
      clearButton.hidden = searchInput.value.length === 0;
      announcement.textContent = query
        ? `${visibleCount} of ${items.length} permission${items.length === 1 ? "" : "s"} match your search.`
        : `${items.length} permission${items.length === 1 ? "" : "s"} shown.`;
    };

    searchInput.addEventListener("input", updatePermissionView);
    clearButton.addEventListener("click", () => {
      searchInput.value = "";
      searchInput.focus();
      updatePermissionView();
    });
    updatePermissionView();
  }

  const handleAvatarError = (image) => {
    image.closest(".ep-avatar")?.classList.add("ep-avatar--image-failed");
  };

  page.querySelectorAll(".ep-avatar-image").forEach((image) => {
    if (image.complete && image.naturalWidth === 0) handleAvatarError(image);
  });

  page.addEventListener("error", (event) => {
    const image = event.target;
    if (!(image instanceof HTMLImageElement) || !image.matches(".ep-avatar-image")) return;
    handleAvatarError(image);
  }, true);
})();
