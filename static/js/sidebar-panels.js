// Sidebar submenu panels: a parent click opens its floating panel
// instead of navigating (direct URLs, modified clicks and no-JS still
// navigate — the parent hrefs stay intact). Panels are server-rendered
// in the sidebar and moved to <body> on open, so no transformed sidebar
// ancestor traps their fixed positioning (same pattern as the users
// row menu). Plain addEventListener wiring: CSP-safe.
(function () {
  let open = null;

  function closePanel() {
    if (!open) return;
    open.panel.hidden = true;
    open.parent.setAttribute('aria-expanded', 'false');
    open.parent.classList.remove('sidebar-panel-open');
    open = null;
  }

  function positionPanel(parent, panel) {
    const rect = parent.getBoundingClientRect();
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    if (viewportWidth >= 768) {
      panel.style.left = (rect.right + 12) + 'px';
      panel.style.right = 'auto';
      panel.style.width = '';
      const height = panel.offsetHeight;
      panel.style.top = Math.max(12, Math.min(rect.top, viewportHeight - height - 12)) + 'px';
    } else {
      panel.style.left = '12px';
      panel.style.right = '12px';
      panel.style.width = 'auto';
      const height = panel.offsetHeight;
      panel.style.top = Math.max(12, Math.min(rect.bottom + 8, viewportHeight - height - 12)) + 'px';
    }
  }

  function openPanel(parent, panel) {
    closePanel();
    if (panel.parentElement !== document.body) document.body.appendChild(panel);
    panel.hidden = false;
    positionPanel(parent, panel);
    parent.setAttribute('aria-expanded', 'true');
    parent.classList.add('sidebar-panel-open');
    open = { parent, panel };
  }

  document.querySelectorAll('a[data-sidebar-panel]').forEach((parent) => {
    const panel = document.getElementById('sidebar-panel-' + parent.dataset.sidebarPanel);
    if (!panel) return;
    parent.setAttribute('aria-haspopup', 'true');
    parent.setAttribute('aria-expanded', 'false');
    parent.setAttribute('aria-controls', panel.id);
    parent.addEventListener('click', (event) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      if (open && open.parent === parent) {
        closePanel();
      } else {
        openPanel(parent, panel);
      }
    });
  });

  function followParent() {
    if (!open) return;
    const rect = open.parent.getBoundingClientRect();
    if (rect.bottom < 0 || rect.top > window.innerHeight) {
      closePanel();
    } else {
      positionPanel(open.parent, open.panel);
    }
  }

  document.addEventListener('click', (event) => {
    if (!open) return;
    if (open.panel.contains(event.target) || open.parent.contains(event.target)) return;
    closePanel();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closePanel();
  });

  // A scroll that only brings the clicked parent into view (e.g. the
  // browser's focus scroll on a below-fold link) must not kill the
  // panel it just opened: follow the parent instead, and close only
  // once the parent itself has scrolled out of view.
  document.addEventListener('scroll', () => followParent(), true);
  window.addEventListener('resize', () => followParent());
})();
