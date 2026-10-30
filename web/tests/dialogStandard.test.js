import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = (rel) => readFileSync(path.join(repo, rel), 'utf8');

// Fake DOM: just enough for dialog.js (focusables, listeners, focus).
function fakeElement(tag, doc) {
  const el = {
    tag: tag.toUpperCase(), tagName: tag.toUpperCase(), children: [], attributes: {}, dataset: {},
    disabled: false, value: '', textContent: '', parentNode: null,
    isConnected: true,
    listeners: {},
    classList: {
      _set: new Set(),
      add(c) { this._set.add(c); },
      remove(c) { this._set.delete(c); },
      contains(c) { return this._set.has(c); },
    },
    setAttribute(k, v) { this.attributes[k] = String(v); },
    getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; },
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    removeEventListener(type, fn) {
      this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn);
    },
    appendChild(child) {
      child.parentNode = this;
      this.children.push(child);
      return child;
    },
    contains(node) {
      let cur = node;
      while (cur) { if (cur === this) return true; cur = cur.parentNode; }
      return false;
    },
    focus() { doc.activeElement = this; },
    querySelectorAll(selector) {
      const out = [];
      const walk = (node) => {
        for (const child of node.children) {
          if (matchesFocusableSelector(child, selector)) out.push(child);
          walk(child);
        }
      };
      walk(this);
      return out;
    },
  };
  return el;
}

function matchesFocusableSelector(el, selector) {
  return selector.split(',').some((part) => {
    const p = part.trim();
    if (p.startsWith('[')) {
      return el.attributes.tabindex !== undefined && el.attributes.tabindex !== '-1';
    }
    const m = p.match(/^([a-z]+)(\[href\])?(:not\(\[disabled\]\))?$/);
    if (!m) return false;
    if (el.tag !== m[1].toUpperCase()) return false;
    if (m[2] && el.attributes.href === undefined) return false;
    if (m[3] && el.disabled) return false;
    return true;
  });
}

function makeEnv() {
  const doc = {
    activeElement: null,
    listeners: {},
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    removeEventListener(type, fn) {
      this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn);
    },
    createElement: (tag) => fakeElement(tag, doc),
  };
  const sandbox = { window: {}, document: doc, console };
  vm.runInNewContext(read('static/js/dialog.js'), sandbox);
  return { doc, SharedDialog: sandbox.window.SharedDialog };
}

function makeModal(doc, { fields = true } = {}) {
  const modal = fakeElement('div', doc);
  const card = fakeElement('div', doc);
  modal.appendChild(card);
  const x = fakeElement('button', doc);
  card.appendChild(x);
  let input = null;
  if (fields) {
    input = fakeElement('input', doc);
    card.appendChild(input);
  }
  const cancel = fakeElement('button', doc);
  card.appendChild(cancel);
  const confirm = fakeElement('button', doc);
  card.appendChild(confirm);
  return { modal, card, x, input, cancel, confirm };
}

function fireKeydown(doc, event) {
  (doc.listeners.keydown || []).forEach((fn) => fn(event));
}

