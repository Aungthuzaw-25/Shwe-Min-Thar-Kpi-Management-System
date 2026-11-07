'use strict';

// Collapsible sidebar: icon rail on desktop, persisted across pages.
// The early-apply script in the layout <head> restores the saved state
// before first paint; this script wires the toggle, the Ctrl/Cmd+B
// shortcut and the collapsed-icon tooltips. No-ops on pages without
// a sidebar. Plain addEventListener wiring: CSP-safe.
(function () {
    var STORAGE_KEY = 'kpi-sidebar-collapsed';

    var sidebar = document.getElementById('mainSidebar');
    if (!sidebar) return;
    var toggleBtn = document.getElementById('sidebarCollapseBtn');

    function isCollapsed() {
        return document.documentElement.classList.contains('sidebar-collapsed');
    }

    function apply(collapsed) {
        document.documentElement.classList.toggle('sidebar-collapsed', collapsed);
        try {
            localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0');
        } catch (error) { /* private mode: state just does not persist */ }
        if (toggleBtn) {
            toggleBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            toggleBtn.setAttribute('aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
        }
        hideTooltip();
    }

    function toggleSidebar() {
        apply(!isCollapsed());
    }

    if (toggleBtn) toggleBtn.addEventListener('click', toggleSidebar);

    document.addEventListener('keydown', function (event) {
        // Ctrl+B / Cmd+B, like shadcn. Shift is excluded: Ctrl+Shift+B
        // is the browser's bookmarks-bar shortcut. Typing is excluded.
        if (!(event.ctrlKey || event.metaKey) || event.shiftKey || event.altKey) return;
        if (event.key !== 'b' && event.key !== 'B') return;
        var target = event.target;
        if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA'
            || target.tagName === 'SELECT' || target.isContentEditable)) return;
        event.preventDefault();
        toggleSidebar();
    });

    // Tooltips on collapsed icons: the label text is still in the DOM
    // (hidden by CSS), so it is read from the link itself. The tooltip
    // node is appended to <body> so no sidebar overflow or transform
    // can clip it (same pattern as the submenu panels).
    var tooltip = null;

    function hideTooltip() {
        if (tooltip) {
            tooltip.remove();
            tooltip = null;
        }
    }

    function showTooltip(link) {
        if (!isCollapsed() || window.innerWidth < 768) return;
        var label = (link.textContent || '').trim();
        if (!label) return;
        hideTooltip();
        tooltip = document.createElement('div');
        tooltip.className = 'sidebar-tooltip';
        tooltip.textContent = label;
        document.body.appendChild(tooltip);
        var rect = link.getBoundingClientRect();
        tooltip.style.left = (rect.right + 10) + 'px';
        tooltip.style.top = (rect.top + rect.height / 2 - tooltip.offsetHeight / 2) + 'px';
    }

    sidebar.querySelectorAll('nav a').forEach(function (link) {
        link.addEventListener('mouseenter', function () { showTooltip(link); });
        link.addEventListener('mouseleave', hideTooltip);
        link.addEventListener('focus', function () { showTooltip(link); });
        link.addEventListener('blur', hideTooltip);
        link.addEventListener('click', hideTooltip);
    });

    window.addEventListener('scroll', hideTooltip, true);
    window.addEventListener('resize', hideTooltip);
})();
