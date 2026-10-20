// RF Analysis: 2.4G / 5G / BT tabs over the selected scope, from one GET /api/rf call.
import { apiGet } from '../core/api.js';
import * as charts from '../core/charts.js';
import { bucketLabels, esc, fmtDateTime, fmtInt, fmtNum, fmtSigned } from '../core/format.js';
import { t } from '../core/i18n.js';
import { queryString } from '../core/scope.js';
import { $, $$, tableState } from '../core/ui.js';
import { renderBands } from './overview.js';

let seq = 0;
let data = null;
let tab = '24g';
const TABS = ['24g', '5g', 'bt'];

function tile(label, value, cls = '', sub = '') {
    return `<div class="tile"><div class="tile-label">${esc(label)}</div><div class="tile-value ${cls}">${esc(value)}</div>${sub ? `<div class="tile-sub">${esc(sub)}</div>` : ''}</div>`;
}

function bandTiles(b, units) {
    const n = v => (v == null ? '—' : fmtNum(v, 1));
    return [
        tile(t('rf.tile.avg'), n(b.avg), '', 'Mbps'),
        tile(t('rf.tile.p50'), n(b.p50), '', 'Mbps'),
        tile(t('rf.tile.p5'), n(b.p5), '', t('rf.tile.p5Sub')),
        tile(t('rf.tile.std'), n(b.std), '', 'Mbps'),
        tile(t('rf.tile.rssi'), b.rssi_before == null ? '—' : `${fmtNum(b.rssi_before, 1)} → ${fmtNum(b.rssi_after, 1)}`,
             '', b.rssi_delta == null ? '' : t('rf.tile.rssiSub', { d: fmtSigned(b.rssi_delta, 1) })),
        tile(t('rf.tile.fail'), fmtInt(b.fail), b.fail ? 'v-bad' : 'v-good', t('rf.tile.ofUnits', { n: fmtInt(units) })),
        tile(t('rf.tile.missing'), fmtInt(b.missing), b.missing ? 'v-warn' : ''),
    ];
}

function renderTrend(canvasId, noteId, trend, key, p5key, c1Key) {
    const pts = trend.points || [];
    $(noteId).textContent = pts.length ? (trend.unit === 'hour' ? t('ov.trendHour') : t('ov.trendDay')) : '';
    if (!pts.some(p => p.units)) { charts.chartMessage(canvasId, t('common.empty')); return; }
    const labels = bucketLabels(pts, trend.unit);
    charts.render(canvasId, c => ({
        data: {
            labels,
            datasets: [
                { type: 'line', label: t('rf.tile.avg'), data: pts.map(p => p[key]), yAxisID: 'y',
                  borderColor: c[c1Key], backgroundColor: c[c1Key], tension: 0.25, pointRadius: 2, spanGaps: true, order: 0 },
                { type: 'line', label: t('rf.tile.p5'), data: pts.map(p => p[p5key]), yAxisID: 'y',
                  borderColor: c.warn, backgroundColor: c.warn, borderDash: [5, 4], tension: 0.25, pointRadius: 0, spanGaps: true, order: 0 },
                { type: 'bar', label: t('ov.unitsAxis'), data: pts.map(p => p.units), yAxisID: 'y1',
                  backgroundColor: charts.alpha(c.muted, 0.35), borderRadius: 3, order: 1 },
            ],
        },
        options: {
            interaction: { mode: 'index', intersect: false },
            scales: {
                y: { position: 'left', beginAtZero: true, title: { display: true, text: 'Mbps' } },
                y1: { position: 'right', beginAtZero: true, grid: { drawOnChartArea: false }, ticks: { precision: 0 } },
            },
        },
    }));
}

function renderStdTable(rows) {
    const tbody = $('#rfStdRows');
    if (!rows.length) { tableState(tbody, 8, 'empty'); return; }
    tbody.innerHTML = rows.map(r => `<tr>
        <td><code>${esc(r.sn)}</code></td>
        <td class="nowrap">${esc(r.work_order || '—')}</td>
        <td class="nowrap">${esc(r.product_model || '—')}</td>
        <td class="nowrap">${esc(fmtDateTime(r.test_time))}</td>
        <td class="num">${fmtNum(r.avg_5g, 1)}</td>
        <td class="num"><strong>${fmtNum(r.std_5g, 2)}</strong></td>
        <td class="num">${fmtNum(r.std_24g, 2)}</td>
        <td class="num">${fmtSigned(r.rssi_delta_5g, 1)}</td>
    </tr>`).join('');
}

