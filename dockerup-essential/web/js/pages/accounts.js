// Accounts (administrator only): uploader accounts, change audit (accounts + products + deletes), login log.
// Uploader accounts are disabled, never deleted, so their history stays attributable.
import { apiGet, apiPatch, apiPost } from '../core/api.js';
import * as auth from '../core/auth.js';
import { esc, fmtDateTime, fmtInt } from '../core/format.js';
import { t } from '../core/i18n.js';
import { $, $$, openFormModal, tableState } from '../core/ui.js';
import logupload from './logupload.js';

const PAGE = 50;
const st = { tab: 'users', users: [], admin: null, auditOffset: 0, loginOffset: 0 };
let appRef;

function renderAccess() {
    const admin = auth.isAdmin();
    $('#acctDenied').hidden = admin;
    $('#acctPanel').hidden = !admin;
}

const cell = (text, cls = '') => `<td${cls ? ` class="${cls}"` : ''}>${esc(text == null || text === '' ? '—' : text)}</td>`;

function fillUserSelects() {
    const options = [[st.admin.username, `${st.admin.username}${t('lu.adminRole')}`]]
        .concat(st.users.map(u => [u.username, auth.displayName(u)]));
    for (const id of ['#acctAuditUser', '#acctLoginUser']) {
        const sel = $(id);
        const current = sel.value;
        sel.innerHTML = '';
        sel.appendChild(new Option(t('common.all'), ''));
        options.forEach(([v, l]) => sel.appendChild(new Option(l, v)));
        sel.value = options.some(([v]) => v === current) ? current : '';
    }
}

async function loadUsers() {
    const tbody = $('#acctUserRows');
    try {
        const data = await apiGet('/api/admin/users');
        st.users = data.items;
        st.admin = data.admin;
        fillUserSelects();
        const a = data.admin;
        const adminRow = `<tr>${cell(a.username)}<td colspan="3" class="muted">${esc(t('acct.builtin'))}</td>
            <td><span class="badge good">${esc(t('acct.stateAdmin'))}</span></td>
            ${cell(fmtInt(a.uploads), 'num')}${cell(fmtInt(a.uploaded_rows), 'num')}${cell(fmtInt(a.reverted_batches), 'num')}
            ${cell(a.last_upload_at ? fmtDateTime(a.last_upload_at) : '', 'nowrap')}${cell(a.last_login_at ? fmtDateTime(a.last_login_at) : '', 'nowrap')}
            <td class="nowrap"><button class="btn small" type="button" data-op="history" data-user="${esc(a.username)}">${esc(t('acct.history'))}</button></td></tr>`;
        const rows = data.items.map(u => `<tr>
            ${cell(u.username)}${cell(u.display_name)}${cell(u.department)}${cell(u.email)}
            <td><span class="badge ${u.enabled ? 'good' : 'muted'}">${esc(u.enabled ? t('acct.stateOn') : t('acct.stateOff'))}</span></td>
            ${cell(fmtInt(u.uploads), 'num')}${cell(fmtInt(u.uploaded_rows), 'num')}${cell(fmtInt(u.reverted_batches), 'num')}
            ${cell(u.last_upload_at ? fmtDateTime(u.last_upload_at) : '', 'nowrap')}${cell(u.last_login_at ? fmtDateTime(u.last_login_at) : '', 'nowrap')}
            <td class="nowrap">
                <button class="btn small" type="button" data-op="edit" data-user="${esc(u.username)}">${esc(t('acct.edit'))}</button>
                <button class="btn small" type="button" data-op="reset" data-user="${esc(u.username)}">${esc(t('acct.reset'))}</button>
                <button class="btn small" type="button" data-op="${u.enabled ? 'disable' : 'enable'}" data-user="${esc(u.username)}">${esc(u.enabled ? t('acct.disable') : t('acct.enable'))}</button>
                <button class="btn small" type="button" data-op="history" data-user="${esc(u.username)}">${esc(t('acct.history'))}</button>
            </td></tr>`).join('');
        tbody.innerHTML = adminRow + (rows || `<tr><td colspan="11" class="muted">${esc(t('acct.empty'))}</td></tr>`);
    } catch (err) {
        tableState(tbody, 11, 'error', t('common.loadFailedMsg', { error: err.message }), loadUsers);
    }
}

