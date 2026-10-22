// Trend page (the former Dashboard), two views switched at the top (and in turn on the TV):
//   Trend (#overview): the PASS / FAIL split, the yield and six numbers on top; four charts below (yield trend, hourly
//   output during working hours, units per month, throughput), from GET /api/overview;
//   Forecast (#overview?view=forecast): pages/forecast.js — the work order's completion, or the week's / month's output.
import { apiGet } from '../core/api.js';
import * as charts from '../core/charts.js';
import { bucketLabels, esc, fmtInt, fmtNum, fmtPct, yieldLevel } from '../core/format.js';
import { t } from '../core/i18n.js';
import { queryString } from '../core/scope.js';
import { $, $$ } from '../core/ui.js';
import * as forecast from './forecast.js';

let seq = 0;
const NUMBER_IDS = ['#ovYield', '#ovUnits', '#ovPass', '#ovFail', '#ovRetry', '#ovAvg24', '#ovAvg5'];
const CHART_IDS = ['chOvTrend', 'chOvSplit', 'chOvHourly', 'chOvMonthly', 'chOvTp24', 'chOvTp5'];

function setNumber(id, text, level) {
    const el = $(id);
    el.textContent = text;
    el.classList.remove('v-good', 'v-warn', 'v-bad');
    if (level) el.classList.add(`v-${level}`);
}

function renderNumbers(s, plan) {
    const hero = $('#ovYield');
    hero.innerHTML = '';
    hero.classList.remove('v-good', 'v-warn', 'v-bad');
    if (s.total) {
        hero.append(Number(s.yield_pct).toFixed(2));
        const small = document.createElement('small');
        small.textContent = '%';
        hero.append(small);
        hero.classList.add(`v-${yieldLevel(s.yield_pct)}`);
    } else {
        hero.append('—');
    }
    const planEl = $('#ovPlan');
    planEl.innerHTML = '';
    if (plan && plan.planned) {
        const b = document.createElement('b');
        b.textContent = `${fmtInt(plan.tested)} / ${fmtInt(plan.planned)}`;
        planEl.append(t('ov.plan'), ' ', b);
        if (plan.gap) {
            const gap = document.createElement('span');
            gap.className = 'gap';
            gap.textContent = plan.gap > 0 ? t('ov.planOver', { n: fmtInt(plan.gap) }) : t('ov.planShort', { n: fmtInt(-plan.gap) });
            planEl.append(gap);
        }
    } else {
        planEl.textContent = t('ov.planNone');
    }
    setNumber('#ovUnits', fmtInt(s.total));
    $('#ovUnitsSub').textContent = s.total ? t('ov.unitsSub', { runs: fmtInt(s.test_runs) }) : '';
    setNumber('#ovPass', fmtInt(s.pass));
    setNumber('#ovFail', fmtInt(s.fail));
    setNumber('#ovRetry', s.total ? fmtPct(s.retry_rate) : '—');
    $('#ovRetrySub').textContent = s.total ? t('ov.retrySub', { units: fmtInt(s.retry_units) }) : '';
    setNumber('#ovAvg24', s.total ? fmtNum(s.avg_24g, 1) : '—');
    setNumber('#ovAvg5', s.total ? fmtNum(s.avg_5g, 1) : '—');
}

// A day with fewer units than this gets no yield point (the dashboard's small-sample rule: 1 unit, 1 FAIL would be
// a 0 % day pulling the axis down); its PASS / FAIL bar still shows.
const DAY_YIELD_MIN_UNITS = 10;

