// Today (5C, the home page): the line's day for the TV and the line leader, who only watches it (Q2).
// A banner with the overall light and why, six numbers, four charts (Q9 / Q10); everything from GET /api/today.
// #today shows today; #today?day=YYYY-MM-DD any day (the small day switch, for desktop users).
import { apiGet } from '../core/api.js';
import * as charts from '../core/charts.js';
import { esc, fmtClockDate, fmtDateTime, fmtInt, fmtNum, fmtShortDate, fmtSigned, fmtTime, yieldLevel } from '../core/format.js';
import { getLang, localized, t } from '../core/i18n.js';
import { detailHref } from '../core/scope.js';
import { $ } from '../core/ui.js';
import { flagText } from './workorders.js';

let seq = 0;
let last = null;           // the last response, re-rendered on a language switch
let loadedAt = 0;

function dayFromHash() {
    const q = new URLSearchParams(location.hash.split('?')[1] || '');
    const d = q.get('day');
    return /^\d{4}-\d{2}-\d{2}$/.test(d || '') ? d : '';
}

function setTile(id, value, level, sub) {
    const el = $(`#${id}`);
    el.querySelector('.value').innerHTML = value;
    el.classList.remove('lv-good', 'lv-warn', 'lv-bad');
    if (level && level !== 'none') el.classList.add(`lv-${level}`);
    el.querySelector('.sub').innerHTML = sub || '';
}

function lightWord(light) {
    return t(`td.light.${light || 'none'}`);
}

function reasonText(r) {
    if (r.code === 'wo') {
        const note = (r.note ? (getLang() === 'zh' ? r.note.zh : r.note.en) : '') + (r.note?.rd ? ` ${t('wo.rdNote', { text: r.note.rd.text })}` : '');
        return t('td.reason.wo', { wo: `${r.work_order}${r.product_model ? ` · ${r.product_model}` : ''}`,
                                   note: note || (r.flags || []).map(flagText).join(t('common.listSep')) });
    }
    if (r.code === 'pending') return t('td.reason.pending', { n: fmtInt(r.n) });
    if (r.code === 'idle') return t('td.reason.idle', { n: fmtInt(r.minutes) });
    if (r.code === 'behind') return t('td.reason.behind', { units: fmtInt(r.units), target: fmtNum(r.target, 0) });
    return '';
}

function renderBanner(d) {
    const b = d.banner;
    const el = $('#tdBanner');
    el.className = `card td-banner light-${b.light}`;
    $('#tdLight').textContent = d.summary.total ? lightWord(b.light) : (d.is_today ? t('td.noTestsToday') : t('td.noTestsDay'));
    $('#tdDate').textContent = `${fmtClockDate(d.day, getLang())}${d.is_today ? '' : ` · ${t('td.notToday')}`}`;
    const main = $('#tdReason');
    if (!d.summary.total) {
        main.textContent = d.last_test?.at ? t('td.lastTestAt', { time: fmtDateTime(d.last_test.at) }) : '';
    } else if (!b.reasons.length) {
        main.textContent = t('td.allNormal', { wos: fmtInt(d.work_orders.length), units: fmtInt(d.summary.total), y: fmtNum(d.summary.yield_pct, 2) });
    } else {
        main.textContent = reasonText(b.reasons[0]);
    }
    const more = b.reasons.slice(1);
    $('#tdMore').innerHTML = more.map(r => `<span class="td-chip lv-${esc(r.level)}">${esc(reasonText(r))}</span>`).join('');
    $('#tdWos').innerHTML = d.work_orders.map(w => {
        const st = w.status || {};
        const plan = st.planned ? t('td.woPlan', { total: fmtInt(st.total), planned: fmtInt(st.planned) }) : t('td.woTotal', { total: fmtInt(st.total ?? w.units) });
        return `<a class="td-wo light-${esc(st.light || 'none')}" href="${esc(detailHref(w.work_order || '', w.product_model))}">
            <span class="wo-dot"></span><b>${esc(w.work_order || t('td.noWo'))}</b><span class="muted">${esc(w.product_model || '')}</span>
            <span>${esc(t('td.woToday', { n: fmtInt(w.units) }))}</span><span class="${yieldLevel(w.yield_pct) ? `v-${yieldLevel(w.yield_pct)}` : ''}">${fmtNum(w.yield_pct, 2)}%</span>
            <span class="muted">${esc(plan)}</span></a>`;
    }).join('');
    const nb = d.neighbours || {};
    $('#btnTdPrev').disabled = !nb.prev;
    $('#btnTdPrev').dataset.day = nb.prev || '';
    $('#btnTdNext').disabled = !nb.next;
    $('#btnTdNext').dataset.day = nb.next || '';
    $('#btnTdToday').hidden = d.is_today;
}

/** Minutes since the day's last test: the server's count (test-station clock) plus the time since this answer. */
function minutesAgo(d) {
    if (!d.is_today || d.last_test?.minutes == null) return null;
    return d.last_test.minutes + Math.floor((Date.now() - loadedAt) / 60000);
}