function rangePager(el, data, onPage) {
    el.innerHTML = '';
    if (!data.total) return;
    const end = Math.min(data.offset + data.limit, data.total);
    const info = document.createElement('span');
    info.textContent = t('common.rangeInfo', { from: fmtInt(data.offset + 1), to: fmtInt(end), total: fmtInt(data.total) });
    const prev = Object.assign(document.createElement('button'), { className: 'btn small', textContent: t('common.prev'), disabled: data.offset <= 0 });
    const next = Object.assign(document.createElement('button'), { className: 'btn small', textContent: t('common.next'), disabled: end >= data.total });
    prev.addEventListener('click', () => onPage(Math.max(0, data.offset - PAGE)));
    next.addEventListener('click', () => onPage(data.offset + PAGE));
    el.append(info, prev, next);
}

function auditDetail(r) {
    const d = r.detail || {};
    const label = k => ({ display_name: t('acct.f.name'), department: t('acct.f.dept'), email: t('acct.f.email') }[k] || k);
    if (r.action === 'update' && d.before) {
        return Object.keys(d.after || {}).map(k => t('acct.change', { field: label(k), before: d.before[k] || t('acct.blank'), after: d.after[k] || t('acct.blank') }))
            .join(t('common.itemSep'));
    }
    if (r.action === 'create') return Object.entries(d).filter(([, v]) => v).map(([k, v]) => t('acct.value', { field: label(k), value: v })).join(t('common.itemSep'));
    return Object.entries(d).filter(([, v]) => v != null && v !== '').map(([k, v]) => t('acct.value', { field: k, value: Array.isArray(v) ? v.join(', ') : String(v) })).join(t('common.itemSep'));
}

const actionLabel = a => (t(`acct.action.${a}`) === `acct.action.${a}` ? a : t(`acct.action.${a}`));

async function loadAudit() {
    const tbody = $('#acctAuditRows');
    const p = new URLSearchParams({ limit: PAGE, offset: st.auditOffset });
    if ($('#acctAuditUser').value) p.set('username', $('#acctAuditUser').value);
    try {
        if (!st.admin) await loadUsers();
        const data = await apiGet(`/api/admin/audit?${p}`);
        tbody.innerHTML = data.items.map(r => `<tr>${cell(fmtDateTime(r.at), 'nowrap')}${cell(r.actor)}
            ${cell(actionLabel(r.action))}${cell(r.target)}<td class="small-text">${esc(auditDetail(r) || '—')}</td>${cell(r.client_ip)}</tr>`).join('')
            || `<tr><td colspan="6" class="muted">${esc(t('acct.empty2'))}</td></tr>`;
        rangePager($('#acctAuditPager'), data, off => { st.auditOffset = off; loadAudit(); });
    } catch (err) {
        tableState(tbody, 6, 'error', t('common.loadFailedMsg', { error: err.message }), loadAudit);
    }
}

async function loadLogins() {
    const tbody = $('#acctLoginRows');
    const p = new URLSearchParams({ limit: PAGE, offset: st.loginOffset });
    if ($('#acctLoginUser').value) p.set('username', $('#acctLoginUser').value);
    if ($('#acctLoginResult').value) p.set('success', $('#acctLoginResult').value);
    try {
        if (!st.admin) await loadUsers();
        const data = await apiGet(`/api/admin/login-log?${p}`);
        tbody.innerHTML = data.items.map(r => `<tr>${cell(fmtDateTime(r.at), 'nowrap')}${cell(r.username)}
            ${cell(t(`acct.event.${r.event}`))}
            <td><span class="badge ${r.success ? 'good' : 'bad'}">${esc(r.success ? t('common.success') : t('common.failed'))}</span></td>
            ${cell(r.reason ? t(`acct.reason.${r.reason}`) : '')}${cell(r.client_ip)}
            <td class="truncate muted" title="${esc(r.user_agent || '')}">${esc(r.user_agent || '—')}</td></tr>`).join('')
            || `<tr><td colspan="7" class="muted">${esc(t('acct.empty2'))}</td></tr>`;
        rangePager($('#acctLoginPager'), data, off => { st.loginOffset = off; loadLogins(); });
    } catch (err) {
        tableState(tbody, 7, 'error', t('common.loadFailedMsg', { error: err.message }), loadLogins);
    }
}

function loadTab() {
    if (st.tab === 'audit') return loadAudit();
    if (st.tab === 'logins') return loadLogins();
    return loadUsers();
}

