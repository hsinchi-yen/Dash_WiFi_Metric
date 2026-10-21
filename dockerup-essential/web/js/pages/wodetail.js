// Work-order detail (5B): #wo?wo=<work order>&pm=<product>[&cmp=<other>&cpm=<product>][&focus=report]
// Status and note (5A), numbers, checks, comparison with the previous work order of the same product (or any
// other), trend, throughput, the structured final report (L3) with follow-up questions, FAIL list, upload batches.
import { apiDelete, apiGet, apiPost, download, request } from '../core/api.js';
import * as auth from '../core/auth.js';
import * as charts from '../core/charts.js';
import { bucketLabels, esc, fmtDate, fmtDateTime, fmtInt, fmtNum, fmtPct, yieldLevel } from '../core/format.js';
import { getLang, joinList, localized, t } from '../core/i18n.js';
import { queryString } from '../core/scope.js';
import { $, openFormModal, tableState, toast } from '../core/ui.js';
import { categoryBadge, reasonText, runSeqHtml, stationText } from './fails.js';
import { renderBands } from './overview.js';
import { flagText } from './workorders.js';

const st = { key: null, data: null, report: null, cmp: null, ask: [], poll: null, seq: 0 };
let appRef;

/** Parameters after "#wo?". */
export function params() {
    const q = location.hash.split('?')[1] || '';
    const p = new URLSearchParams(q);
    return { wo: p.get('wo') || '', pm: p.get('pm') || '', cmp: p.get('cmp') || '', cpm: p.get('cpm') || '', focus: p.get('focus') || '' };
}

const keyParams = () => ({ work_order: st.key.wo, ...(st.key.pm ? { product_model: st.key.pm } : {}) });

/* ─── Header, tiles, checks ─── */
function renderHead(d) {
    const s = d.status;
    $('#wdWo').textContent = s.work_order;
    $('#wdProduct').textContent = s.product_model || '—';
    const pill = $('#wdState');
    pill.className = `wo-state light-${s.light}`;
    $('#wdStateText').textContent = t(`wo.state.${s.state}`);
    const m = d.metrics;
    $('#wdPeriod').textContent = m.first_test ? t('wd.period', { from: fmtDateTime(m.first_test), to: fmtDateTime(m.last_test),
        hours: fmtNum(m.duration_h, 1), active: fmtInt(m.active_hours) }) : '';
    const note = s.note || {};
    $('#wdNote').innerHTML = (note.source === 'llm' ? `<span class="ai-tag" title="${esc(t('wo.aiTitle'))}">${esc(t('wo.ai'))}</span>` : '')
        + esc(getLang() === 'zh' ? note.zh || '' : note.en || '');
    $('#btnWdPlan').hidden = !auth.isAdmin();
    document.title = `${s.work_order} · ${t('app.title')}`;
    $('#pageTitle').textContent = t('wd.title', { wo: s.work_order });
}

function tile(label, value, sub = '', cls = '') {
    return `<div class="tile"><div class="tile-label">${esc(label)}</div><div class="tile-value ${cls}">${value}</div>${sub ? `<div class="tile-sub">${sub}</div>` : ''}</div>`;
}

function renderTiles(d) {
    const s = d.status, m = d.metrics, ex = d.extra, plan = d.plan;
    const planSub = plan ? esc(t(plan.source === 'admin' ? 'wd.planByAdmin' : 'wd.planByUpload', { who: plan.updated_by || '—' })) : esc(t('wo.planNone'));
    const tested = s.planned ? `${fmtInt(s.total)} <small>/ ${fmtInt(s.planned)}</small>` : fmtInt(s.total);
    $('#wdTiles').innerHTML = [
        tile(t('wo.col.plan'), tested, s.planned ? `${esc(fmtNum(s.achieved_pct, 0))}% · ${planSub}` : planSub),
        tile(t('wo.col.yield'), esc(fmtPct(s.yield_pct)), esc(t('wd.yieldSub', { pass: fmtInt(s.passed), fail: fmtInt(s.failed) })), `v-${yieldLevel(s.yield_pct)}`),
        tile('FAIL', esc(fmtInt(s.failed)), ex.fail_reasons.length ? esc(joinList(ex.fail_reasons.slice(0, 2).map(r => `${catName(r)} ${r.units}`))) : '', s.failed ? 'v-fail' : ''),
        tile(t('wo.col.retry'), esc(fmtPct(m.retry_rate)), esc(t('wd.retrySub', { units: fmtInt(ex.retry_units), runs: fmtInt(ex.test_runs) }))),
        tile(t('wd.p50_24g'), esc(fmtNum(s.p50_24g, 1)), s.hist_p50_24g ? esc(t('wd.usual', { n: fmtNum(s.hist_p50_24g, 1) })) : 'Mbps'),
        tile(t('wd.p50_5g'), esc(fmtNum(s.p50_5g, 1)), s.hist_p50_5g ? esc(t('wd.usual', { n: fmtNum(s.hist_p50_5g, 1) })) : 'Mbps'),
        tile(t('wo.col.uphTarget'), `${esc(fmtNum(s.uph, 1))} <small>/ ${esc(fmtNum(s.uph_target, 1))}</small>`,
             s.uph_target_source ? `<span title="${esc(t(`wo.targetSource.${s.uph_target_source}`))}">${esc(t(`wd.target.${s.uph_target_source}`))}</span>` : ''),
        tile(t('wd.streak'), esc(fmtInt(s.max_fail_streak)), esc(t('wd.streakSub'))),
    ].join('');
}

