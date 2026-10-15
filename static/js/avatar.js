(() => {
  document.addEventListener("error", (event) => {
    const image = event.target;
    if (!(image instanceof HTMLImageElement) || !image.matches("[data-kpi-avatar-image]")) return;
    image.closest(".kpi-avatar")?.classList.add("kpi-avatar--image-failed");
  }, true);
})();
