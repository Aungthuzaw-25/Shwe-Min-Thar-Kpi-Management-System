import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const users = read('templates/dashboard/users.njk');
const notifications = read('static/js/notifications.js');
const thead = users.split('<thead')[1].split('</thead>')[0];

describe('users page format', () => {
  it('has the new six-column header', () => {
    expect(thead).toContain('id="selectAllUsers"');
    for (const header of ['User', 'Access', 'Last active', 'Date added']) {
      expect(thead).toContain(header);
    }
  });

  it('drops the old columns', () => {
    for (const header of ['Online Status', 'Active Time', 'Offline Time', 'Assigned Departments', 'System Role']) {
      expect(thead).not.toContain(header);
    }
    expect(users).not.toContain('last_offline_at');
    expect(users).not.toContain('colspan="9"');
  });

  it('shows All users, Filters and Add user in the header', () => {
    expect(users).toContain('All users <span id="allUsersNumber">');
    expect(users).toContain('Filters');
    expect(users).toContain('Add user');
    expect(users).toContain('id="userFiltersPanel"');
    expect(users).toContain('id="userFilterRole"');
    expect(users).toContain('id="userFilterPresence"');
  });

  it('pages client-side with a showing line', () => {
    expect(users).toContain('id="usersPaginationBar"');
    expect(users).toContain('id="usersPagination"');
    expect(users).toContain('id="usersShowingText"');
    expect(users).toContain('USERS_PAGE_SIZE = 10');
    expect(users).toContain('function goToUsersPage');
    expect(users).toContain('function getVisibleUsers');
    // The CSP bridge resolves quoted-string literals only, so page
    // numbers in inline handlers must be quoted, never bare numbers.
    expect(users).toContain("goToUsersPage('${p}')");
    expect(users).not.toContain('onclick="goToUsersPage(${p})"');
    expect(users).not.toContain('onclick="goToUsersPage(${page})"');
  });

  it('keeps the U4/U5 gates inside the row menu', () => {
    expect(users).toContain("!isSuperAdmin && canPerm('users.edit')");
    expect(users).toContain("!isSuperAdmin && canPerm('users.password_reset')");
    expect(users).toContain("!isSuperAdmin && canPerm('users.delete')");
    expect(users).toContain('user-row-menu');
    expect(users).toContain('function toggleUserMenu');
    expect(users).toContain('function closeAllUserMenus');
    // Row entrance animation traps fixed menus inside the row, so the
    // menu is moved onto <body> while open.
    expect(users).toContain('document.body.appendChild(menu)');
  });

  it('selects rows without a bulk action', () => {
    expect(users).toContain('user-row-checkbox');
    expect(users).toContain('function toggleUserSelection');
    expect(users).toContain('function toggleSelectAllUsers');
    expect(users).toContain('selected');
  });

  it('toasts an update with a View profile action and no invented Undo', () => {
    expect(users).toContain('details updated');
    expect(users).toContain('View profile');
    expect(notifications).toContain('app-toast-action');
    expect(notifications).toContain('options.actions');
  });

  it('keeps the loading row a real table cell so it centers across all 6 columns', () => {
    // Regression: flex classes on the <td> turned it into a flex
    // container, so colspan stopped spanning and the row sat left.
    // The centering lives on an inner div now, not the cell itself.
    const textAt = users.indexOf('Loading user data...');
    const tdAt = users.lastIndexOf('<td', textAt);
    const tag = users.slice(tdAt, users.indexOf('>', tdAt) + 1);
    expect(tag).toContain('colspan="6"');
    expect(tag).toContain('text-center');
    expect(tag).not.toContain('flex');
    expect(users.slice(tdAt, textAt)).toContain('flex flex-col items-center justify-center');
    expect(users).not.toMatch(/<td[^>]*\bflex\b/);
  });
});

describe('users subpages (Active / Deleted as views, not tabs)', () => {
  it('has no tab-switching state left', () => {
    expect(users).not.toContain('switchTab');
    expect(users).not.toContain('currentTab');
    expect(users).not.toContain('activeTabBtn');
    expect(users).not.toContain('deletedTabBtn');
    expect(users).not.toContain('refreshCurrentTab');
    expect(users).not.toContain('resetUserTableState');
  });

  it('resolves the view from ?view=, maps legacy ?tab=, defaults to Active', () => {
    expect(users).toContain('function resolveUserView()');
    expect(users).toContain("params.get('view')");
    expect(users).toContain("if (view === 'active' || view === 'deleted') return view;");
    expect(users).toContain("if (params.get('tab') === 'deleted') return 'deleted';");
    expect(users).toContain("return 'active';");
    expect(users).toContain('const USER_VIEW = resolveUserView();');
    // Canonical bookmarkable URL, via replaceState (no extra history entry).
    expect(users).toContain("history.replaceState(null, '', '/superadmin/users?view=' + USER_VIEW);");
    // Cosmetic only: a canonicalization failure must never block the list.
    expect(users).toContain('Canonical URL is cosmetic');
  });

  it('loads only the current view and refreshes only the current view', () => {
    expect(users).toContain('function loadCurrentViewUsers()');
    expect(users).toContain("return USER_VIEW === 'deleted' ? loadDeletedUsers() : loadUsers();");
    expect(users).toContain('await loadCurrentViewUsers();');
    expect(users).toContain('function refreshUsersView()');
    expect(users).toContain('Active users list refreshed.');
    expect(users).toContain('Deleted users trash refreshed.');
  });

  it('shows a per-view heading, empty text, and row menus', () => {
    expect(users).toContain('id="usersViewTitle"');
    expect(users).toContain('id="usersViewSubtitle"');
    expect(users).toContain('Deleted Users (Trash)');
    expect(users).toContain("USER_VIEW === 'active' ? 'No active users found.' : 'No deleted users in trash.'");
    expect(users).toContain("const isUserDeleted = USER_VIEW === 'deleted';");
    // Sidebar child highlight follows the current view.
    expect(users).toContain("sidebar-panel__row--active");
    expect(users).toContain("'/superadmin/users?view=' + USER_VIEW");
  });

  it('keeps Add user on the Active view only', () => {
    expect(users).toContain('id="addUserButton"');
    expect(users).toContain("if (USER_VIEW === 'deleted') document.getElementById('addUserButton')?.remove();");
  });
});