function renderBt(bt, units) {
    const n = v => (v == null ? '—' : fmtNum(v, 1));
    $('#rfTilesBt').innerHTML = [
        tile(t('rf.tile.btAvg'), n(bt.avg_latency), '', 'ms'),
        tile(t('rf.tile.btMin'), n(bt.min_latency), '', 'ms'),
        tile(t('rf.tile.btMax'), n(bt.max_latency), '', 'ms'),
        tile(t('rf.tile.btLoss'), bt.avg_loss_rate == null ? '—' : `${fmtNum(bt.avg_loss_rate, 2)}%`, bt.avg_loss_rate > 0 ? 'v-warn' : ''),
        tile(t('rf.tile.btFail'), fmtInt(bt.fail), bt.fail ? 'v-bad' : 'v-good', t('rf.tile.ofUnits', { n: fmtInt(units) })),
        tile(t('rf.tile.missing'), fmtInt(bt.missing), bt.missing ? 'v-warn' : ''),
    ].join('');
    const bands = bt.latency_bands || [];
    if (!bands.length) { charts.chartMessage('chRfBt', t('common.empty')); return; }
    charts.render('chRfBt', c => ({
        type: 'bar',
        data: { labels: bands.map(b => b.bin), datasets: [{ label: t('ov.unitsAxis'), data: bands.map(b => b.count),
                                                            backgroundColor: charts.alpha(c.c3, 0.75), borderRadius: 3 }] },
        options: { plugins: { legend: { display: false } },
                   scales: { x: { title: { display: true, text: 'ms' } }, y: { beginAtZero: true, ticks: { precision: 0 } } } },
    }));
}

function renderAll() {
    if (!data) return;
    $('#rfNote').textContent = t('rf.note', { n: fmtInt(data.units) });
    if (!data.units) {
        ['#rfTiles24', '#rfTiles5', '#rfTilesBt'].forEach(id => { $(id).innerHTML = ''; });
        ['chRfTrend24', 'chRfDist24', 'chRfTrend5', 'chRfDist5', 'chRfBt'].forEach(id => charts.chartMessage(id, t('common.empty')));
        tableState($('#rfStdRows'), 8, 'empty');
        return;
    }
    $('#rfTiles24').innerHTML = bandTiles(data.band_24g, data.units).join('');
    renderTrend('chRfTrend24', '#rfTrendNote24', data.trend, 'avg_24g', 'p5_24g', 'c1');
    renderBands('chRfDist24', data.bands_24g, 'c1');

    const b5 = data.band_5g;
    $('#rfTiles5').innerHTML = [...bandTiles(b5, data.units),
        tile(t('rf.tile.degraded'), fmtInt(b5.degraded), b5.degraded ? 'v-warn' : 'v-good', t('rf.tile.degradedSub'))].join('');
    renderTrend('chRfTrend5', '#rfTrendNote5', data.trend, 'avg_5g', 'p5_5g', 'c2');
    renderBands('chRfDist5', data.bands_5g, 'c2');
    renderStdTable(data.stddev_top || []);

    renderBt(data.bt, data.units);
}

function showTab(next) {
    tab = TABS.includes(next) ? next : '24g';
    $$('[data-rf-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.rfTab === tab)));
    $$('[data-rf-view]').forEach(v => { v.hidden = v.dataset.rfView !== tab; });
    // Charts in a hidden container have no size; re-render the visible ones.
    renderAll();
}

async function load(app) {
    const mine = ++seq;
    try {
        data = await apiGet(`/api/rf${queryString(app.params())}`);
        if (mine !== seq) return;
        renderAll();
    } catch (err) {
        if (mine !== seq) return;
        data = null;
        $('#rfNote').textContent = '';
        ['chRfTrend24', 'chRfDist24', 'chRfTrend5', 'chRfDist5', 'chRfBt'].forEach(id =>
            charts.chartMessage(id, t('common.loadFailedMsg', { error: err.message }), 'error'));
    }
}

function init() {
    $$('[data-rf-tab]').forEach(b => b.addEventListener('click', () => showTab(b.dataset.rfTab)));
}

export default { id: 'rf', scoped: true, init, load };