const catName = r => localized(r.name_zh, r.name_en) || reasonText(r.code || r.reason);

/** FAIL runs of the work order by category × what the retest showed, and the stations involved (5F). */
function renderFailCats(d) {
    const fr = d.extra.fail_runs;
    const card = $('#wdFailCatCard');
    card.hidden = !fr?.total_runs;
    if (card.hidden) return;
    $('#wdFailCatNote').textContent = t('wd.failCatsNote', { runs: fmtInt(fr.total_runs), pass: fmtInt(fr.retest.pass_on_retest || 0) });
    $('#wdFailCatRows').innerHTML = fr.categories.map(c => `<tr>
        <td title="${esc(localized(c.action_zh, c.action_en) || '')}"><span class="badge fail">${esc(localized(c.name_zh, c.name_en) || c.category)}</span></td>
        <td class="num">${fmtInt(c.runs)}</td><td class="num">${fmtInt(c.units)}</td>
        <td class="num ${c.pass_on_retest ? 'good-text' : 'muted'}">${fmtInt(c.pass_on_retest)}</td>
        <td class="num ${c.failed_again ? 'bad-text' : 'muted'}">${fmtInt(c.failed_again)}</td>
        <td class="num ${c.not_retested ? 'warn-text' : 'muted'}">${fmtInt(c.not_retested)}</td></tr>`).join('');
    $('#wdStationRows').innerHTML = fr.stations.slice(0, 6).map(s => `<tr><td>${esc(stationText(s))}</td>
        <td class="num">${fmtInt(s.runs)}</td><td class="num">${fmtInt(s.units)}</td></tr>`).join('');
}

function renderChecks(d) {
    const flags = d.status.flags || [];
    $('#wdChecksNote').textContent = t('wd.checksNote', { good: d.status.thresholds?.yield_good, warn: d.status.thresholds?.yield_warn });
    $('#wdChecks').innerHTML = flags.length
        ? flags.map(f => `<li class="${f.level === 'bad' ? 'error' : f.level === 'warn' ? 'warn' : ''}">${esc(flagText(f))}</li>`).join('')
        : `<li>${esc(t('wo.noFlags'))}</li>`;
}

/* ─── Comparison ─── */
const CMP_ROWS = [
    ['total', 'wo.col.units', v => fmtInt(v)], ['yield_pct', 'wo.col.yield', v => fmtPct(v)], ['failed', 'FAIL', v => fmtInt(v)],
    ['retry_rate', 'wo.col.retry', v => fmtPct(v)], ['p50_24g', 'wd.p50_24g', v => fmtNum(v, 1)], ['p50_5g', 'wd.p50_5g', v => fmtNum(v, 1)],
    ['uph', 'wo.col.uph', v => fmtNum(v, 1)], ['max_fail_streak', 'wd.streak', v => fmtInt(v)],
    ['active_hours', 'wd.activeHours', v => fmtInt(v)], ['duration_h', 'wd.durationH', v => fmtNum(v, 1)],
];