function renderTrend(trend, workOrder) {
    const pts = trend.points || [];
    const tested = pts.filter(p => p.total);
    const daily = trend.unit === 'day';
    const small = daily ? tested.filter(p => p.total < DAY_YIELD_MIN_UNITS).length : 0;
    let note = pts.length ? (daily ? t('ov.trendDay') : t('ov.trendHour')) : '';
    if (workOrder && daily && tested.length) {      // the work order's whole run, day by day
        const [from, to] = [tested[0], tested[tested.length - 1]].map(p => bucketLabels([p], 'day')[0]);
        note = t('ov.trendWoDays', { from, to, days: fmtInt(tested.length) });
    }
    if (small) note += t('ov.trendSmall', { n: DAY_YIELD_MIN_UNITS });
    $('#ovTrendNote').textContent = note;
    if (!pts.length) { charts.chartMessage('chOvTrend', t('common.empty')); return; }
    const labels = bucketLabels(pts, trend.unit);
    charts.render('chOvTrend', c => ({
        data: {
            labels,
            datasets: [
                { type: 'line', label: t('ov.yieldAxis'), yAxisID: 'y',
                  data: pts.map(p => (daily && p.total < DAY_YIELD_MIN_UNITS ? null : p.yield_pct)),
                  // per day the line is not drawn across days without testing (it would read as a gradual change)
                  borderColor: c.good, backgroundColor: c.good, tension: 0.25, pointRadius: 3, spanGaps: !(workOrder && daily), order: 0 },
                // units per bucket, PASS and FAIL stacked: which hour / day the failures came from
                { type: 'bar', label: 'PASS', data: pts.map(p => p.pass ?? p.total), yAxisID: 'y1', stack: 'units',
                  backgroundColor: charts.alpha(c.pass, 0.6), borderRadius: 4, maxBarThickness: 90, order: 1 },
                { type: 'bar', label: 'FAIL', data: pts.map(p => p.fail ?? 0), yAxisID: 'y1', stack: 'units',
                  backgroundColor: c.fail, borderRadius: 4, maxBarThickness: 90, order: 1 },
            ],
        },
        options: {
            interaction: { mode: 'index', intersect: false },
            scales: {
                y: { position: 'left', suggestedMin: 90, max: 100, title: { display: true, text: t('ov.yieldAxis') } },
                y1: { position: 'right', beginAtZero: true, stacked: true, grid: { drawOnChartArea: false }, title: { display: true, text: t('ov.unitsAxis') } },
                x: { stacked: true },
            },
        },
    }));
}

/** The small PASS / FAIL doughnut left of the yield, with its own two-line legend. */
function renderSplit(split) {
    const values = [split.pass || 0, split.fail || 0];
    const legend = $('#ovSplitLegend');
    const total = values[0] + values[1];
    if (!total) { charts.destroy('chOvSplit'); legend.textContent = '—'; return; }
    legend.innerHTML = ['PASS', 'FAIL'].map((label, i) => `<div class="item"><div class="row">
        <span class="sw" style="background:var(${i ? '--c-fail' : '--c-pass'})"></span><span>${label}</span>
        <b>${esc(fmtInt(values[i]))}</b></div><div class="pct">${esc((values[i] * 100 / total).toFixed(2))}%</div></div>`).join('');
    charts.render('chOvSplit', c => ({
        type: 'doughnut',
        data: { labels: ['PASS', 'FAIL'], datasets: [{ data: values, backgroundColor: [c.pass, c.fail], borderWidth: 0 }] },
        options: { cutout: '64%', plugins: { legend: { display: false } }, animation: { duration: 0 } },
    }));
}

/** Units per hour during working hours (Settings, 07:00–19:00 by default) on the latest test day of the filter. */
function renderHourly(h) {
    const note = $('#ovHourlyNote');
    const pts = h?.points || [];
    if (!h?.day || !pts.length) { note.textContent = ''; charts.chartMessage('chOvHourly', t('common.empty')); return; }
    const parts = [t('ov.hourlyNote', { day: h.day, start: h.start, end: h.end })];
    if (h.target) parts.push(t('ov.hourlyTarget', { n: fmtNum(h.target, 1) }));
    if (h.before || h.after) parts.push(t('ov.hourlyOutside', { before: fmtInt(h.before), after: fmtInt(h.after), start: h.start, end: h.end }));
    note.textContent = parts.join(t('common.dot'));
    const labels = pts.map(p => p.hour);
    charts.render('chOvHourly', c => ({
        data: {
            labels,
            datasets: [
                { type: 'bar', label: 'PASS', data: pts.map(p => p.pass), backgroundColor: c.pass, stack: 'u', borderRadius: 3, order: 1 },
                { type: 'bar', label: 'FAIL', data: pts.map(p => p.fail), backgroundColor: c.fail, stack: 'u', borderRadius: 3, order: 1 },
                ...(h.target ? [{ type: 'line', label: t('ov.targetAxis'), data: pts.map(() => h.target), borderColor: c.warn,
                                  borderDash: [6, 4], borderWidth: 2, pointRadius: 0, order: 0 }] : []),
            ],
        },
        options: {
            interaction: { mode: 'index', intersect: false },
            scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: t('ov.unitsPerHour') } } },
        },
    }));
}

