// The Trend page's Forecast view (#overview?view=forecast), from GET /api/forecast — rules on the data, no LLM.
// With a work order in the filter bar (the latest one by default): its completion — tested / planned, remaining,
// pace, units per working day, the estimated finish, yield; cumulative units with the projection up to the plan, and
// output per test day. Without one (all data or a product): this week's and this month's output so far and projected,
// against last week / month; this month by day and this year by month.
import { apiGet } from '../core/api.js';
import * as charts from '../core/charts.js';
import { esc, fmtDateTime, fmtInt, fmtNum, fmtShortDate, fmtSigned, yieldLevel } from '../core/format.js';
import { t } from '../core/i18n.js';
import { detailHref, queryString } from '../core/scope.js';
import { $ } from '../core/ui.js';

let seq = 0;
let last = null;

function tile(n, label, value, sub, level = '') {
    const el = $(`#fc${n}`);
    el.querySelector('.label').textContent = label;
    el.querySelector('.value').innerHTML = value;
    el.querySelector('.sub').innerHTML = sub || '';
    el.className = `td-num${level ? ` ${level.split(' ').map((x, i) => (i ? x : `lv-${x}`)).join(' ')}` : ''}`;
}

/** "Mon–Fri" for a run of days, else the list. */
function dayNames(days) {
    const run = days.length > 2 && days.every((d, i) => i === 0 || d === days[i - 1] + 1);
    return run ? `${t(`rd.wd${days[0]}`)}–${t(`rd.wd${days[days.length - 1]}`)}` : days.map(d => t(`rd.wd${d}`)).join(t('common.listSep'));
}

function assumptions(d) {
    const a = d.assumptions;
    return t('fc.assume', { start: a.work_start, end: a.work_end, days: dayNames(a.work_days) });
}

function note(text, warn = false) {
    const el = $('#fcNote');
    el.innerHTML = text;
    el.className = `fc-note${warn ? ' warn' : ''}`;
}

