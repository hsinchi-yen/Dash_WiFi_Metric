// DB Maintenance (DB Tweak): browse and delete records with the user's own PostgreSQL login.
// Same API as the previous dashboard (/api/db-tweak/*, credentials in the request body); the
// account system will replace the login with roles.
import { apiPost } from '../core/api.js';
import * as auth from '../core/auth.js';
import { esc, fmtDateTime, fmtInt, fmtNum } from '../core/format.js';
import { t } from '../core/i18n.js';
import { $, tableState, toast } from '../core/ui.js';

const COLS = 12;
let seq = 0;
let appRef;
let workOrders = [];

function body(extra = {}) {
    return { ...auth.credentials(), ...extra };
}

async function call(path, extra) {
    try {
        return await apiPost(path, body(extra));
    } catch (err) {
        if (err.status === 401 && auth.isSignedIn()) auth.expire();
        throw err;
    }
}

function renderAccess() {
    const signedIn = auth.isSignedIn();
    $('#twLogin').hidden = signedIn;
    $('#twPanel').hidden = !signedIn;
}

function fill(select, items, value, allLabel) {
    select.innerHTML = '';
    select.appendChild(new Option(allLabel, 'ALL'));
    items.forEach(v => select.appendChild(new Option(v, v)));
    select.value = items.includes(value) ? value : 'ALL';
}

async function loadFilters() {
    const [wos, products] = await Promise.all([
        call('/api/db-tweak/workorders', { product_model: $('#twProduct').value || 'ALL' }),
        call('/api/db-tweak/products'),
    ]);
    workOrders = wos.items;
    fill($('#twProduct'), products.items.map(p => p.product_model), $('#twProduct').value, t('filter.allProducts'));
    fill($('#twWo'), workOrders.map(w => w.work_order), $('#twWo').value, t('filter.allWo'));
}

function resultBadge(v) {
    if (v === true) return '<span class="badge pass">PASS</span>';
    if (v === false) return '<span class="badge fail">FAIL</span>';
    return '<span class="muted">—</span>';
}

async function loadRecords() {
    const mine = ++seq;
    const tbody = $('#twRows');
    tableState(tbody, COLS, 'loading');
    try {
        const data = await call('/api/db-tweak/records', {
            work_order: $('#twWo').value, product_model: $('#twProduct').value, limit: Number($('#twLimit').value),
        });
        if (mine !== seq) return;
        $('#twCount').textContent = t('tw.count', { n: fmtInt(data.count) });
        if (!data.items.length) { tableState(tbody, COLS, 'empty'); return; }
        tbody.innerHTML = data.items.map(r => `<tr>
            <td class="num">${r.id}</td>
            <td class="nowrap">${esc(fmtDateTime(r.test_time))}</td>
            <td class="nowrap">${esc(r.work_order || '—')}</td>
            <td class="nowrap">${esc(r.product_model || '—')}</td>
            <td><code>${esc(r.sn)}</code></td>
            <td>${resultBadge(r.result)}</td>
            <td>${resultBadge(r.wifi_result)}</td>
            <td>${resultBadge(r.bt_result)}</td>
            <td class="num">${fmtNum(r.avg_24g, 1)}</td>
            <td class="num">${fmtNum(r.avg_5g, 1)}</td>
            <td class="truncate" title="${esc(r.source_file || '')}">${esc(r.source_file || '—')}</td>
            <td class="nowrap">
                <button class="btn small" type="button" data-log="${r.id}">${esc(t('fails.download'))}</button>
                <button class="btn small" type="button" data-del="${r.id}" data-sn="${esc(r.sn)}">${esc(t('tw.deleteOne'))}</button>
            </td></tr>`).join('');
    } catch (err) {
        if (mine !== seq) return;
        tableState(tbody, COLS, 'error', t('common.loadFailedMsg', { error: err.message }), loadRecords);
    }
}

async function downloadLog(id) {
    const res = await fetch('/api/db-tweak/download-record-log', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body({ id: Number(id) })),
    });
    if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try { const d = await res.json(); msg = d.error_en && document.documentElement.lang === 'en' ? d.error_en : (d.error || d.detail || msg); } catch { /* not JSON */ }
        throw new Error(msg);
    }
    const blob = await res.blob();
    const m = /filename="?([^";]+)"?/.exec(res.headers.get('Content-Disposition') || '');
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = m ? m[1] : `record_${id}.txt`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function load() {
    renderAccess();
    if (!auth.isSignedIn()) return;
    try {
        await loadFilters();
    } catch (err) {
        toast(err.message, 'bad');
        return;
    }
    await loadRecords();
}

function init(app) {
    appRef = app;
    auth.bindLoginForm({ user: $('#twUser'), pass: $('#twPass'), button: $('#btnTwLogin'), message: $('#twLoginMsg') });
    $('#twProduct').addEventListener('change', async () => { await loadFilters().catch(e => toast(e.message, 'bad')); loadRecords(); });
    $('#twWo').addEventListener('change', loadRecords);
    $('#twLimit').addEventListener('change', loadRecords);
    $('#btnTwReload').addEventListener('click', load);
    $('#twRows').addEventListener('click', async e => {
        const logBtn = e.target.closest('[data-log]');
        if (logBtn) {
            try { await downloadLog(logBtn.dataset.log); } catch (err) { toast(err.message, 'bad'); }
            return;
        }
        const delBtn = e.target.closest('[data-del]');
        if (!delBtn || !confirm(t('tw.deleteOneConfirm', { id: delBtn.dataset.del, sn: delBtn.dataset.sn }))) return;
        try {
            const res = await call('/api/db-tweak/delete-record', { id: Number(delBtn.dataset.del) });
            toast(t('tw.deleted', { n: fmtInt(res.deleted) }));
            await Promise.all([loadRecords(), appRef.refreshOptions()]);
        } catch (err) { toast(err.message, 'bad'); }
    });
    $('#btnTwDeleteWo').addEventListener('click', async () => {
        const wo = $('#twWo').value;
        if (!wo || wo === 'ALL') { toast(t('tw.deleteWoNeed'), 'bad'); return; }
        const row = workOrders.find(w => w.work_order === wo);
        if (!confirm(t('tw.deleteWoConfirm', { wo, n: fmtInt(row?.total || 0) }))) return;
        try {
            const res = await call('/api/db-tweak/delete-workorder', { work_order: wo });
            toast(t('tw.deleted', { n: fmtInt(res.deleted) }));
            $('#twWo').value = 'ALL';
            await load();
            await appRef.refreshOptions();
        } catch (err) { toast(err.message, 'bad'); }
    });
}

export default {
    id: 'dbtweak', scoped: false, init, load,
    onAuthChange: () => { if (appRef?.page?.id === 'dbtweak') load(); else renderAccess(); },
};