function newUser() {
    openFormModal({
        title: t('acct.newTitle'), desc: t('acct.newDesc'),
        fields: [
            { name: 'username', label: t('acct.f.user'), required: true, maxlength: 32, hint: t('acct.f.userHint') },
            { name: 'display_name', label: t('acct.f.name'), required: true, maxlength: 50 },
            { name: 'department', label: t('acct.f.dept'), maxlength: 50 },
            { name: 'email', label: t('acct.f.email'), type: 'email', maxlength: 120 },
            { name: 'password', label: t('acct.f.pw'), type: 'password', required: true, autocomplete: 'new-password', hint: t('acct.f.pwHint') },
            { name: 'password2', label: t('acct.f.pw2'), type: 'password', required: true, autocomplete: 'new-password' },
        ],
        submitLabel: t('acct.create'),
        onSubmit: async v => {
            if (v.password !== v.password2) return t('acct.pwMismatch');
            try {
                await apiPost('/api/admin/users', { username: v.username, display_name: v.display_name, department: v.department, email: v.email, password: v.password });
            } catch (err) { return err.message; }
            loadUsers();
            return null;
        },
    });
}

async function onUserAction(e) {
    const btn = e.target.closest('[data-op]');
    if (!btn) return;
    const username = btn.dataset.user;
    const user = st.users.find(u => u.username === username);
    const path = `/api/admin/users/${encodeURIComponent(username)}`;
    const op = btn.dataset.op;
    if (op === 'history') {
        logupload.showHistoryFor(username);
        location.hash = 'logupload';
    } else if (op === 'edit' && user) {
        openFormModal({
            title: t('acct.editTitle', { user: username }),
            fields: [
                { name: 'display_name', label: t('acct.f.name'), required: true, value: user.display_name, maxlength: 50 },
                { name: 'department', label: t('acct.f.dept'), value: user.department, maxlength: 50 },
                { name: 'email', label: t('acct.f.email'), type: 'email', value: user.email, maxlength: 120 },
            ],
            submitLabel: t('common.save'),
            onSubmit: async v => {
                try { await apiPatch(path, v); } catch (err) { return err.message; }
                loadUsers();
                return null;
            },
        });
    } else if (op === 'reset') {
        openFormModal({
            title: t('acct.resetTitle', { user: username }), desc: t('acct.resetDesc'),
            fields: [
                { name: 'password', label: t('auth.newPw'), type: 'password', required: true, autocomplete: 'new-password', hint: t('auth.pwHint') },
                { name: 'password2', label: t('auth.newPw2'), type: 'password', required: true, autocomplete: 'new-password' },
            ],
            submitLabel: t('acct.reset'),
            onSubmit: async v => {
                if (v.password !== v.password2) return t('acct.pwMismatch');
                try { await apiPost(`${path}/reset-password`, { password: v.password }); } catch (err) { return err.message; }
                alert(t('acct.resetDone', { user: username }));
                return null;
            },
        });
    } else if (op === 'disable' || op === 'enable') {
        const enable = op === 'enable';
        if (!confirm(t(enable ? 'acct.enableConfirm' : 'acct.disableConfirm', { user: username }))) return;
        try { await apiPatch(path, { enabled: enable }); } catch (err) { alert(err.message); }
        loadUsers();
    }
}

function init(app) {
    appRef = app;
    $$('[data-acct-tab]').forEach(b => b.addEventListener('click', () => {
        st.tab = b.dataset.acctTab;
        $$('[data-acct-tab]').forEach(x => x.setAttribute('aria-selected', String(x === b)));
        $$('[data-acct-view]').forEach(v => { v.hidden = v.dataset.acctView !== st.tab; });
        loadTab();
    }));
    $('#btnAcctNew').addEventListener('click', newUser);
    $('#acctUserRows').addEventListener('click', onUserAction);
    $('#acctAuditUser').addEventListener('change', () => { st.auditOffset = 0; loadAudit(); });
    $('#acctLoginUser').addEventListener('change', () => { st.loginOffset = 0; loadLogins(); });
    $('#acctLoginResult').addEventListener('change', () => { st.loginOffset = 0; loadLogins(); });
}

async function load() {
    renderAccess();
    if (auth.isAdmin()) await loadTab();
}

export default {
    id: 'accounts', scoped: false, adminOnly: true, init, load,
    onAuthChange: () => { renderAccess(); if (appRef?.page?.id === 'accounts' && auth.isAdmin()) loadTab(); },
};