function renderNumbers(d) {
    const s = d.summary;
    const wos = d.work_orders;
    // 1 · tested today (and the plan of the day's only work order)
    let sub = t('td.unitsSub', { wos: fmtInt(wos.length), runs: fmtInt(s.test_runs) });
    if (wos.length === 1 && wos[0].status?.planned) {
        const st = wos[0].status;
        const gap = st.total - st.planned;
        sub = t('td.planSub', { total: fmtInt(st.total), planned: fmtInt(st.planned) })
            + (gap < 0 ? t('td.planShort', { n: fmtInt(-gap) }) : '');
    }
    setTile('tdUnits', s.total ? fmtInt(s.total) : '0', '', esc(sub));
    // 2 · yield vs the previous test day
    let ysub = '';
    if (d.previous && s.total && d.previous.yield_pct != null) {
        const diff = s.yield_pct - d.previous.yield_pct;
        const arrow = diff > 0.005 ? '▲' : diff < -0.005 ? '▼' : '＝';
        ysub = `<span class="${diff < -0.005 ? 'bad-text' : diff > 0.005 ? 'good-text' : ''}">${arrow} ${esc(fmtSigned(diff, 2))}</span> ${esc(t('td.vsDay', { day: fmtShortDate(d.previous.day), y: fmtNum(d.previous.yield_pct, 2) }))}`;
    }
    setTile('tdYield', s.total ? `${fmtNum(s.yield_pct, 2)}<small>%</small>` : '—', s.total ? yieldLevel(s.yield_pct) : '', ysub);
    // 3 · FAIL to handle
    const p = d.pending;
    const psub = [t('td.pendingSub', { fail: fmtInt(p.fail_units), handled: fmtInt(p.handled) })];
    if (p.older_pending) psub.push(t('td.pendingOlder', { n: fmtInt(p.older_pending), days: p.older_days }));
    setTile('tdPending', fmtInt(p.pending), p.pending ? 'bad' : (s.total ? 'good' : ''), esc(psub.join(t('common.dot'))));
    // 4 · the last 60 minutes against the hourly target
    const h = d.last_hour;
    const hval = h.target ? `${fmtInt(h.units)}<small> / ${esc(fmtNum(h.target, 0))}</small>` : fmtInt(h.units);
    const hsub = h.target ? t('td.hourSub', { target: fmtNum(h.target, 0), wo: h.work_order || '—' }) : t('td.hourNoTarget');
    setTile('tdHour', s.total ? hval : '—', h.light, esc(s.total ? (d.is_today ? hsub : `${t('td.hourPast', { from: fmtTime(h.from), to: fmtTime(h.to) })}${t('common.dot')}${hsub}`) : ''));
    // 5 · last test
    renderLastTest(d);
    // 6 · 2.4G / 5G averages
    setTile('tdTp', s.total ? `${fmtNum(s.avg_24g, 1)}<small> / </small>${fmtNum(s.avg_5g, 1)}` : '—', '', esc(t('td.tpSub')));
}

function renderLastTest(d) {
    const lt = d.last_test || {};
    if (!d.summary.total) {
        setTile('tdLast', '—', '', esc(lt.at ? t('td.lastAny', { time: fmtDateTime(lt.at) }) : ''));
        return;
    }
    const mins = minutesAgo(d);
    if (mins === null) {
        setTile('tdLast', fmtTime(lt.day_last), '', esc(t('td.lastOfDay')));
        return;
    }
    const level = lt.light === 'none' ? '' : (mins >= d.settings.idle_warn_minutes ? 'warn' : 'good');
    const value = mins < 60 ? t('td.minAgo', { n: mins }) : t('td.hourAgo', { n: fmtNum(mins / 60, 1) });
    setTile('tdLast', esc(value), level, esc(t('td.lastSub', { time: fmtTime(lt.day_last), n: d.settings.idle_warn_minutes })));
}

