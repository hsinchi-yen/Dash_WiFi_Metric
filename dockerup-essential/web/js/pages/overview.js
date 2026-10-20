// Dashboard: seven numbers (each shown once) and four charts, from GET /api/overview.
import { apiGet } from '../core/api.js';
import * as charts from '../core/charts.js';
import { bucketLabels, fmtInt, fmtNum, fmtPct, yieldLevel } from '../core/format.js';
import { t } from '../core/i18n.js';
import { queryString } from '../core/scope.js';
import { $ } from '../core/ui.js';

let seq = 0;
const NUMBER_IDS = ['#ovYield', '#ovUnits', '#ovPass', '#ovFail', '#ovRetry', '#ovAvg24', '#ovAvg5'];
const CHART_IDS = ['chOvTrend', 'chOvSplit', 'chOvMonthly', 'chOvTp24', 'chOvTp5'];

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

function renderTrend(trend) {
    const pts = trend.points || [];
    $('#ovTrendNote').textContent = pts.length ? (trend.unit === 'hour' ? t('ov.trendHour') : t('ov.trendDay')) : '';
    if (!pts.length) { charts.chartMessage('chOvTrend', t('common.empty')); return; }
    const labels = bucketLabels(pts, trend.unit);
    charts.render('chOvTrend', c => ({
        data: {
            labels,
            datasets: [
                { type: 'line', label: t('ov.yieldAxis'), data: pts.map(p => p.yield_pct), yAxisID: 'y',
                  borderColor: c.good, backgroundColor: c.good, tension: 0.25, pointRadius: 3, spanGaps: true, order: 0 },
                { type: 'bar', label: t('ov.unitsAxis'), data: pts.map(p => p.total), yAxisID: 'y1',
                  backgroundColor: charts.alpha(c.c1, 0.55), borderRadius: 4, order: 1 },
            ],
        },
        options: {
            interaction: { mode: 'index', intersect: false },
            scales: {
                y: { position: 'left', suggestedMin: 90, max: 100, title: { display: true, text: t('ov.yieldAxis') } },
                y1: { position: 'right', beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: t('ov.unitsAxis') } },
            },
        },
    }));
}

function renderSplit(split) {
    const values = [split.pass || 0, split.fail || 0];
    if (!values.some(Boolean)) { charts.chartMessage('chOvSplit', t('common.empty')); return; }
    const total = values[0] + values[1];
    charts.render('chOvSplit', c => ({
        type: 'doughnut',
        data: {
            labels: ['PASS', 'FAIL'].map((label, i) =>
                t('ov.splitLabel', { label, n: fmtInt(values[i]), pct: (values[i] * 100 / total).toFixed(2) })),
            datasets: [{ data: values, backgroundColor: [c.pass, c.fail], borderWidth: 0 }],
        },
        options: { cutout: '62%', plugins: { legend: { position: window.innerWidth < 900 ? 'bottom' : 'right', labels: { font: { size: 15 } } } } },
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

async function load(app) {
    const mine = ++seq;
    try {
        const data = await apiGet(`/api/overview${queryString(app.params())}`);
        if (mine !== seq) return;
        renderNumbers(data.summary, data.plan);
        if (!data.summary.total) {
            ['chOvTrend', 'chOvSplit', 'chOvTp24', 'chOvTp5'].forEach(id => charts.chartMessage(id, t('common.empty')));
            $('#ovTrendNote').textContent = '';
        } else {
            renderTrend(data.trend);
            renderSplit(data.split);
            renderBands('chOvTp24', data.throughput.bands_24g, 'c1');
            renderBands('chOvTp5', data.throughput.bands_5g, 'c2');
        }
        renderMonthly(data.monthly, app.scope.workOrder ? app.scope.workOrder : (app.scope.product || t('filter.allWo')));
    } catch (err) {
        if (mine !== seq) return;
        NUMBER_IDS.forEach(id => { $(id).textContent = '—'; });
        $('#ovPlan').textContent = err.message;
        CHART_IDS.forEach(id => charts.chartMessage(id, t('common.loadFailedMsg', { error: err.message }), 'error'));
    }
}

export default { id: 'overview', scoped: true, load };