describe('SharedDialog controller', () => {
  it('focuses the first field on open, not a button', () => {
    const { doc, SharedDialog } = makeEnv();
    const trigger = fakeElement('button', doc);
    doc.activeElement = trigger;
    const { modal, input } = makeModal(doc);
    SharedDialog.open(modal, () => {});
    expect(doc.activeElement).toBe(input);
  });

  it('Escape calls onCancel and never confirms', () => {
    const { doc, SharedDialog } = makeEnv();
    const { modal } = makeModal(doc);
    let cancelled = 0;
    let confirmed = 0;
    SharedDialog.open(modal, () => { cancelled += 1; });
    fireKeydown(doc, { key: 'Escape', preventDefault() {} });
    expect(cancelled).toBe(1);
    expect(confirmed).toBe(0);
  });

  it('backdrop click cancels, card click does not', () => {
    const { doc, SharedDialog } = makeEnv();
    const { modal, card } = makeModal(doc);
    let cancelled = 0;
    SharedDialog.open(modal, () => { cancelled += 1; });
    modal.listeners.click.forEach((fn) => fn({ target: card }));
    expect(cancelled).toBe(0);
    modal.listeners.click.forEach((fn) => fn({ target: modal }));
    expect(cancelled).toBe(1);
  });

  it('traps Tab inside the modal', () => {
    const { doc, SharedDialog } = makeEnv();
    const { modal, x, confirm } = makeModal(doc);
    SharedDialog.open(modal, () => {});
    doc.activeElement = confirm;
    fireKeydown(doc, { key: 'Tab', shiftKey: false, preventDefault() {} });
    expect(doc.activeElement).toBe(x);
    doc.activeElement = x;
    fireKeydown(doc, { key: 'Tab', shiftKey: true, preventDefault() {} });
    expect(doc.activeElement).toBe(confirm);
  });

  it('returns focus to the trigger on close', () => {
    const { doc, SharedDialog } = makeEnv();
    const trigger = fakeElement('button', doc);
    doc.activeElement = trigger;
    const { modal } = makeModal(doc);
    SharedDialog.open(modal, () => {});
    SharedDialog.close(modal);
    expect(doc.activeElement).toBe(trigger);
  });

  it('stacks: Escape closes the topmost modal only; closeAll closes both', () => {
    const { doc, SharedDialog } = makeEnv();
    const a = makeModal(doc);
    const b = makeModal(doc);
    let aCancelled = 0;
    let bCancelled = 0;
    SharedDialog.open(a.modal, () => { aCancelled += 1; });
    SharedDialog.open(b.modal, () => { bCancelled += 1; });
    fireKeydown(doc, { key: 'Escape', preventDefault() {} });
    expect(bCancelled).toBe(1);
    expect(aCancelled).toBe(0);
    SharedDialog.closeAll();
    expect(aCancelled).toBe(0); // closeAll unwires without cancelling
    fireKeydown(doc, { key: 'Escape', preventDefault() {} });
    expect(aCancelled).toBe(0);
    expect(bCancelled).toBe(1);
  });

  it('close on a non-open modal is a no-op', () => {
    const { SharedDialog } = makeEnv();
    const { modal } = makeModal({ activeElement: null });
    expect(() => SharedDialog.close(modal)).not.toThrow();
  });
});