function renderTrend(d) {
    const pts = d.trend || [];
    const small = pts.some(p => p.total && p.yield_pct == null);
    $('#tdTrendNote').textContent = pts.length ? t('td.trendNote', { n: pts.length }) + (small ? t('common.dot') + t('td.trendMin', { n: d.trend_min_units }) : '') : '';
    if (!pts.length) { charts.chartMessage('chTdTrend', t('common.empty')); return; }
    charts.render('chTdTrend', c => ({
        data: {
            labels: pts.map(p => fmtShortDate(p.day)),
            datasets: [
                { type: 'line', label: t('ov.yieldAxis'), data: pts.map(p => p.yield_pct), yAxisID: 'y', borderColor: c.good,
                  backgroundColor: c.good, tension: 0.25, spanGaps: true, pointRadius: pts.map(p => (p.day === d.day ? 6 : 3)), order: 0 },
                { type: 'bar', label: t('ov.unitsAxis'), data: pts.map(p => p.total), yAxisID: 'y1', borderRadius: 4, order: 1,
                  backgroundColor: pts.map(p => charts.alpha(c.c1, p.day === d.day ? 0.95 : 0.45)) },
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

function renderHourly(d) {
    const h = d.hourly;
    const pts = h?.points || [];
    const parts = [t('ov.hourlyNote', { day: d.day, start: h.start, end: h.end })];
    if (h.before || h.after) parts.push(t('ov.hourlyOutside', { before: fmtInt(h.before), after: fmtInt(h.after), start: h.start, end: h.end }));
    $('#tdHourlyNote').textContent = parts.join(t('common.dot'));
    if (!d.summary.total) { charts.chartMessage('chTdHourly', d.is_today ? t('td.noTestsToday') : t('common.empty')); return; }
    const hasTarget = pts.some(p => p.target);
    charts.render('chTdHourly', c => ({
        data: {
            labels: pts.map(p => p.hour),
            datasets: [
                { type: 'bar', label: 'PASS', data: pts.map(p => p.pass), backgroundColor: c.pass, stack: 'u', borderRadius: 3, order: 1 },
                { type: 'bar', label: 'FAIL', data: pts.map(p => p.fail), backgroundColor: c.fail, stack: 'u', borderRadius: 3, order: 1 },
                ...(hasTarget ? [{ type: 'line', label: t('ov.targetAxis'), data: pts.map(p => p.target), borderColor: c.warn, stepped: 'middle',
                                   borderDash: [6, 4], borderWidth: 2, pointRadius: 0, order: 0 }] : []),
            ],
        },
        options: {
            interaction: { mode: 'index', intersect: false },
            plugins: { tooltip: { callbacks: { footer: items => { const p = pts[items[0]?.dataIndex]; return p?.work_order ? t('td.hourWo', { wo: p.work_order }) : ''; } } } },
            scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: t('ov.unitsPerHour') } } },
        },
    }));
}

function renderFailTop(d) {
    const rows = d.fail_top || [];
    $('#tdFailNote').textContent = t('td.failNote', { days: d.fail_top_days });
    if (!rows.length) { charts.chartMessage('chTdFails', t('td.noFails', { days: d.fail_top_days })); return; }
    charts.render('chTdFails', c => ({
        type: 'bar',
        data: {
            labels: rows.map(r => localized(r.name_zh, r.name_en) || r.category),
            datasets: [
                { label: d.is_today ? t('td.failToday') : fmtShortDate(d.day), data: rows.map(r => r.day), backgroundColor: c.fail, borderRadius: 3 },
                { label: t('td.failWeek', { days: d.fail_top_days }), data: rows.map(r => r.week), backgroundColor: charts.alpha(c.fail, 0.35), borderRadius: 3 },
            ],
        },
        options: { indexAxis: 'y', scales: { x: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: t('td.failRuns') } } } },
    }));
}

function renderMonthly(d) {
    const m = d.monthly || {};
    const months = m.months || [];
    $('#tdMonthlyNote').textContent = m.year ? t('ov.yearNote', { year: m.year, scope: t('filter.allProducts') }) : '';
    if (!months.some(x => x.total)) { charts.chartMessage('chTdMonthly', t('common.empty')); return; }
    charts.render('chTdMonthly', c => ({
        type: 'bar',
        data: { labels: months.map(x => t(`ov.m${x.month}`)), datasets: [
            { label: 'PASS', data: months.map(x => x.passed), backgroundColor: c.pass, stack: 'u', borderRadius: 3 },
            { label: 'FAIL', data: months.map(x => x.failed), backgroundColor: c.fail, stack: 'u', borderRadius: 3 },
        ] },
        options: { scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } } },
    }));
}

function render(d) {
    renderBanner(d);
    renderNumbers(d);
    renderTrend(d);
    renderHourly(d);
    renderFailTop(d);
    renderMonthly(d);
}

async function load() {
    const mine = ++seq;
    const day = dayFromHash();
    try {
        const d = await apiGet(`/api/today${day ? `?day=${day}` : ''}`);
        if (mine !== seq) return;
        last = d;
        loadedAt = Date.now();
        render(d);
    } catch (err) {
        if (mine !== seq) return;
        $('#tdBanner').className = 'card td-banner light-none';
        $('#tdLight').textContent = t('common.loadFailed');
        $('#tdReason').textContent = err.message;
        ['chTdTrend', 'chTdHourly', 'chTdFails', 'chTdMonthly'].forEach(id => charts.chartMessage(id, t('common.loadFailedMsg', { error: err.message }), 'error'));
    }
}

function go(day) {
    location.hash = day ? `today?day=${day}` : 'today';
}

function init() {
    $('#btnTdPrev').addEventListener('click', e => go(e.currentTarget.dataset.day));
    $('#btnTdNext').addEventListener('click', e => go(e.currentTarget.dataset.day));
    $('#btnTdToday').addEventListener('click', () => go(''));
    // "N minutes ago" keeps counting between data reloads.
    setInterval(() => { if (last && !$('#page-today').hidden) renderLastTest(last); }, 30 * 1000);
}

export default {
    id: 'today', scoped: false, live: true, init, load,
    onLangChange: () => { if (last) render(last); },
};
