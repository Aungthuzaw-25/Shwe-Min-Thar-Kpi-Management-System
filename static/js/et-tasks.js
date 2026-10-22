(() => {
  const page = document.querySelector(".et-page");
  if (!page) return;

  const tabs = ["btn-drafts-tab", "btn-rejected-tab", "btn-resign-tab", "btn-review-tab", "btn-completed-tab"]
    .map((id) => page.querySelector(`#${id}`))
    .filter(Boolean);
  const panels = ["drafts-tab", "rejected-tab", "resign-tab", "review-tab", "completed-tab"]
    .map((id) => page.querySelector(`#${id}`))
    .filter(Boolean);
  const searches = Array.from(page.querySelectorAll(".et-search-input"));

  tabs.forEach((tab, index) => {
    tab.dataset.etTab = String(index);
  });
  panels.forEach((panel, index) => {
    panel.dataset.etPanel = String(index);
  });

  const activateTab = (index, loadOriginalTab = true) => {
    if (!Number.isInteger(index) || index < 0 || index >= panels.length) return;
    const panel = panels.find((item) => Number(item.dataset.etPanel) === index);
    if (!panel) return;

    if (loadOriginalTab && typeof window.switchTab === "function") {
      window.switchTab(panel.id);
    }

    panels.forEach((item) => {
      const active = Number(item.dataset.etPanel) === index;
      item.hidden = !active;
      item.style.display = active ? "" : "none";
      item.classList.toggle("et-panel-active", active);
      item.classList.toggle("et-panel-hidden", !active);
    });

    tabs.forEach((tab) => {
      const active = Number(tab.dataset.etTab) === index;
      tab.classList.toggle("active", active);
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", String(active));
    });
  };

  window.activateEtTaskTab = activateTab;

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      activateTab(Number(tab.dataset.etTab));
    });
  });

  searches.forEach((input) => {
    const panel = input.closest("[data-et-panel]");
    if (!panel) return;

    input.addEventListener("input", () => {
      const query = input.value.trim().toLocaleLowerCase();
      panel.querySelectorAll("tbody tr").forEach((row) => {
        if (!row.querySelector("[colspan]")) {
          row.hidden = Boolean(query) && !row.textContent.toLocaleLowerCase().includes(query);
        }
      });
      panel.querySelectorAll("#rejectedList > *, #resignList > *").forEach((card) => {
        card.hidden = Boolean(query) && !card.textContent.toLocaleLowerCase().includes(query);
      });
    });
  });

  document.addEventListener("DOMContentLoaded", () => activateTab(0, false));
})();
