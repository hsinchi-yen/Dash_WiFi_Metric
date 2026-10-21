// DB Maintenance (DB Tweak): browse, export and delete records — administrator session only.
// /api/db-tweak/* with the session token (no PostgreSQL credentials in the browser any more).
// 5D (Q19): every delete first shows what it removes (/impact: rows, units, web batches, FAIL classifications,
// dispositions, RD triage, each work order's units and yield before → after); deleting a work order also asks for its
// number to be typed. Records export as CSV; web upload batches can be undone here too (one batch = its own rows).
import { apiDelete, apiGet, download } from '../core/api.js';
import * as auth from '../core/auth.js';
import { esc, fmtDateTime, fmtInt, fmtNum } from '../core/format.js';
import { t } from '../core/i18n.js';
import { $, $$, openFormModal, tableState, toast } from '../core/ui.js';

const COLS = 13;
let seq = 0;
let appRef;
let workOrders = [];
let tab = 'records';

function renderAccess() {
    const signedIn = auth.isSignedIn();
    const admin = auth.isAdmin();
    $('#twLogin').hidden = signedIn;
    $('#twDenied').hidden = !signedIn || admin;
    $('#twPanel').hidden = !admin;
}

function fill(select, items, value, allLabel) {
    select.innerHTML = '';
    select.appendChild(new Option(allLabel, 'ALL'));
    items.forEach(v => select.appendChild(new Option(v, v)));
    select.value = items.includes(value) ? value : 'ALL';
}

