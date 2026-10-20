// Work Orders: one row per work order with yield colour-coded by the shared thresholds.
import { apiGet } from '../core/api.js';
import { esc, fmtDateTime, fmtInt, fmtNum, fmtPct, yieldLevel } from '../core/format.js';
import { t } from '../core/i18n.js';
import { queryString } from '../core/scope.js';
import { $, debounce, tableState } from '../core/ui.js';

const COLS = 13;
let rows = [];
let seq = 0;
let appRef;

/** "1.5 h" / "2.3 d" from seconds. */
export function fmtDuration(sec) {
    if (sec == null) return '—';
    const hours = Number(sec) / 3600;
    return hours < 24 ? t('wo.hours', { n: hours.toFixed(1) }) : t('wo.days', { n: (hours / 24).toFixed(1) });
}

function render() {
    const q = $('#woSearch').value.trim().toLowerCase();
    const shown = q ? rows.filter(r => `${r.work_order} ${r.product_model || ''}`.toLowerCase().includes(q)) : rows;
    $('#woCount').textContent = t('wo.count', { n: fmtInt(shown.length) });
    const tbody = $('#woRows');
    if (!shown.length) { tableState(tbody, COLS, 'empty'); return; }
    tbody.innerHTML = shown.map(r => `<tr>
        <td class="nowrap"><strong>${esc(r.work_order)}</strong></td>
        <td class="nowrap">${esc(r.product_model || '—')}</td>
        <td class="nowrap">${esc(fmtDateTime(r.end_time))}</td>
        <td class="num">${fmtInt(r.total)}</td>
        <td class="num good-text">${fmtInt(r.passed)}</td>
        <td class="num bad-text">${fmtInt(r.failed)}</td>
        <td class="num"><span class="badge ${yieldLevel(r.yield_pct)}">${fmtPct(r.yield_pct)}</span></td>
        <td class="num">${fmtPct(r.retry_rate)}</td>
        <td class="num">${fmtNum(r.avg_24g, 1)}</td>
        <td class="num">${fmtNum(r.avg_5g, 1)}</td>
        <td class="num nowrap">${esc(fmtDuration(r.duration_sec))}</td>
        <td class="num">${fmtNum(r.units_per_hour, 1)}</td>
        <td><button class="btn small" type="button" data-report="${esc(r.work_order)}" data-product="${esc(r.product_model || '')}">✨ ${esc(t('wo.report'))}</button></td>
    </tr>`).join('');
}

async function load(app) {
    const mine = ++seq;
    const tbody = $('#woRows');
    if (!rows.length) tableState(tbody, COLS, 'loading');
    try {
        const data = await apiGet(`/api/work-order-summary${queryString(app.params())}`);
        if (mine !== seq) return;
        rows = data;
        render();
    } catch (err) {
        if (mine !== seq) return;
        rows = [];
        tableState(tbody, COLS, 'error', t('common.loadFailedMsg', { error: err.message }), () => load(app));
    }
}

function init(app) {
    appRef = app;
    $('#woSearch').addEventListener('input', debounce(render, 200));
    $('#woRows').addEventListener('click', e => {
        const btn = e.target.closest('[data-report]');
        if (btn) appRef.openReport(btn.dataset.report, btn.dataset.product);
    });
}

export default { id: 'workorders', scoped: true, init, load, onLangChange: () => { if (rows.length) render(); } };
