// Advanced Analysis (5F-2, RD and the administrator only, Q34): query FAIL runs by category, product, station,
// time, retest outcome and measurements; Pareto and cross tables; batch triage (category, stage, RD status, owner,
// note, instruction for the line); a unit's history with a log viewer that compares two logs; similar cases;
// CSV / log export; "Ask AI" on the filtered group's aggregates; taxonomy maintenance.
import { apiGet, apiPost, download, request } from '../core/api.js';
import * as auth from '../core/auth.js';
import * as charts from '../core/charts.js';
import { esc, fmtDateTime, fmtInt, fmtNum } from '../core/format.js';
import { getLang, localized, t } from '../core/i18n.js';
import { detailHref, queryString } from '../core/scope.js';
import { $, $$, debounce, openFormModal, tableState, toast } from '../core/ui.js';
import { categoryBadge, openFailDetail, runSeqHtml, stationText } from './fails.js';

const PAGE_SIZE = 50;
const LIST_COLS = 13;
const SELECTS = ['category', 'band', 'ssid_group', 'station_port', 'retest', 'classifier', 'rd_status', 'stage'];
const RANGES = ['mbps_min', 'mbps_max', 'rssi_min', 'rssi_max'];
const st = { tab: 'overview', page: 1, sort: 'time', selected: new Set(), taxonomy: [], stages: [], facets: null, key: '',
             unit: null, logA: null, logB: null, signalOnly: false, lastAsk: null };
let appRef;
let seq = 0;

/* ─── Access ─── */
function allowed() { return auth.isAdmin() || auth.isRd(); }

function renderAccess() {
    $('#rdLogin').hidden = auth.isSignedIn();
    $('#rdDenied').hidden = !auth.isSignedIn() || allowed();
    $('#rdPanel').hidden = !allowed();
}

/* ─── Filters: the filter bar (range, product, work order) + this page's own ─── */
function filters() {
    const f = { ...appRef.params() };
    for (const k of SELECTS) { const v = $(`#rdF_${k}`).value; if (v) f[k] = v; }
    for (const k of RANGES) { const v = $(`#rdF_${k}`).value.trim(); if (v !== '' && Number.isFinite(Number(v))) f[k] = v; }
    const s = $('#rdSearch').value.trim();
    if (s) f.search = s;
    if ($('#rdStill').checked) f.still_failing = '1';
    return f;
}

const catName = code => { const c = st.taxonomy.find(x => x.code === code); return c ? localized(c.name_zh, c.name_en) : code; };
const stageName = code => { const s = st.stages.find(x => x.code === code); return s ? localized(s.name_zh, s.name_en) : code; };

function valueLabel(key, v) {
    if (v === '—' || v == null) return t('rd.none');
    if (key === 'category') return catName(v);
    if (key === 'stage') return stageName(v);
    if (key === 'retest') return t(`fails.retest.${v}`);
    if (key === 'rd_status') return t(`rd.status.${v}`);
    if (key === 'classifier') return t(`rd.classifier.${v}`);
    if (key === 'band') return { '24g': '2.4G', '5g': '5G', bt: 'BT' }[v] || v;
    if (key === 'station_port') return String(v).replace('/dev/tty', '');
    return v;
}

function renderFacets(facets) {
    st.facets = facets;
    for (const k of SELECTS) {
        const sel = $(`#rdF_${k}`);
        const cur = sel.value;
        sel.innerHTML = '';
        sel.appendChild(new Option(t(`rd.f.${k}`), ''));
        (facets[k] || []).forEach(o => sel.appendChild(new Option(`${valueLabel(k, o.value)} (${fmtInt(o.n)})`, o.value)));
        sel.value = cur;
        if (sel.value !== cur) sel.value = '';
    }
}

/* ─── Overview: Pareto + cross table + Ask AI ─── */
function dimLabel(by, key) {
    if (by === 'category') return catName(key);
    if (by === 'stage') return key === '—' ? t('rd.none') : stageName(key);
    if (by === 'retest') return key === '—' ? t('rd.none') : t(`fails.retest.${key}`);
    if (by === 'rd_status') return t(`rd.status.${key}`);
    if (by === 'classifier') return t(`rd.classifier.${key}`);
    if (by === 'weekday') return t(`rd.wd${key}`);
    if (by === 'hour') return `${key}:00`;
    if (by === 'band') return { '24g': '2.4G', '5g': '5G', bt: 'BT' }[key] || key;
    return key;
}