async function loadFilters() {
    const pm = $('#twProduct').value || 'ALL';
    const [wos, products] = await Promise.all([
        apiGet(`/api/db-tweak/workorders?product_model=${encodeURIComponent(pm)}`),
        apiGet('/api/db-tweak/products'),
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
        const p = new URLSearchParams({ work_order: $('#twWo').value, product_model: $('#twProduct').value, limit: $('#twLimit').value });
        const data = await apiGet(`/api/db-tweak/records?${p}`);
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
            <td class="nowrap muted" title="${esc(r.upload_id || '')}">${r.upload_id ? `<code>${esc(r.upload_id.slice(0, 8))}</code>` : '—'}</td>
            <td class="nowrap"><details class="row-menu"><summary aria-label="${esc(t('tw.actions'))}">⋯</summary><div class="row-menu-list">
                <button class="btn small" type="button" data-log="${r.id}">${esc(t('fails.download'))}</button>
                <button class="btn small danger" type="button" data-del="${r.id}" data-sn="${esc(r.sn)}">${esc(t('tw.deleteOne'))}</button>
            </div></details></td></tr>`).join('');
    } catch (err) {
        if (mine !== seq) return;
        tableState(tbody, COLS, 'error', t('common.loadFailedMsg', { error: err.message }), loadRecords);
    }
}

/* ─── Impact preview: what a delete removes ─── */
function impactText(im) {
    const lines = [t('tw.im.rows', { rows: fmtInt(im.rows), units: fmtInt(im.units), fail: fmtInt(im.fail_rows) })];
    if (im.first_test) lines.push(t('tw.im.period', { from: fmtDateTime(im.first_test), to: fmtDateTime(im.last_test) }));
    if (im.web_rows) lines.push(t('tw.im.web', { n: fmtInt(im.web_rows), batches: fmtInt(im.batches.length) }));
    if (im.fail_analyses || im.dispositions || im.rd_triaged) {
        lines.push(t('tw.im.side', { fa: fmtInt(im.fail_analyses), disp: fmtInt(im.dispositions), rd: fmtInt(im.rd_triaged) }));
    }
    for (const w of im.work_orders) {
        const y0 = w.yield_before != null ? fmtNum(w.yield_before, 2) : '—';
        lines.push(w.units_after ? t('tw.im.wo', { wo: w.work_order, u0: fmtInt(w.units_before), y0, u1: fmtInt(w.units_after), y1: fmtNum(w.yield_after, 2) })
            : t('tw.im.woGone', { wo: w.work_order, u0: fmtInt(w.units_before), y0 }));
        if (w.planned_qty && !w.units_after) lines.push(t('tw.im.plan', { wo: w.work_order, qty: fmtInt(w.planned_qty) }));
    }
    return lines.join('\n');
}

async function confirmDelete({ query, title, typeToConfirm, submit, run }) {
    let im;
    try { im = await apiGet(`/api/db-tweak/impact?${new URLSearchParams(query)}`); } catch (err) { toast(err.message, 'bad'); return; }
    openFormModal({
        title, desc: impactText(im),
        fields: typeToConfirm ? [{ name: 'confirm', label: t('tw.typeToConfirm', { text: typeToConfirm }), required: true }] : [],
        submitLabel: submit, danger: true,
        onSubmit: async v => {
            if (typeToConfirm && v.confirm.trim() !== typeToConfirm) return t('tw.typeMismatch', { text: typeToConfirm });
            try { await run(); } catch (err) { return err.message; }
            return null;
        },
    });
}

/* ─── Upload batches ─── */
async function loadBatches() {
    const tbody = $('#twBatchRows');
    tableState(tbody, 8, 'loading');
    try {
        const data = await apiGet('/api/log-upload/history?limit=100');
        const rows = data.items.filter(r => r.status !== 'analyzed');
        if (!rows.length) { tableState(tbody, 8, 'empty', t('lu.none')); return; }
        tbody.innerHTML = rows.map(r => {
            const can = ['done', 'failed', 'cancelled'].includes(r.status) && r.uploaded > 0;
            return `<tr><td class="nowrap">${esc(fmtDateTime(r.created_at))}</td><td class="nowrap">${esc(r.display_name || r.username || '—')}</td>
                <td class="nowrap">${esc(r.work_order || '—')}</td><td class="nowrap">${esc(r.product_model || '—')}</td>
                <td class="truncate" title="${esc(r.source_name || '')}">${esc(r.source_name || '—')}</td>
                <td class="nowrap num">${r.uploaded != null ? esc(t('lu.histResult', { n: fmtInt(r.uploaded), s: fmtInt(r.skipped), f: fmtInt(r.failed) })) : '—'}</td>
                <td><span class="badge ${r.status === 'done' ? 'done' : r.status === 'reverted' ? 'reverted' : 'warn'}">${esc(t(`lu.status.${r.status}`))}</span></td>
                <td>${can ? `<button class="btn small danger" type="button" data-batch="${esc(r.id)}" data-wo="${esc(r.work_order || '')}">${esc(t('lu.revert'))}</button>` : ''}</td></tr>`;
        }).join('');
    } catch (err) {
        tableState(tbody, 8, 'error', t('common.loadFailedMsg', { error: err.message }), loadBatches);
    }
}

function showTab(next) {
    tab = next;
    $$('[data-tw-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.twTab === tab)));
    $$('[data-tw-view]').forEach(v => { v.hidden = v.dataset.twView !== tab; });
    if (tab === 'batches') loadBatches();
}

async function load() {
    renderAccess();
    if (!auth.isAdmin()) return;
    try {
        await loadFilters();
    } catch (err) {
        toast(err.message, 'bad');
        return;
    }
    await loadRecords();
    if (tab === 'batches') loadBatches();
}

function init(app) {
    appRef = app;
    auth.bindLoginForm({ user: $('#twUser'), pass: $('#twPass'), button: $('#btnTwLogin'), message: $('#twLoginMsg') });
    $('#twProduct').addEventListener('change', async () => { await loadFilters().catch(e => toast(e.message, 'bad')); loadRecords(); });
    $('#twWo').addEventListener('change', loadRecords);
    $('#twLimit').addEventListener('change', loadRecords);
    $('#btnTwReload').addEventListener('click', load);
    $$('[data-tw-tab]').forEach(b => b.addEventListener('click', () => showTab(b.dataset.twTab)));
    $('#btnTwBatchesReload').addEventListener('click', loadBatches);
    $('#btnTwExport').addEventListener('click', async () => {
        const p = new URLSearchParams({ work_order: $('#twWo').value, product_model: $('#twProduct').value });
        try { await download(`/api/db-tweak/export.csv?${p}`, 'test_record.csv'); } catch (err) { toast(err.message, 'bad'); }
    });
    $('#twRows').addEventListener('click', async e => {
        const logBtn = e.target.closest('[data-log]');
        if (logBtn) {
            logBtn.closest('details')?.removeAttribute('open');
            try { await download(`/api/db-tweak/records/${logBtn.dataset.log}/log`, `record_${logBtn.dataset.log}.txt`); } catch (err) { toast(err.message, 'bad'); }
            return;
        }
        const delBtn = e.target.closest('[data-del]');
        if (!delBtn) return;
        delBtn.closest('details')?.removeAttribute('open');
        const id = delBtn.dataset.del;
        await confirmDelete({
            query: { record_id: id }, title: t('tw.deleteOneConfirm', { id, sn: delBtn.dataset.sn }), submit: t('tw.deleteOne'),
            run: async () => {
                const res = await apiDelete(`/api/db-tweak/records/${id}`);
                toast(t('tw.deleted', { n: fmtInt(res.deleted) }));
                await Promise.all([loadRecords(), appRef.refreshOptions()]);
            },
        });
    });
    $('#btnTwDeleteWo').addEventListener('click', async () => {
        const wo = $('#twWo').value;
        if (!wo || wo === 'ALL') { toast(t('tw.deleteWoNeed'), 'bad'); return; }
        await confirmDelete({
            query: { work_order: wo }, title: t('tw.deleteWoTitle', { wo }), typeToConfirm: wo, submit: t('tw.deleteWo'),
            run: async () => {
                const res = await apiDelete(`/api/db-tweak/workorders/${encodeURIComponent(wo)}`);
                toast(t('tw.deleted', { n: fmtInt(res.deleted) }));
                $('#twWo').value = 'ALL';
                await load();
                await appRef.refreshOptions();
            },
        });
    });
    $('#twBatchRows').addEventListener('click', async e => {
        const btn = e.target.closest('[data-batch]');
        if (!btn) return;
        await confirmDelete({
            query: { upload_id: btn.dataset.batch }, title: t('tw.revertTitle', { wo: btn.dataset.wo || '—' }), submit: t('lu.revert'),
            run: async () => {
                const res = await apiDelete(`/api/log-upload/${btn.dataset.batch}`);
                toast(t('lu.reverted', { n: fmtInt(res.reverted) }));
                await Promise.all([loadBatches(), loadRecords(), appRef.refreshOptions()]);
            },
        });
    });
}

export default {
    id: 'dbtweak', scoped: false, init, load,
    onAuthChange: () => { if (appRef?.page?.id === 'dbtweak') load(); else renderAccess(); },
};