describe('dialog wiring pins', () => {
  const pages = ['users', 'departments', 'positions', 'kpi_periods', 'target',
    'action_plans', 'super_admin_management', 'superadmin_kpi', 'system_settings', 'tasks'];
  it('every modal page loads the controller + backdrop stylesheet', () => {
    for (const p of pages) {
      const t = read(`templates/dashboard/${p}.njk`);
      expect(t).toContain('<script src="/static/js/dialog.js">');
      expect(t).toContain('<link rel="stylesheet" href="/static/css/dialog.css">');
    }
    const login = read('templates/auth/login.njk');
    expect(login).toContain('<script src="/static/js/dialog.js">');
    expect(login).toContain('<link rel="stylesheet" href="/static/css/dialog.css">');
  });

  it('every custom modal opens/closes through SharedDialog', () => {
    const cases = [
      ['templates/dashboard/users.njk', 'SharedDialog.open(modal, function () { toggleModal(modalId, contentId, false); })'],
      ['templates/dashboard/departments.njk', 'SharedDialog.open(document.getElementById("departmentModal"), closeDepartmentModal)'],
      ['templates/dashboard/departments.njk', 'SharedDialog.open(document.getElementById("assignUserModal"), closeAssignModal)'],
      ['templates/dashboard/departments.njk', 'SharedDialog.open(document.getElementById("actionModal"), closeActionModal)'],
      ['templates/dashboard/positions.njk', "SharedDialog.open(document.getElementById('positionModal'), closeModal)"],
      ['templates/dashboard/kpi_periods.njk', 'SharedDialog.open(modal, closeModal)'],
      ['templates/dashboard/target.njk', 'SharedDialog.open(modal, closeTargetModal)'],
      ['templates/dashboard/action_plans.njk', "SharedDialog.open(document.getElementById('reviewModal'), closeModal)"],
      ['templates/dashboard/super_admin_management.njk', 'SharedDialog.open(modal, closeModal)'],
      ['templates/dashboard/system_settings.njk', "SharedDialog.open(document.getElementById('emailProviderModal'), closeEmailProviderModal)"],
      ['templates/dashboard/tasks.njk', 'SharedDialog.open(modal, closeModals)'],
      ['templates/auth/login.njk', "SharedDialog.open(document.getElementById('forgotPasswordModal'), closeForgotPasswordModal)"],
      ['static/js/superadmin-kpi.js', 'SharedDialog.open(modal, closeFormulasModal)'],
      ['static/js/superadmin-kpi.js', 'SharedDialog.open(modal, closeInterventionModal)'],
      ['static/js/departed-records.js', 'SharedDialog.open(modal, window.closeOffboardModal)'],
    ];
    for (const [file, needle] of cases) expect(read(file)).toContain(needle);
  });

  it('every modal overlay uses the shared backdrop class', () => {
    const modalIds = ['userModal', 'offboardModal', 'resetPasswordModal', 'departmentModal',
      'assignUserModal', 'actionModal', 'positionModal', 'periodModal', 'targetModal',
      'reviewModal', 'superAdminModal', 'formulasModal', 'interventionModal',
      'emailProviderModal', 'selfTaskModal', 'assignTaskModal', 'uploadModal',
      'resubmitModal', 'detailsModal', 'submitTargetModal', 'resignToL2Modal',
      'forgotPasswordModal'];
    const all = ['templates/dashboard/users.njk', 'templates/dashboard/departments.njk',
      'templates/dashboard/positions.njk', 'templates/dashboard/kpi_periods.njk',
      'templates/dashboard/target.njk', 'templates/dashboard/action_plans.njk',
      'templates/dashboard/super_admin_management.njk', 'templates/dashboard/superadmin_kpi.njk',
      'templates/dashboard/system_settings.njk', 'templates/dashboard/tasks.njk',
      'templates/auth/login.njk'].map(read).join('\n');
    for (const id of modalIds) {
      const line = all.split('\n').find((l) => l.includes(`id="${id}"`));
      expect(line, id).toBeDefined();
      expect(line).toContain('app-modal-backdrop');
    }
  });

  it('the permissions custom confirm is gone; showConfirm delegates to appConfirm', () => {
    const t = read('templates/dashboard/permissions.njk');
    expect(t).not.toContain('id="confirmModal"');
    expect(t).not.toContain('id="confirmOk"');
    expect(t).toContain('return window.appConfirm(body, { title });');
  });

  it('shared confirm/prompt/message gain a close X; prompt backdrop cancels', () => {
    const js = read('static/js/notifications.js');
    expect(js).toContain('class="app-confirm-close"');
    expect(js).toContain('app-confirm-submit--destructive');
    expect(js).toContain('overlay.querySelector(".app-confirm-close").addEventListener("click", () => finish(false))');
    expect(js).toContain('overlay.querySelector(".app-confirm-close").addEventListener("click", () => finish(null))');
    expect(js).toContain('closeX.addEventListener("click", finish)');
    const css = read('static/css/notifications.css');
    expect(css).toContain('.app-confirm-close');
    expect(css).toContain('.app-confirm-submit--destructive');
  });

  it('destructive confirms carry the destructive flag; restores and logouts do not', () => {
    const destructive = [
      ['templates/dashboard/users.njk', 'Disable user?'],
      ['templates/dashboard/users.njk', 'Delete user?'],
      ['templates/dashboard/departments.njk', 'Delete department?'],
      ['templates/dashboard/departments.njk', 'Delete department permanently?'],
      ['templates/dashboard/departments.njk', 'Remove employee?'],
      ['static/js/superadmin-kpi.js', 'Delete KPI record?'],
      ['templates/dashboard/tasks.njk', 'Remove attachment?'],
      ['templates/dashboard/target.njk', 'Delete target?'],
      ['templates/dashboard/kpi_periods.njk', 'Delete draft period?'],
      ['templates/dashboard/super_admin_management.njk', 'Delete account?'],
      ['static/js/departed-records.js', 'Revoke grant?'],
      ['templates/dashboard/positions.njk', 'Delete position?'],
      ['templates/dashboard/system_settings.njk', 'Turn on audit log deletion?'],
    ];
    for (const [file, title] of destructive) {
      const line = read(file).split('\n').find((l) => l.includes(title));
      expect(line, `${file} ${title}`).toBeDefined();
      expect(line).toContain('destructive: true');
    }
    const users = read('templates/dashboard/users.njk');
    const restore = users.split('\n').find((l) => l.includes('Restore user?'));
    expect(restore).not.toContain('destructive');
    const base = read('templates/layouts/base.njk');
    const logout = base.split('\n').find((l) => l.includes('appConfirm'));
    expect(logout).not.toContain('destructive');
  });

  it('tasks no longer has its own Escape listener; SharedDialog owns Escape', () => {
    const t = read('templates/dashboard/tasks.njk');
    expect(t).not.toContain('if (event.key === "Escape") closeModals();');
    expect(t).toContain('Escape is owned by SharedDialog');
  });

  it('content and gates of the migrated permissions confirms are unchanged', () => {
    const t = read('templates/dashboard/permissions.njk');
    expect(t).toContain('Save Role Permissions?');
    expect(t).toContain('Save User Overrides?');
    expect(t).toContain('Reset to Role Defaults?');
    expect(t).toContain('Unsaved Changes');
  });
});