/* ─── One work order ─── */
function renderWorkOrder(d) {
    if (!d.found) {
        note(esc(t('fc.woMissing', { wo: d.work_order })), true);
        [1, 2, 3, 4, 5, 6].forEach(n => tile(n, '', '—', ''));
        charts.chartMessage('chFc1', t('common.empty'));
        charts.chartMessage('chFc2', t('common.empty'));
        return;
    }
    const r = d.rate;
    const notes = [];
    if (d.planned && d.remaining > 0 && d.idle_hours > 2) notes.push(t('fc.woIdle', { h: fmtNum(d.idle_hours, 0), time: fmtDateTime(d.last_test) }));
    if (!d.planned) notes.push(t('fc.woNoPlan'));
    notes.push(r.basis === 'recent' ? t('fc.woBasisRecent', { n: r.recent_hours }) : t('fc.woBasisAll'));
    notes.push(assumptions(d));
    note(`<a class="wo-link" href="${esc(detailHref(d.work_order, d.product_model))}">${esc(d.work_order)}</a> · ${esc(d.product_model)}${t('common.dot')}${esc(notes.join(t('common.dot')))}`,
         !d.planned || d.idle_hours > 2);

    tile(1, t('fc.wo.tested'), d.planned ? `${fmtInt(d.total)}<small> / ${fmtInt(d.planned)}</small>` : fmtInt(d.total),
         esc(d.planned ? t('fc.wo.achieved', { pct: fmtNum(d.achieved_pct, 1) }) : t('fc.wo.noPlanShort')), d.planned && d.remaining === 0 ? 'good' : '');
    tile(2, t('fc.wo.remaining'), d.planned ? fmtInt(d.remaining) : '—', esc(d.planned ? t('fc.wo.remainingSub') : t('fc.wo.setPlan')));
    const pace = [];
    if (r.overall_uph != null) pace.push(t('fc.wo.overallUph', { n: fmtNum(r.overall_uph, 1) }));
    if (r.target_uph) pace.push(t('fc.wo.targetUph', { n: fmtNum(r.target_uph, 1) }));
    const slow = r.uph && r.target_uph && r.uph < r.target_uph * 0.8;
    tile(3, t('fc.wo.pace'), r.uph != null ? `${fmtNum(r.uph, 1)}<small> ${esc(t('fc.perHour'))}</small>` : '—', esc(pace.join(t('common.dot'))), slow ? 'warn' : '');
    tile(4, t('fc.wo.perDay'), r.per_working_day != null ? fmtInt(r.per_working_day) : '—',
         esc(r.hours_per_day ? t('fc.wo.perDaySub', { h: fmtNum(r.hours_per_day, 1) }) : ''));
    if (d.planned && d.remaining === 0) {
        tile(5, t('fc.wo.finish'), esc(t('fc.wo.reached')), esc(d.reached_at ? t('fc.wo.reachedAt', { time: fmtDateTime(d.reached_at) }) : ''), 'good');
    } else if (d.finish) {
        const alt = d.finish_target ? t('fc.wo.atTarget', { time: fmtDateTime(d.finish_target.at) }) : '';
        tile(5, t('fc.wo.finish'), esc(fmtDateTime(d.finish.at)), esc([t('fc.wo.daysLeft', { n: fmtNum(d.finish.working_days, 1) }), alt].filter(Boolean).join(t('common.dot'))), 'accent date');
    } else {
        tile(5, t('fc.wo.finish'), '—', esc(d.planned ? t('fc.wo.noRate') : t('fc.wo.setPlan')));
    }
    tile(6, t('fc.wo.yield'), d.yield_pct != null ? `${fmtNum(d.yield_pct, 2)}<small>%</small>` : '—',
         esc(t('fc.wo.yieldSub', { pass: fmtInt(d.passed), fail: fmtInt(d.failed) })), yieldLevel(d.yield_pct));

    // Cumulative units and the projection up to the plan
    const s = d.series;
    $('#fcChart1Title').textContent = t('fc.wo.chartCum');
    $('#fcChart1Note').textContent = s.unit === 'hour' ? t('fc.byHour') : t('fc.byDay');
    const label = p => (s.unit === 'hour' ? fmtDateTime(p.t) : fmtShortDate(p.t));
    const labels = [...s.actual.map(label), ...s.projected.map(label)];
    const actual = [...s.actual.map(p => p.cum), ...s.projected.map(() => null)];
    const projected = s.projected.length
        ? [...s.actual.map((p, i) => (i === s.actual.length - 1 ? p.cum : null)), ...s.projected.map(p => p.cum)] : [];
    if (!labels.length) charts.chartMessage('chFc1', t('common.empty'));
    else charts.render('chFc1', c => ({
        type: 'line',
        data: { labels, datasets: [
            { label: t('fc.actual'), data: actual, borderColor: c.c1, backgroundColor: charts.alpha(c.c1, 0.15), fill: true, pointRadius: 2, tension: 0.2 },
            ...(projected.length ? [{ label: t('fc.projected'), data: projected, borderColor: c.c2, borderDash: [6, 4], pointRadius: 2, spanGaps: true }] : []),
            ...(s.plan ? [{ label: t('fc.plan'), data: labels.map(() => s.plan), borderColor: c.warn, borderDash: [2, 3], borderWidth: 2, pointRadius: 0 }] : []),
        ] },
        options: { interaction: { mode: 'index', intersect: false }, scales: { x: { ticks: { maxTicksLimit: 14 } }, y: { beginAtZero: true, title: { display: true, text: t('ov.unitsAxis') } } } },
    }));

    // Output per test day and the pace
    const days = d.days || [];
    $('#fcChart2Title').textContent = t('fc.wo.chartDays');
    $('#fcChart2Note').textContent = t('fc.wo.chartDaysNote', { n: days.length });
    if (!days.length) charts.chartMessage('chFc2', t('common.empty'));
    else charts.render('chFc2', c => ({
        data: { labels: days.map(x => fmtShortDate(x.day)), datasets: [
            { type: 'bar', label: t('ov.unitsAxis'), data: days.map(x => x.units), backgroundColor: charts.alpha(c.c1, 0.6), borderRadius: 3, yAxisID: 'y', order: 1 },
            { type: 'line', label: t('fc.uph'), data: days.map(x => x.uph), borderColor: c.good, pointRadius: 3, yAxisID: 'y1', order: 0 },
            ...(r.target_uph ? [{ type: 'line', label: t('ov.targetAxis'), data: days.map(() => r.target_uph), borderColor: c.warn, borderDash: [6, 4], pointRadius: 0, yAxisID: 'y1', order: 0 }] : []),
        ] },
        options: { interaction: { mode: 'index', intersect: false }, scales: {
            y: { beginAtZero: true, title: { display: true, text: t('ov.unitsAxis') } },
            y1: { position: 'right', beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: t('ov.unitsPerHour') } } } },
    }));
}