function renderCompare(a, b, changes, isPrevious) {
    $('#wdCmpA').textContent = a.work_order;
    $('#wdCmpB').textContent = b ? b.work_order : '—';
    if (!b) {
        $('#wdCmpRows').innerHTML = `<tr><td colspan="4" class="muted">${esc(t('wd.noPrevious'))}</td></tr>`;
        $('#wdCmpNote').textContent = '';
        return;
    }
    $('#wdCmpRows').innerHTML = CMP_ROWS.map(([k, label, f]) => {
        const c = changes[k];
        const arrow = !c || c.diff === 0 ? '' : c.diff > 0 ? '▲' : '▼';
        const cls = c?.verdict === 'worse' ? 'bad-text' : c?.verdict === 'better' ? 'good-text' : 'muted';
        const diff = c ? `${arrow} ${f(Math.abs(c.diff))}`.trim() : '—';
        return `<tr${c?.notable ? ' class="notable"' : ''}><td>${esc(label.includes('.') ? t(label) : label)}</td>
            <td class="num">${esc(f(a[k]))}</td><td class="num">${esc(f(b[k]))}</td>
            <td class="num ${cls}">${esc(c && c.diff === 0 ? '=' : diff)}${c?.notable ? ` <span class="badge bad">${esc(t('wd.notable'))}</span>` : ''}</td></tr>`;
    }).join('');
    $('#wdCmpNote').textContent = t(isPrevious ? 'wd.cmpPrevious' : 'wd.cmpChosen', { wo: b.work_order, product: b.product_model || '—', date: fmtDate(b.last_test) });
}

function fillCompareSelect(d, chosen) {
    const sel = $('#wdCompareSel');
    sel.innerHTML = '';
    const prev = d.previous?.metrics;
    if (!prev) sel.appendChild(new Option(t('wd.noPrevious'), ''));
    d.others.forEach(o => {
        const label = `${o.work_order} · ${o.product_model || '—'} · ${fmtDate(o.last_test)}${prev && o.work_order === prev.work_order && o.product_model === prev.product_model ? ` ${t('wd.previousTag')}` : ''}`;
        sel.appendChild(new Option(label, JSON.stringify([o.work_order, o.product_model || ''])));
    });
    sel.value = chosen ? JSON.stringify(chosen) : prev ? JSON.stringify([prev.work_order, prev.product_model || '']) : '';
}

async function chooseCompare(value) {
    if (!value) { renderCompare(st.data.metrics, null, {}, true); return; }
    const [wo, pm] = JSON.parse(value);
    const prev = st.data.previous?.metrics;
    if (prev && prev.work_order === wo && (prev.product_model || '') === pm) {
        renderCompare(st.data.metrics, prev, st.data.previous.changes, true);
        return;
    }
    try {
        const r = await apiGet(`/api/wo-compare${queryString({ a: st.key.wo, pa: st.key.pm, b: wo, pb: pm })}`);
        renderCompare(r.a, r.b, r.changes, false);
    } catch (err) { toast(err.message, 'bad'); }
}

/* ─── Charts ─── */
function renderCharts(d) {
    const pts = d.trend.points || [];
    $('#wdTrendNote').textContent = pts.length ? (d.trend.unit === 'hour' ? t('ov.trendHour') : t('ov.trendDay')) : '';
    if (!pts.length) charts.chartMessage('chWdTrend', t('common.empty'));
    else {
        const labels = bucketLabels(pts, d.trend.unit);
        charts.render('chWdTrend', c => ({
            data: { labels, datasets: [
                { type: 'line', label: t('ov.yieldAxis'), data: pts.map(p => p.yield_pct), yAxisID: 'y', borderColor: c.good, backgroundColor: c.good,
                  tension: 0.25, pointRadius: 3, spanGaps: true, order: 0 },
                { type: 'bar', label: t('ov.unitsAxis'), data: pts.map(p => p.total), yAxisID: 'y1', backgroundColor: charts.alpha(c.c1, 0.55), borderRadius: 4, order: 1 },
            ] },
            options: { interaction: { mode: 'index', intersect: false },
                scales: { y: { position: 'left', suggestedMin: 90, max: 100 }, y1: { position: 'right', beginAtZero: true, grid: { drawOnChartArea: false } } } },
        }));
    }
    renderBands('chWdTp24', d.throughput.bands_24g, 'c1');
    renderBands('chWdTp5', d.throughput.bands_5g, 'c2');
}

/* ─── Final report (L3) ─── */
function reportMarkdown(r, lang) {
    const part = r.report?.[lang];
    if (!part) return '';
    const L = k => t(`wd.sec.${k}`);
    const lines = [`# ${t('wd.reportTitle', { wo: r.work_order })}`, '', part.summary, ''];
    for (const k of ['anomalies', 'causes', 'actions']) {
        if (part[k]?.length) lines.push(`## ${L(k)}`, ...part[k].map(x => `- ${x}`), '');
    }
    lines.push(`## ${t('wd.facts')}`, ...factRows(r.facts).map(([k, v]) => `- ${k}: ${v}`));
    return lines.join('\n');
}