async function loadGroup() {
    const by = $('#rdGroupBy').value;
    try {
        const g = await apiGet(`/api/rd/group${queryString({ ...filters(), by })}`);
        $('#rdGroupNote').textContent = t('rd.groupNote', { n: fmtInt(g.total) });
        if (!g.items.length) { charts.chartMessage('chRdPareto', t('common.empty')); return; }
        const timeAxis = ['week', 'month', 'day', 'hour', 'weekday'].includes(by);
        charts.render('chRdPareto', c => ({
            data: {
                labels: g.items.map(i => dimLabel(by, i.key)),
                datasets: [
                    { type: 'bar', label: t('rd.runs'), data: g.items.map(i => i.runs), backgroundColor: charts.alpha(c.fail, 0.8), borderRadius: 3, yAxisID: 'y', order: 1 },
                    { type: 'bar', label: t('rd.passedRetest'), data: g.items.map(i => i.passed_retest), backgroundColor: charts.alpha(c.pass, 0.7), borderRadius: 3, yAxisID: 'y', order: 2 },
                    ...(timeAxis ? [] : [{ type: 'line', label: t('rd.cum'), data: g.items.map(i => i.cum_pct), borderColor: c.warn, pointRadius: 3, yAxisID: 'y1', order: 0 }]),
                ],
            },
            options: {
                interaction: { mode: 'index', intersect: false },
                scales: {
                    x: { ticks: { autoSkip: false, maxRotation: 45, minRotation: 0 } },
                    y: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: t('rd.runs') } },
                    ...(timeAxis ? {} : { y1: { position: 'right', min: 0, max: 100, grid: { drawOnChartArea: false }, title: { display: true, text: '%' } } }),
                },
            },
        }));
    } catch (err) {
        charts.chartMessage('chRdPareto', t('common.loadFailedMsg', { error: err.message }), 'error');
    }
}

async function loadCross() {
    const rows = $('#rdCrossRows').value;
    const cols = $('#rdCrossCols').value;
    const box = $('#rdCross');
    try {
        const x = await apiGet(`/api/rd/crosstab${queryString({ ...filters(), rows, cols, top: 9 })}`);
        if (!x.rows.length) { box.innerHTML = `<div class="state-msg">${esc(t('common.empty'))}</div>`; return; }
        const max = Math.max(...x.rows.flatMap(r => r.cells), 1);
        const head = `<tr><th></th>${x.cols.map(c => `<th class="num">${esc(dimLabel(cols, c))}</th>`).join('')}${x.other_total ? `<th class="num">${esc(t('rd.other'))}</th>` : ''}<th class="num">${esc(t('rd.total'))}</th></tr>`;
        const body = x.rows.map(r => `<tr><th>${esc(dimLabel(rows, r.key))}</th>${r.cells.map(v => `<td class="num rd-heat" style="--h:${(v / max).toFixed(2)}">${v ? fmtInt(v) : ''}</td>`).join('')}${x.other_total ? `<td class="num muted">${r.other ? fmtInt(r.other) : ''}</td>` : ''}<td class="num"><b>${fmtInt(r.total)}</b></td></tr>`).join('');
        const foot = `<tr><th>${esc(t('rd.total'))}</th>${x.col_totals.map(v => `<td class="num"><b>${fmtInt(v)}</b></td>`).join('')}${x.other_total ? `<td class="num"><b>${fmtInt(x.other_total)}</b></td>` : ''}<td class="num"><b>${fmtInt(x.total)}</b></td></tr>`;
        box.innerHTML = `<div class="table-wrap"><table class="rd-cross"><thead>${head}</thead><tbody>${body}${foot}</tbody></table></div>`;
    } catch (err) {
        box.innerHTML = `<div class="state-msg error">${esc(t('common.loadFailedMsg', { error: err.message }))}</div>`;
    }
}

