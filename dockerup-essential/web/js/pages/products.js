// Product registry: the names the dashboard accepts. Signed-in users see the list and can add a
// product (R4: a well-formed new name whose TN number is unused); the administrator retires one.
// "Check a name" shows how a typed name resolves — the same matching the uploader and the AI use.
import { apiGet, apiPatch, apiPost } from '../core/api.js';
import * as auth from '../core/auth.js';
import { esc, fmtDateTime, fmtInt } from '../core/format.js';
import { t } from '../core/i18n.js';
import { $, debounce, openFormModal, tableState, toast } from '../core/ui.js';

const COLS = 9;
let appRef;
let items = [];
let checkSeq = 0;

function renderAccess() {
    const signedIn = auth.isSignedIn();
    $('#prodLogin').hidden = signedIn;
    $('#prodPanel').hidden = !signedIn;
}

function sourceLabel(src) {
    const key = `prod.source.${src}`;
    return src && t(key) !== key ? t(key) : (src || '—');
}

async function loadList() {
    const tbody = $('#prodRows');
    tableState(tbody, COLS, 'loading');
    try {
        const data = await apiGet('/api/products/list');
        items = data.items;
        if (!items.length) { tableState(tbody, COLS, 'empty', t('prod.empty')); return; }
        const admin = auth.isAdmin();
        tbody.innerHTML = items.map(p => `<tr class="${p.status === 'retired' ? 'muted' : ''}">
            <td class="nowrap"><strong>${esc(p.canonical)}</strong>${(p.aliases || []).length ? `<div class="small-text muted">${esc(p.aliases.join(', '))}</div>` : ''}</td>
            <td class="num">${p.tn_number ?? '—'}</td>
            <td class="num">${fmtInt(p.units)}</td>
            <td class="num">${fmtInt(p.work_orders)}</td>
            <td class="nowrap">${p.last_test ? esc(fmtDateTime(p.last_test)) : '—'}</td>
            <td><span class="badge ${p.status === 'active' ? 'good' : 'muted'}">${esc(p.status === 'active' ? t('prod.active') : t('prod.retired'))}</span></td>
            <td class="nowrap small-text">${esc(sourceLabel(p.source))}${p.created_by && p.source !== 'seed' ? ` · ${esc(p.created_by)}` : ''}</td>
            <td class="small-text">${esc(p.note || '—')}</td>
            <td class="nowrap">${admin ? `
                <button class="btn small" type="button" data-op="note" data-id="${p.id}">${esc(t('prod.editNote'))}</button>
                <button class="btn small" type="button" data-op="${p.status === 'active' ? 'retire' : 'activate'}" data-id="${p.id}">${esc(p.status === 'active' ? t('prod.retire') : t('prod.activate'))}</button>` : ''}
            </td></tr>`).join('');
    } catch (err) {
        tableState(tbody, COLS, 'error', t('common.loadFailedMsg', { error: err.message }), loadList);
    }
}

async function checkName() {
    const text = $('#prodCheck').value.trim();
    const out = $('#prodCheckResult');
    const mine = ++checkSeq;
    if (!text) { out.textContent = ''; out.className = 'status-line'; return; }
    try {
        const r = await apiPost('/api/products/resolve', { text });
        if (mine !== checkSeq) return;
        const names = (r.same_tn || r.candidates || []).join(', ');
        const map = {
            known: ['ok', t('prod.res.known', { name: r.canonical })],
            new: ['', t('prod.res.new', { name: r.proposal })],
            blocked_same_tn: ['error', t('prod.res.blocked_same_tn', { names })],
            ambiguous: ['', t('prod.res.ambiguous', { names })],
            bad_format: ['error', t('prod.res.bad_format')],
        };
        const [kind, msg] = map[r.status] || ['', r.status];
        out.textContent = msg;
        out.className = `status-line ${kind}`;
    } catch (err) {
        out.textContent = err.message;
        out.className = 'status-line error';
    }
}

function newProduct() {
    openFormModal({
        title: t('prod.newTitle'), desc: t('prod.newDesc'),
        fields: [
            { name: 'name', label: t('prod.f.name'), required: true, maxlength: 120, hint: t('lu.productNewPh') },
            { name: 'note', label: t('prod.f.note'), maxlength: 200 },
        ],
        submitLabel: t('prod.create'),
        onSubmit: async v => {
            let res;
            try { res = await apiPost('/api/products', { name: v.name, note: v.note }); } catch (err) { return err.message; }
            toast(t('prod.added', { name: res.canonical }));
            loadList();
            appRef.refreshOptions();
            return null;
        },
    });
}

async function onAction(e) {
    const btn = e.target.closest('[data-op]');
    if (!btn) return;
    const p = items.find(x => String(x.id) === btn.dataset.id);
    if (!p) return;
    if (btn.dataset.op === 'note') {
        openFormModal({
            title: t('prod.noteTitle', { name: p.canonical }),
            fields: [{ name: 'note', label: t('prod.f.note'), value: p.note || '', maxlength: 200 }],
            submitLabel: t('common.save'),
            onSubmit: async v => {
                try { await apiPatch(`/api/products/${p.id}`, { note: v.note }); } catch (err) { return err.message; }
                loadList();
                return null;
            },
        });
        return;
    }
    const retire = btn.dataset.op === 'retire';
    if (retire && !confirm(t('prod.retireConfirm', { name: p.canonical }))) return;
    try {
        await apiPatch(`/api/products/${p.id}`, { status: retire ? 'retired' : 'active' });
        loadList();
    } catch (err) { toast(err.message, 'bad'); }
}

function init(app) {
    appRef = app;
    auth.bindLoginForm({ user: $('#prodUser'), pass: $('#prodPass'), button: $('#btnProdLogin'), message: $('#prodLoginMsg') });
    $('#btnProdNew').addEventListener('click', newProduct);
    $('#prodRows').addEventListener('click', onAction);
    $('#prodCheck').addEventListener('input', debounce(checkName, 250));
    $('#btnProdReload').addEventListener('click', loadList);
}

async function load() {
    renderAccess();
    if (auth.isSignedIn()) await loadList();
}

export default {
    id: 'products', scoped: false, init, load,
    onAuthChange: () => { renderAccess(); if (appRef?.page?.id === 'products' && auth.isSignedIn()) loadList(); },
    onLangChange: () => { if ($('#prodCheck').value.trim()) checkName(); },
};