function renderMonthly(monthly, scopeLabel) {
    $('#ovMonthlyNote').textContent = monthly.year ? t('ov.yearNote', { year: monthly.year, scope: scopeLabel }) : '';
    const months = monthly.months || [];
    if (!months.some(m => m.total)) { charts.chartMessage('chOvMonthly', t('common.empty')); return; }
    charts.render('chOvMonthly', c => ({
        type: 'bar',
        data: { labels: months.map(m => t(`ov.m${m.month}`)), datasets: [
            { label: 'PASS', data: months.map(m => m.passed), backgroundColor: c.pass, stack: 'u', borderRadius: 3 },
            { label: 'FAIL', data: months.map(m => m.failed), backgroundColor: c.fail, stack: 'u', borderRadius: 3 },
        ] },
        options: { scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } } },
    }));
}

/** Throughput histogram for one band (shared with the RF page). */
export function renderBands(canvasId, bands, colorKey) {
    if (!bands || !bands.length) { charts.chartMessage(canvasId, t('common.empty')); return; }
    charts.render(canvasId, c => ({
        type: 'bar',
        data: { labels: bands.map(b => b.bin), datasets: [{ label: t('ov.unitsAxis'), data: bands.map(b => b.count),
                                                            backgroundColor: charts.alpha(c[colorKey], 0.75), borderRadius: 3 }] },
        options: { plugins: { legend: { display: false } },
                   scales: { x: { title: { display: true, text: 'Mbps' } }, y: { beginAtZero: true, ticks: { precision: 0 } } } },
    }));
}

/** 'trend' or 'forecast', from the address (#overview?view=forecast). */
export function currentView() {
    return new URLSearchParams(location.hash.split('?')[1] || '').get('view') === 'forecast' ? 'forecast' : 'trend';
}

function showView(view) {
    $$('#page-overview [data-ov-view]').forEach(el => { el.hidden = el.dataset.ovView !== view; });
    $('#page-overview').classList.toggle('view-forecast', view === 'forecast');
    $$('#ovViewSeg button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.view === view)));
    const title = view === 'forecast' ? t('fc.title') : t('page.overview');
    $('#pageTitle').textContent = title;
    document.title = `${title} · ${t('app.title')}`;
}

async function load(app) {
    const view = currentView();
    showView(view);
    if (view === 'forecast') { await forecast.load(app); return; }
    const mine = ++seq;
    try {
        const data = await apiGet(`/api/overview${queryString(app.params())}`);
        if (mine !== seq) return;
        renderNumbers(data.summary, data.plan);
        if (!data.summary.total) {
            ['chOvTrend', 'chOvHourly', 'chOvTp24', 'chOvTp5'].forEach(id => charts.chartMessage(id, t('common.empty')));
            renderSplit({ pass: 0, fail: 0 });
            $('#ovTrendNote').textContent = '';
            $('#ovHourlyNote').textContent = '';
        } else {
            renderTrend(data.trend, app.scope.workOrder);
            renderSplit(data.split);
            renderHourly(data.hourly);
            renderBands('chOvTp24', data.throughput.bands_24g, 'c1');
            renderBands('chOvTp5', data.throughput.bands_5g, 'c2');
        }
        renderMonthly(data.monthly, app.scope.workOrder ? app.scope.workOrder : (app.scope.product || t('filter.allWo')));
    } catch (err) {
        if (mine !== seq) return;
        NUMBER_IDS.forEach(id => { $(id).textContent = '—'; });
        $('#ovPlan').textContent = err.message;
        CHART_IDS.filter(id => id !== 'chOvSplit').forEach(id => charts.chartMessage(id, t('common.loadFailedMsg', { error: err.message }), 'error'));
        charts.destroy('chOvSplit');
        $('#ovSplitLegend').textContent = '—';
    }
}

function init() {
    $$('#ovViewSeg button').forEach(b => b.addEventListener('click', () => {
        location.hash = b.dataset.view === 'forecast' ? 'overview?view=forecast' : 'overview';
    }));
}

export default { id: 'overview', scoped: true, init, load, onLangChange: () => { if (currentView() === 'forecast') forecast.onLangChange(); } };
