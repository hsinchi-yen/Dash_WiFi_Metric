// Production: output rate per work order (units / hour, minutes per unit) over the selected
// scope, plus the hour-by-hour output of the work order in scope (or the latest one).
import { apiGet } from '../core/api.js';
import * as charts from '../core/charts.js';
import { esc, fmtDateTime, fmtInt, fmtNum, fmtPct, fmtTime, yieldLevel } from '../core/format.js';
import { t } from '../core/i18n.js';
import { queryString } from '../core/scope.js';
import { $, tableState } from '../core/ui.js';

const MAX_HOURS = 720;        // work orders spanning more than 30 days are not rate samples
const DIST_BINS = [0, 1, 2, 3, 5, 10, 30, Infinity];
const DIST_LABELS = ['<1m', '1-2m', '2-3m', '3-5m', '5-10m', '10-30m', '>30m'];
let seq = 0;

function tile(label, value, sub = '', cls = '') {
    return `<div class="tile"><div class="tile-label">${esc(label)}</div><div class="tile-value ${cls}">${esc(value)}</div><div class="tile-sub">${esc(sub)}</div></div>`;
}

function usable(rows) {
    return rows
        .filter(r => r.total > 0 && r.duration_sec != null && r.duration_sec / 3600 <= MAX_HOURS)
        .map(r => ({ ...r, hours: Math.max(r.duration_sec / 3600, 1 / 60) }))
        .sort((a, b) => b.units_per_hour - a.units_per_hour);
}

function renderTiles(rows) {
    if (!rows.length) { $('#prTiles').innerHTML = ''; return; }
    const fastest = rows.reduce((a, b) => (a.min_per_unit <= b.min_per_unit ? a : b));
    const slowest = rows.reduce((a, b) => (a.min_per_unit >= b.min_per_unit ? a : b));
    const totalUnits = rows.reduce((s, r) => s + r.total, 0);
    const totalHours = rows.reduce((s, r) => s + r.hours, 0);
    $('#prTiles').innerHTML = [
        tile(t('pr.fastest'), t('pr.minutes', { n: fmtNum(fastest.min_per_unit, 2) }), `WO ${fastest.work_order}`, 'v-good'),
        tile(t('pr.slowest'), t('pr.minutes', { n: fmtNum(slowest.min_per_unit, 2) }), `WO ${slowest.work_order}`, 'v-bad'),
        tile(t('pr.avgTime'), t('pr.minutes', { n: fmtNum(totalHours * 60 / totalUnits, 2) }), t('pr.weighted')),
        tile(t('pr.avgUph'), fmtNum(totalUnits / totalHours, 1), t('pr.unitsPerHour')),
        tile(t('pr.workOrders'), fmtInt(rows.length), t('pr.inScope')),
    ].join('');
}

function renderRate(rows) {
    const top = rows.slice(0, 20);
    if (!top.length) { charts.chartMessage('chPrRate', t('common.empty')); return; }
    charts.render('chPrRate', c => ({
        type: 'bar',
        data: { labels: top.map(r => r.work_order), datasets: [{ label: t('pr.unitsPerHour'), data: top.map(r => r.units_per_hour),
            backgroundColor: top.map(r => charts.alpha(c[yieldLevel(r.yield_pct) || 'muted'], 0.75)), borderRadius: 4 }] },
        options: { plugins: { legend: { display: false } }, scales: { x: { ticks: { font: { size: 11 } } }, y: { beginAtZero: true } } },
    }));
}

function renderDist(rows) {
    if (!rows.length) { charts.chartMessage('chPrDist', t('common.empty')); return; }
    const counts = new Array(DIST_LABELS.length).fill(0);
    rows.forEach(r => {
        for (let i = 0; i < DIST_BINS.length - 1; i++) {
            if (r.min_per_unit >= DIST_BINS[i] && r.min_per_unit < DIST_BINS[i + 1]) { counts[i]++; break; }
        }
    });
    charts.render('chPrDist', c => ({
        type: 'bar',
        data: { labels: DIST_LABELS, datasets: [{ label: t('pr.workOrders'), data: counts, backgroundColor: charts.alpha(c.c3, 0.75), borderRadius: 4 }] },
        options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
    }));
}

function renderTable(rows) {
    $('#prCount').textContent = t('wo.count', { n: fmtInt(rows.length) });
    const tbody = $('#prRows');
    if (!rows.length) { tableState(tbody, 9, 'empty'); return; }
    tbody.innerHTML = rows.map(r => `<tr>
        <td class="nowrap"><strong>${esc(r.work_order)}</strong></td>
        <td class="nowrap">${esc(r.product_model || '—')}</td>
        <td class="nowrap">${esc(fmtDateTime(r.start_time))}</td>
        <td class="nowrap">${esc(fmtDateTime(r.end_time))}</td>
        <td class="num">${fmtNum(r.hours, 1)}</td>
        <td class="num">${fmtInt(r.total)}</td>
        <td class="num"><strong>${fmtNum(r.units_per_hour, 1)}</strong></td>
        <td class="num">${fmtNum(r.min_per_unit, 2)}</td>
        <td class="num"><span class="badge ${yieldLevel(r.yield_pct)}">${fmtPct(r.yield_pct)}</span></td>
    </tr>`).join('');
}

function renderHourly(h) {
    const day = h.days?.[0];
    const wo = h.work_order;
    $('#prHourlyNote').textContent = wo ? t('pr.hourlyNote', { wo, date: day?.date || '—' }) : '';
    const points = day?.hourly || [];
    if (!wo || !points.some(p => p.units)) { charts.chartMessage('chPrHourly', t('common.empty')); return; }
    charts.render('chPrHourly', c => ({
        type: 'bar',
        data: { labels: points.map(p => fmtTime(p.hour)), datasets: [{ label: t('ov.unitsAxis'), data: points.map(p => p.units),
                                                                       backgroundColor: charts.alpha(c.c1, 0.7), borderRadius: 4 }] },
        options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
    }));
}

async function load(app) {
    const mine = ++seq;
    const params = app.params();
    const hourlyParams = { work_order: app.scope.workOrder || undefined, product_model: app.scope.product || undefined, lookback_days: 1 };
    try {
        const [rows, hourly] = await Promise.all([
            apiGet(`/api/work-order-summary${queryString(params)}`),
            apiGet(`/api/dashboard/latest-workorder-hourly${queryString(hourlyParams)}`).catch(() => ({ work_order: null, days: [] })),
        ]);
        if (mine !== seq) return;
        const use = usable(rows);
        renderTiles(use);
        renderRate(use);
        renderDist(use);
        renderTable(use);
        renderHourly(hourly);
    } catch (err) {
        if (mine !== seq) return;
        $('#prTiles').innerHTML = '';
        ['chPrRate', 'chPrDist', 'chPrHourly'].forEach(id => charts.chartMessage(id, t('common.loadFailedMsg', { error: err.message }), 'error'));
        tableState($('#prRows'), 9, 'error', t('common.loadFailedMsg', { error: err.message }), () => load(app));
    }
}

export default { id: 'production', scoped: true, load };
