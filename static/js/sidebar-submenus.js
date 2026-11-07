// Sidebar submenus, INLINE inside the sidebar (shadcn SidebarMenuSub /
// Collapsible pattern). A parent click expands its children below the
// parent; it never navigates on a plain left click (direct URLs, modified
// clicks and no-JS still navigate — the parent hrefs stay intact). One
// submenu is open at a time (accordion). The server renders the active
// parent's submenu already expanded (see the layout); this script only
// toggles, and highlights the active child by exact URL match.
// Collapsed-rail click (owner decision): clicking a parent while the rail
// is collapsed expands the rail AND opens that parent's submenu inline —
// no floating popover panel is brought back for this. Plain
// addEventListener wiring: CSP-safe. No-ops on pages without a sidebar.
(function () {
  'use strict';

  var STORAGE_KEY = 'kpi-sidebar-collapsed';

  var parents = document.querySelectorAll('a[data-sidebar-submenu]');
  if (!parents.length) return;

  var openSubmenu = null;

  function isCollapsedRail() {
    return window.innerWidth >= 768 &&
      document.documentElement.classList.contains('sidebar-collapsed');
  }

  function expandRail() {
    document.documentElement.classList.remove('sidebar-collapsed');
    try {
      localStorage.setItem(STORAGE_KEY, '0');
    } catch (error) { /* private mode: state just does not persist */ }
    var toggle = document.getElementById('sidebarCollapseBtn');
    if (toggle) {
      toggle.setAttribute('aria-expanded', 'true');
      toggle.setAttribute('aria-label', 'Collapse sidebar');
    }
  }

  function setOpen(parent, submenu, willOpen) {
    submenu.hidden = !willOpen;
    parent.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    if (willOpen) {
      openSubmenu = submenu;
    } else if (openSubmenu === submenu) {
      openSubmenu = null;
    }
  }

  parents.forEach(function (parent) {
    var submenu = document.getElementById('sidebar-submenu-' + parent.dataset.sidebarSubmenu);
    if (!submenu) return;
    submenu._parent = parent;
    parent.setAttribute('aria-controls', submenu.id);
    parent.setAttribute('aria-expanded', submenu.hidden ? 'false' : 'true');
    if (!submenu.hidden) openSubmenu = submenu;
    parent.addEventListener('click', function (event) {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      if (isCollapsedRail()) {
        // A collapsed rail hides every inline section by CSS, so from the
        // user's view the section is closed even when its hidden flag is
        // still off (it was open before collapsing). Force it open rather
        // than toggling it shut.
        expandRail();
        if (openSubmenu && openSubmenu !== submenu) {
          setOpen(openSubmenu._parent, openSubmenu, false);
        }
        setOpen(parent, submenu, true);
        return;
      }
      var willOpen = submenu.hidden;
      if (willOpen && openSubmenu && openSubmenu !== submenu) {
        setOpen(openSubmenu._parent, openSubmenu, false);
      }
      setOpen(parent, submenu, willOpen);
    });
  });

  // Active-child highlight: exact URL match (path + query + hash), so a
  // ?view= or #anchor child lights up when it is the page you are on. The
  // Users page with no view defaults to Active (its existing view logic),
  // so that row lights up too. A matched row also opens its submenu if
  // the server did not already (defensive: every known child belongs to
  // a server-expanded parent, but never show a highlighted child inside
  // a closed section).
  var current = window.location.pathname + window.location.search + window.location.hash;
  var matched = null;
  document.querySelectorAll('.sidebar-submenu__row').forEach(function (row) {
    if (row.getAttribute('href') === current) matched = row;
  });
  if (!matched && window.location.pathname === '/superadmin/users' && !window.location.search) {
    document.querySelectorAll('.sidebar-submenu__row').forEach(function (row) {
      if (row.getAttribute('href') === '/superadmin/users?view=active') matched = row;
    });
  }
  if (matched) {
    matched.classList.add('sidebar-submenu__row--active');
    var submenu = matched.closest('.sidebar-submenu');
    if (submenu && submenu.hidden && submenu._parent) {
      if (openSubmenu && openSubmenu !== submenu) {
        setOpen(openSubmenu._parent, openSubmenu, false);
      }
      setOpen(submenu._parent, submenu, true);
    }
  }
})();