/* ─── All data / one product ─── */
function renderOverall(d) {
    const w = d.week, m = d.month, a = d.average;
    const notes = [d.product_model ? d.product_model : t('filter.allProducts'), t('fc.avgBasis', { n: a.working_days, tested: a.tested_days })];
    let warn = false;
    if (d.idle_days != null && d.idle_days >= 3) {
        warn = true;
        notes.push(t('fc.idle', { n: d.idle_days, time: fmtDateTime(d.last_test) }));
        if (a.per_test_day) notes.push(t('fc.resume', { avg: fmtInt(a.per_test_day), n: fmtInt(m.projected_resume) }));
    }
    notes.push(assumptions(d));
    note(esc(notes.join(t('common.dot'))), warn);
    const delta = (now, then) => (then ? `${fmtSigned((now - then) * 100 / then, 0)}%` : '—');
    tile(1, t('fc.weekDone'), fmtInt(w.done), esc(t('fc.vsSamePoint', { n: fmtInt(w.previous_same_point), d: delta(w.done, w.previous_same_point) })));
    tile(2, t('fc.weekProjected'), fmtInt(w.projected), esc(t('fc.projSub', { prev: fmtInt(w.previous), days: fmtNum(w.working_days_left, 1) })), 'accent');
    tile(3, t('fc.monthDone'), fmtInt(m.done), esc(t('fc.vsSamePoint', { n: fmtInt(m.previous_same_point), d: delta(m.done, m.previous_same_point) })));
    tile(4, t('fc.monthProjected'), fmtInt(m.projected), esc(t('fc.projSubMonth', { prev: fmtInt(m.previous), days: fmtNum(m.working_days_left, 1) })), 'accent');
    tile(5, t('fc.avg'), fmtInt(a.per_working_day), esc(a.per_test_day != null ? t('fc.avgSub', { n: fmtInt(a.per_test_day) }) : ''));
    tile(6, t('fc.monthYield'), m.yield_pct != null ? `${fmtNum(m.yield_pct, 2)}<small>%</small>` : '—',
         esc(m.previous_yield_pct != null ? t('fc.prevYield', { y: fmtNum(m.previous_yield_pct, 2) }) : ''), yieldLevel(m.yield_pct));

    // This month day by day
    const days = d.daily;
    $('#fcChart1Title').textContent = t('fc.chartMonth');
    $('#fcChart1Note').textContent = t('fc.chartMonthNote');
    charts.render('chFc1', c => ({
        data: { labels: days.map(x => fmtShortDate(x.day)), datasets: [
            { type: 'bar', label: t('fc.actual'), data: days.map(x => x.units), backgroundColor: charts.alpha(c.c1, 0.7), borderRadius: 3, yAxisID: 'y', order: 2 },
            { type: 'bar', label: t('fc.projected'), data: days.map(x => x.projected), backgroundColor: charts.alpha(c.c2, 0.3), borderColor: c.c2, borderWidth: 1, borderRadius: 3, yAxisID: 'y', order: 2 },
            { type: 'line', label: t('fc.cumActual'), data: days.map(x => x.cum), borderColor: c.c1, pointRadius: 0, yAxisID: 'y1', order: 0 },
            { type: 'line', label: t('fc.cumProjected'), data: days.map(x => x.cum_projected), borderColor: c.c2, borderDash: [6, 4], pointRadius: 0, yAxisID: 'y1', order: 0 },
            { type: 'line', label: t('fc.cumPrev'), data: days.map(x => x.prev_cum), borderColor: c.muted, borderDash: [2, 3], pointRadius: 0, yAxisID: 'y1', order: 1 },
        ] },
        options: { interaction: { mode: 'index', intersect: false }, scales: {
            x: { stacked: true, ticks: { maxTicksLimit: 16 } },
            y: { stacked: true, beginAtZero: true, title: { display: true, text: t('fc.perDayAxis') } },
            y1: { position: 'right', beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: t('fc.cumAxis') } } } },
    }));

    // This year by month, this month's projection on top
    $('#fcChart2Title').textContent = t('fc.chartYear');
    $('#fcChart2Note').textContent = t('ov.yearNote', { year: d.year, scope: d.product_model || t('filter.allProducts') });
    charts.render('chFc2', c => ({
        type: 'bar',
        data: { labels: d.monthly.map(x => t(`ov.m${x.month}`)), datasets: [
            { label: t('fc.actual'), data: d.monthly.map(x => x.units), backgroundColor: charts.alpha(c.c1, 0.7), stack: 'u', borderRadius: 3 },
            { label: t('fc.projected'), data: d.monthly.map(x => x.projected_extra), backgroundColor: charts.alpha(c.c2, 0.3), borderColor: c.c2, borderWidth: 1, stack: 'u', borderRadius: 3 },
        ] },
        options: { scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } } },
    }));
}

function render(d) {
    if (d.kind === 'work_order') renderWorkOrder(d);
    else renderOverall(d);
}

export async function load(app) {
    const mine = ++seq;
    const p = app.params();
    try {
        const d = await apiGet(`/api/forecast${queryString({ work_order: p.work_order, product_model: p.product_model })}`);
        if (mine !== seq) return;
        last = d;
        render(d);
    } catch (err) {
        if (mine !== seq) return;
        note(esc(t('common.loadFailedMsg', { error: err.message })), true);
        ['chFc1', 'chFc2'].forEach(id => charts.chartMessage(id, t('common.loadFailedMsg', { error: err.message }), 'error'));
    }
}

export function onLangChange() { if (last) render(last); }