function factRows(f) {
    if (!f) return [];
    const rows = [
        [t('wo.col.units'), fmtInt(f.total)], [t('wo.col.yield'), fmtPct(f.yield_pct)], ['PASS / FAIL', `${fmtInt(f.passed)} / ${fmtInt(f.failed)}`],
        [t('wd.thresholds'), `${f.thresholds?.yield_good}% / ${f.thresholds?.yield_warn}%`],
        [t('wo.col.plan'), f.planned ? `${fmtInt(f.total)} / ${fmtInt(f.planned)} (${fmtNum(f.achieved_pct, 0)}%)` : t('wo.planNone')],
        [t('wo.col.retry'), `${fmtPct(f.retry_rate)} (${fmtInt(f.retry_units)})`],
        [t('wd.p50_24g'), `${fmtNum(f.p50_24g, 1)} (${t('wd.usual', { n: fmtNum(f.usual_p50_24g, 1) })})`],
        [t('wd.p50_5g'), `${fmtNum(f.p50_5g, 1)} (${t('wd.usual', { n: fmtNum(f.usual_p50_5g, 1) })})`],
        [t('wo.col.uphTarget'), `${fmtNum(f.uph, 1)} / ${fmtNum(f.uph_target, 1)}`], [t('wd.streak'), fmtInt(f.max_fail_streak)],
        [t('wd.period2'), `${f.first_test} – ${f.last_test}`],
        [t('wd.failingUnits'), f.failing_units_by_category?.length ? joinList(f.failing_units_by_category.map(r => `${r.category} ${r.units}`)) : '—'],
        [t('wd.failCats'), f.fail_runs_by_category?.length ? joinList(f.fail_runs_by_category.map(r => t('wd.failRunsFact', { cat: r.category, runs: r.fail_runs, pass: r.passed_on_retest }))) : '—'],
    ];
    if (f.previous) rows.push([t('wd.previous'), `${f.previous.work_order} (${fmtPct(f.previous.yield_pct)}, ${fmtInt(f.previous.total)})`]);
    return rows;
}