function renderAsk(r) {
    const box = $('#rdAskOut');
    if (!r) { box.innerHTML = ''; return; }
    if (r.empty) { box.innerHTML = `<p class="muted">${esc(t('rd.askEmpty'))}</p>`; return; }
    const a = r.answer || {};
    const list = (title, items) => items?.length ? `<h4>${esc(title)}</h4><ul>${items.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '';
    box.innerHTML = `<div class="wd-report">${a.summary ? `<div class="wd-summary">${esc(a.summary)}</div>` : ''}
        ${list(t('rd.askCommon'), a.common)}${list(t('rd.askCauses'), a.causes)}${list(t('rd.askChecks'), a.checks)}</div>
        <div class="muted small-text mt-s">${esc(t('rd.askMeta', { n: fmtInt(r.facts.fail_runs), units: fmtInt(r.facts.units), s: r.elapsed }))}${r.rejected?.length ? esc(t('common.dot') + t('rd.askRejected', { n: r.rejected.length })) : ''}</div>
        <details class="mt-s"><summary>${esc(t('rd.askFacts'))}</summary><pre class="rd-facts">${esc(JSON.stringify(r.facts, null, 2))}</pre></details>`;
}

async function ask() {
    const btn = $('#btnRdAsk');
    btn.disabled = true;
    $('#rdAskOut').innerHTML = `<div class="rp-pending"><span class="spinner"></span>${esc(t('rd.askWorking'))}</div>`;
    try {
        const f = filters();
        st.lastAsk = await apiPost('/api/rd/ask', { filters: f, question: $('#rdAskInput').value.trim() || null, lang: getLang() });
        renderAsk(st.lastAsk);
    } catch (err) {
        $('#rdAskOut').innerHTML = `<p class="bad-text">${esc(err.message)}</p>`;
    } finally {
        btn.disabled = false;
    }
}

/* ─── List + batch triage ─── */
function rdCell(r) {
    const bits = [];
    if (r.rd_action) bits.push(`<span class="rd-action" title="${esc(t('rd.actionTitle'))}">→ ${esc(r.rd_action)}</span>`);
    if (r.rd_note) bits.push(`<span class="muted" title="${esc(r.rd_note)}">✎ ${esc(r.rd_note.length > 40 ? `${r.rd_note.slice(0, 40)}…` : r.rd_note)}</span>`);
    if (r.rd_owner) bits.push(`<span class="muted">@${esc(r.rd_owner)}</span>`);
    return bits.join('<br>') || '—';
}

function renderList(data) {
    const tbody = $('#rdRows');
    $('#rdCount').textContent = t('rd.count', { n: fmtInt(data.total) });
    if (!data.items.length) { tableState(tbody, LIST_COLS, 'empty'); return; }
    tbody.innerHTML = data.items.map(r => {
        const a = { ...r, name_zh: r.name_zh, name_en: r.name_en };
        const mb = r.mbps != null ? `${fmtNum(r.mbps, 1)}${r.threshold != null ? `<span class="muted"> / ${fmtNum(r.threshold, 0)}</span>` : ''}` : '—';
        return `<tr class="${st.selected.has(r.record_id) ? 'rd-picked' : ''}">
            <td><input type="checkbox" class="wo-pick" data-pick="${r.record_id}" ${st.selected.has(r.record_id) ? 'checked' : ''} aria-label="${esc(t('rd.pick'))}"></td>
            <td class="nowrap">${esc(fmtDateTime(r.test_time))}</td>
            <td class="nowrap">${r.work_order ? `<a class="wo-link" href="${esc(detailHref(r.work_order, r.product_model))}">${esc(r.work_order)}</a>` : '—'}</td>
            <td class="nowrap small-text">${esc(r.product_model || '—')}</td>
            <td><button class="link-btn mono" type="button" data-detail="${r.record_id}">${esc(r.sn || '—')}</button>
                <button class="link-btn small-text" type="button" data-unit="${esc(r.sn || '')}" title="${esc(t('rd.unitTitle'))}">⏱</button></td>
            <td class="nowrap">${categoryBadge(a)}</td>
            <td class="num nowrap">${mb}</td>
            <td class="num">${r.rssi != null ? fmtNum(r.rssi, 0) : '—'}</td>
            <td class="nowrap small-text">${esc(stationText(r))}</td>
            <td class="nowrap">${runSeqHtml(r)}</td>
            <td class="nowrap"><span class="badge ${r.rd_status === 'new' ? 'muted' : r.rd_status === 'closed' ? 'good' : 'warn'}">${esc(t(`rd.status.${r.rd_status}`))}</span>
                <div class="muted small-text">${esc(t(`rd.classifier.${r.classifier}`))}${r.stage ? ` · ${esc(stageName(r.stage))}` : ''}</div></td>
            <td class="rd-cell small-text">${rdCell(r)}</td>
            <td><button class="btn small" type="button" data-log="${r.record_id}">${esc(t('rd.logBtn'))}</button></td>
        </tr>`;
    }).join('');
}

function renderBatch() {
    const n = st.selected.size;
    $('#rdBatch').hidden = n === 0;
    $('#rdBatchCount').textContent = t('rd.selected', { n: fmtInt(n) });
}

async function loadList() {
    const mine = ++seq;
    const tbody = $('#rdRows');
    try {
        const data = await apiGet(`/api/rd/fails${queryString({ ...filters(), page: st.page, page_size: PAGE_SIZE, sort: st.sort })}`);
        if (mine !== seq) return;
        st.page = data.page;
        renderFacets(data.facets);
        renderList(data);
        st.listItems = data.items;
        st.listTotal = data.total;
        renderPagerInto($('#rdPager'), data);
        renderBatch();
    } catch (err) {
        if (mine !== seq) return;
        tableState(tbody, LIST_COLS, 'error', t('common.loadFailedMsg', { error: err.message }), loadList);
    }
}

function renderPagerInto(el, data) {
    el.innerHTML = '';
    if (!data.total) return;
    const info = document.createElement('span');
    info.textContent = t('common.pageInfo', { page: data.page, pages: data.pages, total: fmtInt(data.total) });
    const mk = (label, page, disabled) => {
        const b = document.createElement('button');
        b.className = 'btn small'; b.textContent = label; b.disabled = disabled;
        b.addEventListener('click', () => { st.page = page; loadList(); });
        return b;
    };
    el.append(info, mk(t('common.prev'), data.page - 1, data.page <= 1), mk(t('common.next'), data.page + 1, data.page >= data.pages));
}

function batchOptions(sel, items, labelFn, keep = t('rd.keep')) {
    sel.innerHTML = '';
    sel.appendChild(new Option(keep, ''));
    items.forEach(v => sel.appendChild(new Option(labelFn(v), v)));
}

async function applyBatch() {
    const changes = {};
    if ($('#rdB_category').value) changes.category = $('#rdB_category').value;
    if ($('#rdB_stage').value) changes.stage = $('#rdB_stage').value === '-' ? null : $('#rdB_stage').value;
    if ($('#rdB_status').value) changes.rd_status = $('#rdB_status').value;
    for (const [id, key] of [['#rdB_owner', 'rd_owner'], ['#rdB_note', 'rd_note'], ['#rdB_action', 'rd_action']]) {
        const v = $(id).value;
        if (v.trim()) changes[key] = v.trim();
        if (v.trim() === '-') changes[key] = null;      // "-" clears the field
    }
    const msg = $('#rdBatchMsg');
    if (!Object.keys(changes).length) { msg.textContent = t('rd.batchNothing'); return; }
    if (!confirm(t('rd.batchConfirm', { n: st.selected.size }))) return;
    try {
        const r = await request('/api/rd/fails', { method: 'PATCH', body: { ids: [...st.selected], changes } });
        toast(t('rd.batchDone', { n: fmtInt(r.updated) }));
        st.selected.clear();
        ['#rdB_category', '#rdB_stage', '#rdB_status'].forEach(id => { $(id).value = ''; });
        ['#rdB_owner', '#rdB_note', '#rdB_action'].forEach(id => { $(id).value = ''; });
        msg.textContent = '';
        loadList();
    } catch (err) {
        msg.textContent = err.message;
    }
}

/* ─── Unit history + log viewer ─── */
async function loadUnit(sn) {
    st.unit = null; st.logA = null; st.logB = null;
    $('#rdUnitSn').value = sn;
    const box = $('#rdUnitRuns');
    $('#rdLogs').innerHTML = '';
    $('#rdSimilar').innerHTML = '';
    if (!sn) { box.innerHTML = ''; return; }
    box.innerHTML = `<div class="state-msg">${esc(t('common.loading'))}</div>`;
    try {
        st.unit = await apiGet(`/api/rd/sn/${encodeURIComponent(sn)}`);
        const runs = st.unit.runs;
        box.innerHTML = `<div class="table-wrap"><table><thead><tr><th>A</th><th>B</th><th>${esc(t('fails.col.time'))}</th><th>${esc(t('fails.col.result'))}</th>
            <th>${esc(t('wo.col.wo'))}</th><th>${esc(t('fails.col.category'))}</th><th>${esc(t('fails.col.station'))}</th><th>WiFi MAC</th>
            <th class="num">${esc(t('fails.col.tp24'))}</th><th class="num">${esc(t('fails.col.tp5'))}</th><th>${esc(t('rd.file'))}</th><th></th></tr></thead><tbody>
            ${runs.map((r, i) => `<tr><td><input type="radio" name="rdA" value="${r.id}" ${i === runs.length - 1 ? 'checked' : ''} aria-label="A"></td>
                <td><input type="radio" name="rdB" value="${r.id}" ${i === runs.length - 2 ? 'checked' : ''} aria-label="B"></td>
                <td class="nowrap">${esc(fmtDateTime(r.test_time))}</td><td><span class="badge ${r.result ? 'pass' : 'fail'}">${r.result ? 'PASS' : 'FAIL'}</span></td>
                <td class="nowrap">${esc(r.work_order || '—')}</td><td>${r.category ? esc(localized(r.name_zh, r.name_en) || r.category) : ''}</td>
                <td class="nowrap small-text">${esc(stationText(r))}</td><td><code>${esc(r.wifi_mac || '—')}</code></td>
                <td class="num">${fmtNum(r.avg_24g, 1)}</td><td class="num">${fmtNum(r.avg_5g, 1)}</td><td class="small-text">${esc(r.source_file || '')}</td>
                <td>${r.result ? '' : `<button class="btn small" type="button" data-similar="${r.id}">${esc(t('rd.similarBtn'))}</button>`}</td></tr>`).join('')}
            </tbody></table></div>
            <div class="toolbar mt-s"><button class="btn primary" type="button" id="btnRdShowLogs">${esc(t('rd.showLogs'))}</button>
            <label class="rd-check"><input type="checkbox" id="rdSignalOnly" ${st.signalOnly ? 'checked' : ''}> ${esc(t('rd.signalOnly'))}</label></div>`;
        $('#btnRdShowLogs').addEventListener('click', showLogs);
        $('#rdSignalOnly').addEventListener('change', e => { st.signalOnly = e.target.checked; renderLogs(); });
        showLogs();
    } catch (err) {
        box.innerHTML = `<div class="state-msg error">${esc(err.message)}</div>`;
    }
}

async function showLogs() {
    const a = $('input[name="rdA"]:checked')?.value;
    const b = $('input[name="rdB"]:checked')?.value;
    try {
        [st.logA, st.logB] = await Promise.all([a ? apiGet(`/api/rd/log/${a}`) : null, b && b !== a ? apiGet(`/api/rd/log/${b}`) : null]);
        renderLogs();
    } catch (err) {
        $('#rdLogs').innerHTML = `<div class="state-msg error">${esc(err.message)}</div>`;
    }
}

function logPane(l) {
    if (!l) return '';
    const ev = new Set(l.evidence_lines);
    const sig = new Set(l.signal_lines);
    const lines = l.lines.map((text, i) => ({ n: i + 1, text })).filter(x => !st.signalOnly || ev.has(x.n) || sig.has(x.n));
    return `<div class="rd-log"><div class="rd-log-head"><b>${esc(fmtDateTime(l.test_time))}</b> <span class="badge ${l.result ? 'pass' : 'fail'}">${l.result ? 'PASS' : 'FAIL'}</span>
        <span class="muted small-text">${esc(l.source_file || '')} · ${esc(t('rd.lines', { n: fmtInt(l.lines.length), ev: ev.size }))}</span>
        <button class="btn small" type="button" data-log="${l.id}">${esc(t('fails.download'))}</button></div>
        <div class="rd-log-body">${lines.map(x => `<div class="${ev.has(x.n) ? 'ev' : sig.has(x.n) ? 'sig' : ''}" id="rdL${l.id}_${x.n}"><span class="ln">${x.n}</span>${esc(x.text)}</div>`).join('')}</div></div>`;
}

function renderLogs() {
    $('#rdLogs').innerHTML = `<div class="rd-logs ${st.logB ? 'two' : ''}">${logPane(st.logA)}${logPane(st.logB)}</div>`;
    for (const l of [st.logA, st.logB]) {       // scroll each pane to its first evidence line
        if (l?.evidence_lines?.length) document.getElementById(`rdL${l.id}_${l.evidence_lines[0]}`)?.scrollIntoView({ block: 'center' });
    }
    window.scrollTo({ top: $('#rdLogs').offsetTop - 120 });
}

async function loadSimilar(id) {
    const box = $('#rdSimilar');
    box.innerHTML = `<div class="state-msg">${esc(t('common.loading'))}</div>`;
    try {
        const s = await apiGet(`/api/rd/similar/${id}`);
        const rows = items => items.map(r => `<tr><td class="nowrap">${esc(fmtDateTime(r.test_time))}</td><td class="nowrap">${esc(r.work_order || '—')}</td>
            <td><button class="link-btn mono" type="button" data-unit="${esc(r.sn)}">${esc(r.sn)}</button></td><td>${esc(localized(r.name_zh, r.name_en) || r.category)}</td>
            <td class="nowrap small-text">${esc(stationText(r))}</td><td>${esc(t(`fails.retest.${r.retest_outcome || 'not_retested'}`))}</td></tr>`).join('');
        const table = items => items.length ? `<div class="table-wrap"><table><tbody>${rows(items)}</tbody></table></div>` : `<p class="muted">${esc(t('rd.noneFound'))}</p>`;
        box.innerHTML = `<div class="grid cols-2">
            <div><div class="card-title mb-s">${esc(t('rd.sameSig', { n: fmtInt(s.same_signature_total || 0) }))}</div>
                 ${s.signature ? `<code class="small-text">${esc(s.signature)}</code>` : ''}${table(s.same_signature)}</div>
            <div><div class="card-title mb-s">${esc(t('rd.sameStation'))}</div>${table(s.same_station)}</div></div>`;
    } catch (err) {
        box.innerHTML = `<div class="state-msg error">${esc(err.message)}</div>`;
    }
}

/* ─── Taxonomy ─── */
async function loadTaxonomy() {
    const tax = await apiGet('/api/fail-taxonomy');
    st.taxonomy = tax.items;
    st.stages = tax.stages;
    batchOptions($('#rdB_category'), tax.items.filter(x => x.enabled).map(x => x.code), catName);
    batchOptions($('#rdB_stage'), ['-', ...tax.stages.map(s => s.code)], v => (v === '-' ? t('rd.clear') : stageName(v)));
    batchOptions($('#rdB_status'), ['new', 'confirmed', 'reclassified', 'closed'], v => t(`rd.status.${v}`));
    renderTaxonomy();
}

function renderTaxonomy() {
    $('#rdTaxRows').innerHTML = st.taxonomy.map(x => `<tr class="${x.enabled ? '' : 'muted'}">
        <td><code>${esc(x.code)}</code></td><td>${esc(x.name_zh)}<div class="muted small-text">${esc(x.name_en)}</div></td>
        <td class="small-text">${esc(localized(x.description_zh, x.description_en) || '')}</td>
        <td class="small-text">${esc(localized(x.action_zh, x.action_en) || '')}</td>
        <td>${esc(stageName(x.likely_stage))}</td><td class="num">${x.severity}</td><td class="num">${fmtInt(x.runs)}</td>
        <td>${x.enabled ? `<span class="badge good">${esc(t('rd.enabled'))}</span>` : `<span class="badge muted">${esc(x.merged_into ? t('rd.mergedInto', { code: x.merged_into }) : t('rd.disabled'))}</span>`}</td>
        <td class="nowrap"><button class="btn small" type="button" data-tax-edit="${esc(x.code)}">${esc(t('rd.edit'))}</button>
            ${x.enabled && x.code !== 'UNKNOWN' ? `<button class="btn small" type="button" data-tax-merge="${esc(x.code)}">${esc(t('rd.merge'))}</button>` : ''}</td></tr>`).join('');
}

function taxForm(x) {
    const creating = !x;
    const fields = [
        ...(creating ? [{ name: 'code', label: t('rd.tax.code'), required: true, maxlength: 32, hint: t('rd.tax.codeHint') }] : []),
        { name: 'name_zh', label: t('rd.tax.nameZh'), required: true, value: x?.name_zh, maxlength: 60 },
        { name: 'name_en', label: t('rd.tax.nameEn'), required: true, value: x?.name_en, maxlength: 80 },
        { name: 'description_zh', label: t('rd.tax.descZh'), value: x?.description_zh, maxlength: 300 },
        { name: 'description_en', label: t('rd.tax.descEn'), value: x?.description_en, maxlength: 400 },
        { name: 'action_zh', label: t('rd.tax.actionZh'), value: x?.action_zh, maxlength: 200 },
        { name: 'action_en', label: t('rd.tax.actionEn'), value: x?.action_en, maxlength: 300 },
        { name: 'likely_stage', label: t('rd.tax.stage'), type: 'select', value: x?.likely_stage || 'unknown', options: st.stages.map(s => ({ value: s.code, label: stageName(s.code) })) },
        { name: 'severity', label: t('rd.tax.severity'), type: 'number', value: x?.severity ?? 5, hint: t('rd.tax.severityHint') },
        ...(creating || x.code === 'UNKNOWN' ? [] : [{ name: 'enabled', label: t('rd.tax.enabled'), type: 'select', value: String(x.enabled), options: [{ value: 'true', label: t('rd.enabled') }, { value: 'false', label: t('rd.disabled') }] }]),
    ];
    openFormModal({
        title: creating ? t('rd.tax.add') : t('rd.tax.editTitle', { code: x.code }), fields, submitLabel: t('fails.fd.save'),
        onSubmit: async v => {
            const body = { ...v, severity: Number(v.severity) };
            if ('enabled' in v) body.enabled = v.enabled === 'true';
            if (!creating) delete body.code;
            try {
                const r = creating ? await apiPost('/api/fail-taxonomy', body) : await request(`/api/fail-taxonomy/${encodeURIComponent(x.code)}`, { method: 'PATCH', body });
                st.taxonomy = r.items;
                renderTaxonomy();
                loadTaxonomy();
                toast(t('rd.tax.saved'));
                return null;
            } catch (err) { return err.message; }
        },
    });
}

function mergeForm(code) {
    const targets = st.taxonomy.filter(x => x.enabled && x.code !== code);
    openFormModal({
        title: t('rd.tax.mergeTitle', { name: catName(code) }), desc: t('rd.tax.mergeDesc'),
        fields: [{ name: 'into', label: t('rd.tax.into'), type: 'select', options: targets.map(x => ({ value: x.code, label: `${catName(x.code)} (${x.code})` })) }],
        submitLabel: t('rd.merge'),
        onSubmit: async v => {
            try {
                const r = await apiPost(`/api/fail-taxonomy/${encodeURIComponent(code)}/merge`, { into: v.into });
                st.taxonomy = r.items;
                renderTaxonomy();
                toast(t('rd.tax.merged', { n: fmtInt(r.moved) }));
                loadTaxonomy();
                return null;
            } catch (err) { return err.message; }
        },
    });
}

/* ─── Page ─── */
function showTab(tab) {
    st.tab = tab;
    $$('[data-rd-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.rdTab === tab)));
    $$('[data-rd-view]').forEach(v => { v.hidden = v.dataset.rdView !== tab; });
    loadTab();
}

function loadTab() {
    if (st.tab === 'overview') { loadGroup(); loadCross(); }
    else if (st.tab === 'list') loadList();
    else if (st.tab === 'taxonomy') renderTaxonomy();
}

function refilter() { st.page = 1; st.selected.clear(); loadTab(); if (st.tab !== 'list') loadFacetsOnly(); }

async function loadFacetsOnly() {
    try { const d = await apiGet(`/api/rd/fails${queryString({ ...filters(), page_size: 1 })}`); renderFacets(d.facets); $('#rdCount').textContent = t('rd.count', { n: fmtInt(d.total) }); }
    catch { /* the tab itself shows the error */ }
}

async function load(app) {
    appRef = app;
    renderAccess();
    if (!allowed()) return;
    const key = app.scopeKey();
    if (key !== st.key) { st.key = key; st.page = 1; st.selected.clear(); }
    if (!st.taxonomy.length) await loadTaxonomy().catch(() => {});
    if (st.tab !== 'list') loadFacetsOnly();
    loadTab();
}

function init(app) {
    appRef = app;
    auth.bindLoginForm({ user: $('#rdUser'), pass: $('#rdPass'), button: $('#btnRdLogin'), message: $('#rdLoginMsg') });
    $$('[data-rd-tab]').forEach(b => b.addEventListener('click', () => showTab(b.dataset.rdTab)));
    SELECTS.forEach(k => $(`#rdF_${k}`).addEventListener('change', refilter));
    RANGES.forEach(k => $(`#rdF_${k}`).addEventListener('input', debounce(refilter, 500)));
    $('#rdSearch').addEventListener('input', debounce(refilter, 400));
    $('#rdStill').addEventListener('change', refilter);
    $('#btnRdReset').addEventListener('click', () => {
        SELECTS.forEach(k => { $(`#rdF_${k}`).value = ''; });
        RANGES.forEach(k => { $(`#rdF_${k}`).value = ''; });
        $('#rdSearch').value = ''; $('#rdStill').checked = false;
        refilter();
    });
    $('#rdGroupBy').addEventListener('change', loadGroup);
    $('#rdCrossRows').addEventListener('change', loadCross);
    $('#rdCrossCols').addEventListener('change', loadCross);
    $('#btnRdAsk').addEventListener('click', ask);
    $('#rdSort').addEventListener('change', e => { st.sort = e.target.value; st.page = 1; loadList(); });
    $('#btnRdCsv').addEventListener('click', async () => {
        try { await download(`/api/rd/export.csv${queryString(filters())}`, 'fail_analysis.csv'); } catch (err) { toast(err.message, 'bad'); }
    });
    $('#btnRdLogs').addEventListener('click', async () => {
        try { await download(`/api/rd/export-logs.zip${queryString(filters())}`, 'fail_logs.zip'); } catch (err) { toast(err.message, 'bad'); }
    });
    $('#btnRdApply').addEventListener('click', applyBatch);
    $('#btnRdClearSel').addEventListener('click', () => { st.selected.clear(); loadList(); });
    $('#rdPickAll').addEventListener('change', e => {
        (st.listItems || []).forEach(r => { if (e.target.checked) st.selected.add(r.record_id); else st.selected.delete(r.record_id); });
        renderList({ items: st.listItems || [], total: st.listTotal || 0 });
        renderBatch();
    });
    $('#page-rd').addEventListener('click', async e => {
        const pick = e.target.closest('[data-pick]');
        if (pick) {
            const id = Number(pick.dataset.pick);
            if (pick.checked) st.selected.add(id); else st.selected.delete(id);
            pick.closest('tr').classList.toggle('rd-picked', pick.checked);
            renderBatch();
            return;
        }
        const detail = e.target.closest('[data-detail]');
        if (detail) { openFailDetail(detail.dataset.detail, () => { if (st.tab === 'list') loadList(); }); return; }
        const unit = e.target.closest('[data-unit]');
        if (unit && unit.dataset.unit) { showTab('unit'); loadUnit(unit.dataset.unit); return; }
        const sim = e.target.closest('[data-similar]');
        if (sim) { loadSimilar(sim.dataset.similar); return; }
        const log = e.target.closest('[data-log]');
        if (log && st.tab === 'list') {
            const row = (st.listItems || []).find(r => String(r.record_id) === log.dataset.log);
            if (row?.sn) { showTab('unit'); await loadUnit(row.sn); }
            return;
        }
        if (log) { try { await download(`/api/raw-log/${log.dataset.log}`, `record_${log.dataset.log}.txt`); } catch (err) { toast(err.message, 'bad'); } return; }
        const edit = e.target.closest('[data-tax-edit]');
        if (edit) { taxForm(st.taxonomy.find(x => x.code === edit.dataset.taxEdit)); return; }
        const merge = e.target.closest('[data-tax-merge]');
        if (merge) { mergeForm(merge.dataset.taxMerge); return; }
    });
    $('#btnRdUnit').addEventListener('click', () => loadUnit($('#rdUnitSn').value.trim()));
    $('#rdUnitSn').addEventListener('keydown', e => { if (e.key === 'Enter') loadUnit(e.target.value.trim()); });
    $('#btnRdTaxAdd').addEventListener('click', () => taxForm(null));
}

export default {
    id: 'rd', scoped: true, init, load,
    onAuthChange: () => { renderAccess(); if (appRef?.page?.id === 'rd' && allowed()) load(appRef); },
    onLangChange: () => { if (st.lastAsk) renderAsk(st.lastAsk); if (st.taxonomy.length) renderTaxonomy(); },
};