function renderReport(r) {
    st.report = r;
    const lang = getLang();
    const box = $('#wdReport');
    const stateEl = $('#wdReportState');
    const part = r.report?.[lang];
    const meta = [];
    if (r.generated_at) meta.push(t('rp.generated', { time: fmtDateTime(r.generated_at) }));
    if (r.source === 'llm') meta.push(t('wd.aiChecked'));
    if (r.source === 'rule') meta.push(t('wd.ruleOnly'));
    $('#wdReportMeta').textContent = meta.join(t('common.dot'));
    $('#btnWdRegen').disabled = !r.llm || r.pending;
    $('#btnWdRegen').textContent = r.state === 'none' ? t('wd.generate') : t('wd.regen');
    $('#btnWdCopy').disabled = !part;
    let notice = '';
    if (r.pending) notice = `<div class="rp-pending"><span class="spinner"></span>${esc(t(r.state === 'none' ? 'wd.writing' : 'wd.rewriting'))}</div>`;
    else if (r.state === 'stale') notice = `<div class="notice warn">${esc(t('wd.stale'))}</div>`;
    else if (r.state === 'none') notice = `<div class="notice">${esc(t(r.auto ? 'wd.willWrite' : 'wd.notDone'))}</div>`;
    if (!r.llm && !part) notice += `<div class="notice warn">${esc(t('rp.offline'))}</div>`;
    stateEl.innerHTML = notice;
    if (!part) { box.innerHTML = ''; }
    else {
        const sec = k => part[k]?.length ? `<h4>${esc(t(`wd.sec.${k}`))}</h4><ul>${part[k].map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '';
        box.innerHTML = `<p class="wd-summary">${esc(part.summary)}</p>${sec('anomalies')}${sec('causes')}${sec('actions')}`
            + (r.source === 'llm' && (part.causes?.length || part.actions?.length) ? `<p class="muted small-text">${esc(t('wd.confirm'))}</p>` : '');
    }
    $('#wdFacts').innerHTML = factRows(r.facts).map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join('');
    clearTimeout(st.poll);
    if (r.pending) st.poll = setTimeout(() => loadReport(true), 4000);
}

async function loadReport(quiet = false) {
    const mine = st.seq;
    try {
        const r = await apiGet(`/api/wo-report${queryString(keyParams())}`);
        if (mine !== st.seq) return;
        renderReport(r);
    } catch (err) {
        if (!quiet) $('#wdReportState').innerHTML = `<div class="notice bad">${esc(err.message)}</div>`;
    }
}

/* ─── Follow-up questions ─── */
function renderAsk() {
    const log = $('#wdAskLog');
    log.innerHTML = st.ask.map(x => `<div class="msg user">${esc(x.q)}</div>`
        + (x.a === null ? '<div class="msg assistant typing"></div>' : `<div class="msg assistant${x.refused ? ' refused' : ''}">${esc(x.a)}</div>`)).join('')
        || `<div class="msg note">${esc(t('wd.askHint'))}</div>`;
    log.scrollTop = log.scrollHeight;
    $('#wdAskChips').innerHTML = ['wd.q1', 'wd.q2', 'wd.q3', 'wd.q4'].map(k => `<button class="btn small" type="button" data-q="${esc(t(k))}">${esc(t(k))}</button>`).join('');
}

async function ask(question) {
    question = (question || '').trim();
    if (!question || st.ask.some(x => x.a === null)) return;
    const history = st.ask.filter(x => x.a !== null && !x.refused).slice(-3).map(x => ({ q: x.q, a: x.a }));
    const item = { q: question, a: null };
    st.ask.push(item);
    $('#wdAskInput').value = '';
    renderAsk();
    try {
        const r = await apiPost('/api/wo-report/ask', { ...keyParams(), question, lang: getLang(), history });
        item.a = r.answer;
        item.refused = r.refused;
    } catch (err) {
        item.a = err.message;
        item.refused = true;
    }
    renderAsk();
}

/* ─── FAIL list, batches ─── */
async function loadFails() {
    const tbody = $('#wdFailRows');
    $('#wdFailsLink').hidden = true;
    try {
        const data = await apiGet(`/api/fail-list${queryString({ ...keyParams(), page_size: 50 })}`);
        if (!data.items.length) { tableState(tbody, 10, 'empty', t('wd.noFails')); return; }
        tbody.innerHTML = data.items.map(r => `<tr>
            <td class="nowrap">${esc(fmtDateTime(r.test_time))}</td><td><code>${esc(r.sn)}</code></td><td><code>${esc(r.wifi_mac || '—')}</code></td>
            <td class="nowrap">${categoryBadge(r.analysis, r.fail_reason)}</td><td class="nowrap">${runSeqHtml(r.analysis)}</td>
            <td class="nowrap small-text">${esc(stationText(r.analysis))}</td>
            <td class="num">${fmtNum(r.avg_24g, 1)}</td><td class="num">${fmtNum(r.avg_5g, 1)}</td><td class="num">${fmtInt(r.sn_runs)}</td>
            <td><button class="btn small" type="button" data-log="${r.id}">${esc(t('fails.download'))}</button></td></tr>`).join('');
        if (data.total > data.items.length) { $('#wdFailsLink').hidden = false; $('#wdFailsLink').textContent = t('wd.failsAll', { n: fmtInt(data.total) }); }
    } catch (err) {
        tableState(tbody, 10, 'error', t('common.loadFailedMsg', { error: err.message }), loadFails);
    }
}

async function loadBatches() {
    const card = $('#wdBatchCard');
    card.hidden = !auth.isSignedIn();
    if (card.hidden) return;
    const tbody = $('#wdBatchRows');
    try {
        const data = await apiGet(`/api/wo-batches${queryString({ work_order: st.key.wo })}`);
        if (!data.items.length) { tableState(tbody, 8, 'empty', t('wd.noBatches')); return; }
        tbody.innerHTML = data.items.map(b => `<tr><td class="nowrap">${esc(fmtDateTime(b.created_at))}</td><td>${esc(b.display_name || b.username)}</td>
            <td>${esc(b.source_name || '—')}</td><td class="num">${fmtInt(b.planned_qty)}</td><td class="num">${fmtInt(b.uploaded)}</td>
            <td class="num">${fmtInt(b.skipped)}</td><td class="num">${fmtInt(b.failed)}</td>
            <td><span class="badge ${esc(b.status)}">${esc(t(`lu.status.${b.status}`))}</span></td></tr>`).join('');
    } catch (err) {
        tableState(tbody, 8, 'error', t('common.loadFailedMsg', { error: err.message }), loadBatches);
    }
}

/* ─── Plan (administrator) ─── */
function openPlan() {
    const plan = st.data?.plan;
    openFormModal({
        title: t('wd.setPlanTitle', { wo: st.key.wo }),
        desc: t('wd.setPlanDesc'),
        fields: [{ name: 'planned_qty', label: t('lu.qty'), type: 'number', value: plan?.planned_qty ?? '', hint: t('wd.setPlanHint') }],
        submitLabel: t('common.save'),
        onSubmit: async values => {
            const raw = String(values.planned_qty || '').trim();
            if (!raw) {
                if (!plan) return null;
                await apiDelete(`/api/wo-plan${queryString({ work_order: st.key.wo })}`);
            } else {
                const qty = Number(raw);
                if (!Number.isInteger(qty) || qty < 1) return t('wd.planInvalid');
                await request('/api/wo-plan', { method: 'PUT', body: { ...keyParams(), planned_qty: qty } });
            }
            toast(t('wd.planSaved'));
            await load(appRef);
            return null;
        },
    });
}

/* ─── Page ─── */
async function load(app) {
    appRef = app;
    const p = params();
    st.seq += 1;
    const mine = st.seq;
    clearTimeout(st.poll);
    if (!st.key || st.key.wo !== p.wo || st.key.pm !== p.pm) st.ask = [];
    st.key = { wo: p.wo, pm: p.pm };
    $('#wdMissing').hidden = true;
    if (!p.wo) { $('#wdPanel').hidden = true; $('#wdMissing').hidden = false; $('#wdMissingText').textContent = t('wd.pick'); return; }
    try {
        const d = await apiGet(`/api/wo-detail${queryString(keyParams())}`);
        if (mine !== st.seq) return;
        st.data = d;
        $('#wdPanel').hidden = false;
        renderHead(d);
        renderTiles(d);
        renderChecks(d);
        renderFailCats(d);
        fillCompareSelect(d, p.cmp ? [p.cmp, p.cpm] : null);
        chooseCompare($('#wdCompareSel').value);
        renderCharts(d);
        renderAsk();
        await Promise.all([loadReport(), loadFails(), loadBatches()]);
        if (p.focus === 'report') $('#wdReportCard').scrollIntoView({ block: 'start' });
    } catch (err) {
        if (mine !== st.seq) return;
        $('#wdPanel').hidden = true;
        $('#wdMissing').hidden = false;
        $('#wdMissingText').textContent = err.status === 404 ? t('wd.notFound', { wo: p.wo }) : t('common.loadFailedMsg', { error: err.message });
    }
}

function init(app) {
    appRef = app;
    $('#wdCompareSel').addEventListener('change', e => chooseCompare(e.target.value));
    $('#btnWdPlan').addEventListener('click', openPlan);
    $('#btnWdRegen').addEventListener('click', async () => {
        try { renderReport(await apiPost('/api/wo-report/generate', keyParams())); }
        catch (err) { toast(err.message, 'bad'); }
    });
    $('#btnWdCopy').addEventListener('click', async () => {
        const text = reportMarkdown(st.report, getLang());
        try { await navigator.clipboard.writeText(text); toast(t('rp.copied')); } catch { toast(t('rp.copyFailed'), 'bad'); }
    });
    $('#btnWdAsk').addEventListener('click', () => ask($('#wdAskInput').value));
    $('#wdAskInput').addEventListener('keydown', e => { if (e.key === 'Enter') ask(e.target.value); });
    $('#wdAskChips').addEventListener('click', e => { const b = e.target.closest('[data-q]'); if (b) ask(b.dataset.q); });
    $('#wdFailsLink').addEventListener('click', e => {
        e.preventDefault();
        appRef.setScope({ range: 'all', product: st.key.pm, workOrder: st.key.wo });
        location.hash = 'fails';
    });
    $('#wdFailRows').addEventListener('click', async e => {
        const btn = e.target.closest('[data-log]');
        if (!btn) return;
        try { await download(`/api/raw-log/${btn.dataset.log}`, `record_${btn.dataset.log}.txt`); } catch (err) { toast(err.message, 'bad'); }
    });
}

export default {
    id: 'wo', scoped: false, init, load,
    isBusy: () => st.ask.some(x => x.a === null),
    onAuthChange: () => { if (appRef?.page?.id === 'wo' && st.data) { $('#btnWdPlan').hidden = !auth.isAdmin(); loadBatches(); } },
    onLangChange: () => { if (st.data) { renderHead(st.data); renderTiles(st.data); renderChecks(st.data); renderFailCats(st.data); chooseCompare($('#wdCompareSel').value); renderAsk(); if (st.report) renderReport(st.report); } },
};
