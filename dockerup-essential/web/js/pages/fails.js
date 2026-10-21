// Fail List: failed units (latest test per SN), paged on the server. 5F: the failure category, run sequence and
// station of each; 5C: the suggested next step, the disposition (pending ones first by default) and a detail
// window with the evidence lines, the unit's runs and the disposition form (any signed-in account, Q13).
import { apiGet, download, request } from '../core/api.js';
import * as auth from '../core/auth.js';
import { esc, fmtDateTime, fmtInt, fmtNum } from '../core/format.js';
import { joinList, localized, t } from '../core/i18n.js';
import { detailHref, queryString } from '../core/scope.js';
import { $, $$, debounce, tableState, toast } from '../core/ui.js';

const PAGE_SIZE = 50;
const COLS = 12;
const DISPOSITIONS = ['open', 'retest', 'repair', 'rd', 'closed'];
const DISP_CLASS = { open: 'bad', retest: 'warn', repair: 'muted', rd: 'muted', closed: 'good', retest_pass: 'good', superseded: 'muted' };
const state = { page: 1, key: '', disp: 'pending' };
let seq = 0;
let appRef;
const fd = { id: null, data: null, onChange: null };

export function renderPager(el, data, onPage) {
    el.innerHTML = '';
    if (!data.total) return;
    const info = document.createElement('span');
    info.textContent = t('common.pageInfo', { page: data.page, pages: data.pages, total: fmtInt(data.total) });
    const prev = document.createElement('button');
    prev.className = 'btn small';
    prev.textContent = t('common.prev');
    prev.disabled = data.page <= 1;
    prev.addEventListener('click', () => onPage(data.page - 1));
    const next = document.createElement('button');
    next.className = 'btn small';
    next.textContent = t('common.next');
    next.disabled = data.page >= data.pages;
    next.addEventListener('click', () => onPage(data.page + 1));
    el.append(info, prev, next);
}

/** The 5F category of a FAIL run as a badge (evidence lines and the suggested action in its tooltip);
 *  the coarse rule reason while the run is not classified yet. */
export function categoryBadge(a, fallbackReason) {
    if (!a) return `<span class="badge fail">${esc(reasonText(fallbackReason))}</span>`;
    const name = localized(a.name_zh, a.name_en) || a.category;
    const lines = (a.evidence || []).slice(0, 4).map(e => `#${e.n} ${e.text}`);
    const action = localized(a.action_zh, a.action_en);
    const title = [name, ...lines, action ? `→ ${action}` : '', a.classifier === 'llm' ? t('fails.byAi') : ''].filter(Boolean).join('\n');
    const more = (a.secondary || []).length ? ` <span class="chip-more" title="${esc(joinList(a.secondary))}">+${a.secondary.length}</span>` : '';
    return `<span class="badge fail cat" title="${esc(title)}">${esc(name)}</span>${more}`;
}

/** The SN's PASS / FAIL history, this run underlined: F F P. */
export function runSeqHtml(a) {
    if (!a?.run_seq) return '—';
    const title = `${t(`fails.retest.${a.retest_outcome || 'not_retested'}`)} · ${t('fails.seqHint')}`;
    return `<span class="runseq" title="${esc(title)}">${[...a.run_seq].map((c, i) =>
        `<b class="${c === 'P' ? 'p' : 'f'}${i === a.run_index ? ' me' : ''}">${c}</b>`).join('')}</span>`;
}

export function stationText(a) {
    if (!a || !(a.ssid_group || a.station_port)) return '—';
    return `${a.ssid_group || '—'} · ${String(a.station_port || '—').replace('/dev/tty', '')}`;
}

/** Translate the server's fail reason (an English phrase) through the dictionary when it has one. */
export function reasonText(reason) {
    const key = `reason.${String(reason || '').replace(/[^a-z0-9]+/gi, '_').toLowerCase()}`;
    const text = t(key);
    return text === key ? (reason || '—') : text;
}

/** The suggested next step (server rules, services/fail_actions.py) in the UI language. */
export function suggestText(s) {
    if (!s) return '—';
    return t(`fails.next.${s.code}`, s.values || {});
}

export function suggestHtml(s, a) {
    if (!s) return '—';
    const action = a ? localized(a.action_zh, a.action_en) : '';
    const cls = { done: 'muted', see_latest: 'muted', check_env: 'warn', retest: '', to_rd: 'bad', rd: 'rd' }[s.code] ?? '';
    return `<span class="fl-next ${cls}" title="${esc(action || '')}">${esc(suggestText(s))}</span>`;
}

export function dispositionHtml(d) {
    if (!d) return '—';
    const by = d.updated_by ? t('fails.dispBy', { who: d.updated_by, time: fmtDateTime(d.updated_at) }) : '';
    const title = [d.auto ? t(`fails.dispAuto.${d.status}`) : '', d.note || '', by].filter(Boolean).join('\n');
    return `<span class="badge ${DISP_CLASS[d.status] || 'muted'}"${title ? ` title="${esc(title)}"` : ''}>${esc(t(`fails.disp.${d.status}`))}</span>${d.note ? ' <span class="chip-more" title="' + esc(d.note) + '">✎</span>' : ''}`;
}

function renderTiles(data, sum) {
    const c = data.counts || { all: 0, pending: 0, handled: 0 };
    const retest = sum?.retest || {};
    const runs = sum?.total_runs || 0;
    const top = sum?.categories?.[0];
    const station = sum?.stations?.[0];
    const tile = (label, value, sub, cls = '') => `<div class="tile"><div class="tile-label">${esc(label)}</div>
        <div class="tile-value ${cls}">${value}</div><div class="tile-sub">${esc(sub)}</div></div>`;
    $('#flTiles').innerHTML = [
        tile(t('fails.tile.units'), fmtInt(c.all), t('fails.tile.unitsSub', { pending: fmtInt(c.pending), handled: fmtInt(c.handled) }), c.pending ? 'v-bad' : ''),
        tile(t('fails.tile.runs'), fmtInt(runs), runs ? t('fails.tile.runsSub', { n: fmtInt(retest.pass_on_retest || 0), pct: fmtNum((retest.pass_on_retest || 0) * 100 / runs, 0) }) : ''),
        tile(t('fails.tile.top'), top ? esc(localized(top.name_zh, top.name_en) || top.category) : '—',
             top ? t('fails.tile.topSub', { runs: fmtInt(top.runs), units: fmtInt(top.units) }) : '', 'small-value'),
        tile(t('fails.tile.station'), station ? esc(stationText(station)) : '—',
             station ? t('fails.tile.topSub', { runs: fmtInt(station.runs), units: fmtInt(station.units) }) : '', 'small-value'),
    ].join('');
}

function renderSeg() {
    $$('#flDispSeg button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.disp === state.disp)));
}

async function load(app = appRef) {
    const key = app.scopeKey();
    if (key !== state.key) { state.key = key; state.page = 1; }
    const mine = ++seq;
    const tbody = $('#failRows');
    renderSeg();
    const params = { ...app.params(), page: state.page, page_size: PAGE_SIZE };
    if (state.disp) params.disposition = state.disp;
    const search = $('#failSearch').value.trim();
    if (search) params.search = search;
    try {
        const [data, sum] = await Promise.all([
            apiGet(`/api/fail-list${queryString(params)}`),
            apiGet(`/api/fail-summary${queryString(app.params())}`).catch(() => null),
        ]);
        if (mine !== seq) return;
        state.page = data.page;
        renderTiles(data, sum);
        $('#failCount').textContent = t('fails.count', { n: fmtInt(data.total) });
        if (!data.items.length) tableState(tbody, COLS, 'empty', state.disp === 'pending' && data.counts?.all ? t('fails.nonePending') : '');
        else {
            tbody.innerHTML = data.items.map(r => `<tr>
                <td class="nowrap">${esc(fmtDateTime(r.test_time))}</td>
                <td class="nowrap">${r.work_order ? `<a class="wo-link" href="${esc(detailHref(r.work_order, r.product_model))}">${esc(r.work_order)}</a>` : '—'}</td>
                <td class="nowrap">${esc(r.product_model || '—')}</td>
                <td><button class="link-btn mono" type="button" data-detail="${r.id}" title="${esc(t('fails.detailTitle'))}">${esc(r.sn)}</button></td>
                <td class="nowrap">${categoryBadge(r.analysis, r.fail_reason)}</td>
                <td class="fl-next-cell">${suggestHtml(r.suggest, r.analysis)}</td>
                <td class="nowrap">${dispositionHtml(r.disposition)}</td>
                <td class="nowrap">${runSeqHtml(r.analysis)}</td>
                <td class="nowrap small-text">${esc(stationText(r.analysis))}</td>
                <td class="num">${fmtNum(r.avg_24g, 1)}</td>
                <td class="num">${fmtNum(r.avg_5g, 1)}</td>
                <td><button class="btn small" type="button" data-log="${r.id}">${esc(t('fails.download'))}</button></td>
            </tr>`).join('');
        }
        renderPager($('#failPager'), data, p => { state.page = p; load(); });
    } catch (err) {
        if (mine !== seq) return;
        tableState(tbody, COLS, 'error', t('common.loadFailedMsg', { error: err.message }), () => load());
        $('#failPager').innerHTML = '';
    }
}

/* ─── FAIL detail window (also opened from the work-order detail page) ─── */

function measureRows(a) {
    const rows = [];
    if (a.band) rows.push([t('fails.fd.band'), a.band === '24g' ? '2.4G' : a.band === '5g' ? '5G' : a.band]);
    if (a.mbps != null) rows.push([t('fails.fd.mbps'), a.threshold != null ? t('fails.fd.mbpsOf', { v: fmtNum(a.mbps, 1), th: fmtNum(a.threshold, 0) }) : fmtNum(a.mbps, 1)]);
    if (a.rssi != null) rows.push(['RSSI', `${fmtNum(a.rssi, 0)} dBm`]);
    if (a.attempts != null) rows.push([t('fails.fd.attempts'), fmtInt(a.attempts)]);
    rows.push([t('fails.col.station'), [a.ssid_group, a.station_port, a.target_ssid].filter(Boolean).join(' · ') || '—']);
    rows.push([t('fails.fd.classifier'), a.classifier === 'llm' ? t('fails.fd.byLlm', { c: fmtNum((a.confidence || 0) * 100, 0) }) : a.classifier === 'rd' ? t('fails.fd.byRd') : t('fails.fd.byRule')]);
    return rows;
}

function renderDetail() {
    const a = fd.data;
    const d = a.disposition;
    $('#fdTitle').textContent = t('fails.fd.title', { sn: a.sn || '—' });
    $('#fdSub').textContent = [a.work_order, a.product_model, fmtDateTime(a.test_time), a.wifi_mac ? `MAC ${a.wifi_mac}` : ''].filter(Boolean).join(t('common.dot'));
    const evidence = (a.evidence || []).map(e => `<li><span class="muted">#${esc(e.n)}</span> <code>${esc(e.text)}</code></li>`).join('');
    const runs = (a.runs || []).map(r => `<tr class="${r.id === a.record_id ? 'fd-me' : ''}">
        <td class="nowrap">${esc(fmtDateTime(r.test_time))}</td><td><span class="badge ${r.result ? 'pass' : 'fail'}">${r.result ? 'PASS' : 'FAIL'}</span></td>
        <td class="nowrap">${esc(r.work_order || '—')}</td><td>${esc(r.category ? localized(r.name_zh, r.name_en) || r.category : '')}</td><td class="nowrap small-text">${esc(stationText(r))}</td>
        <td class="num">${fmtNum(r.avg_24g, 1)}</td><td class="num">${fmtNum(r.avg_5g, 1)}</td></tr>`).join('');
    const canEdit = auth.isSignedIn() && !d.auto;
    const options = DISPOSITIONS.map(s => `<option value="${s}"${s === d.status ? ' selected' : ''}>${esc(t(`fails.disp.${s}`))}</option>`).join('');
    const form = canEdit ? `<div class="fd-form">
            <select class="select" id="fdStatus" aria-label="${esc(t('fails.col.disp'))}">${options}</select>
            <input class="input" id="fdNote" maxlength="300" value="${esc(d.manual ? d.note || '' : '')}" placeholder="${esc(t('fails.fd.notePh'))}" aria-label="${esc(t('fails.fd.note'))}">
            <button class="btn primary" type="button" id="btnFdSave">${esc(t('fails.fd.save'))}</button>
        </div><div class="form-msg" id="fdMsg" aria-live="polite"></div>`
        : `<p class="muted small-text">${esc(d.auto ? t(`fails.dispAuto.${d.status}`) : t('fails.fd.signIn'))}</p>`;
    const history = (a.history || []).map(h => `<li><span class="muted">${esc(fmtDateTime(h.at))}</span> ${esc(h.display_name || h.actor)} · ${esc(historyText(h))}</li>`).join('');
    $('#fdBody').innerHTML = `
        <div class="fd-head">${categoryBadge(a)} <span class="muted">${esc(localized(a.description_zh, a.description_en) || '')}</span></div>
        <div class="fd-next"><div class="fd-k">${esc(t('fails.col.next'))}</div><div class="fd-v">${suggestHtml(a.suggest, a)}</div>
            <div class="muted small-text">${esc(localized(a.action_zh, a.action_en) || '')}</div></div>
        <div class="grid cols-2 fd-grid">
            <div><div class="card-title mb-s">${esc(t('fails.fd.measures'))}</div>
                <table class="fd-kv"><tbody>${measureRows(a).map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join('')}</tbody></table></div>
            <div><div class="card-title mb-s">${esc(t('fails.fd.evidence'))}</div>
                ${evidence ? `<ul class="fd-evidence">${evidence}</ul>` : `<p class="muted">${esc(t('fails.fd.noEvidence'))}</p>`}</div>
        </div>
        <div class="card-title mb-s mt">${esc(t('fails.fd.runs'))}</div>
        <div class="table-wrap"><table><thead><tr><th>${esc(t('fails.col.time'))}</th><th>${esc(t('fails.col.result'))}</th><th>${esc(t('wo.col.wo'))}</th>
            <th>${esc(t('fails.col.category'))}</th><th>${esc(t('fails.col.station'))}</th><th class="num">${esc(t('fails.col.tp24'))}</th><th class="num">${esc(t('fails.col.tp5'))}</th></tr></thead>
            <tbody>${runs}</tbody></table></div>
        <div class="card-title mb-s mt">${esc(t('fails.col.disp'))} ${dispositionHtml(d)}</div>
        ${form}
        ${history ? `<details class="mt-s" open><summary>${esc(t('fails.fd.history'))}</summary><ul class="fd-history">${history}</ul></details>` : ''}
        ${rdSection(a)}`;
    $('#btnFdSave')?.addEventListener('click', saveDisposition);
    $('#btnFdRdSave')?.addEventListener('click', saveRd);
}

/** One audited change: a disposition ("open → repair · note") or an RD triage (field: before → after). */
function historyText(h) {
    const d = h.detail || {};
    if (h.action !== 'fail_rd') return [d.status, d.note].filter(Boolean).join(' · ');
    const label = k => ({ category: t('fails.rd.category'), stage: t('fails.rd.stage'), rd_status: t('fails.rd.status'), rd_owner: t('fails.rd.owner'),
                          rd_note: t('fails.rd.note'), rd_action: t('fails.col.next') }[k] || k);
    return `RD · ${Object.entries(d).filter(([k]) => k !== 'sn').map(([k, v]) => `${label(k)}: ${v}`).join(' · ')}`;
}

/* RD analysis inside the detail window (RD and the administrator): category, stage, status, owner, note and the
   instruction the line sees as the next step (5F-2). */
const rdCache = { taxonomy: null, stages: null };

async function loadRdLists() {
    if (rdCache.taxonomy) return;
    const tax = await apiGet('/api/fail-taxonomy');
    rdCache.taxonomy = tax.items;
    rdCache.stages = tax.stages;
}

function rdSection(a) {
    if (!(auth.isAdmin() || auth.isRd()) || !rdCache.taxonomy) return '';
    const opt = (value, label, cur) => `<option value="${esc(value)}"${value === (cur ?? '') ? ' selected' : ''}>${esc(label)}</option>`;
    const cats = rdCache.taxonomy.filter(x => x.enabled || x.code === a.category).map(x => opt(x.code, localized(x.name_zh, x.name_en), a.category)).join('');
    const stages = [opt('', t('fails.rd.noStage'), a.stage), ...rdCache.stages.map(x => opt(x.code, localized(x.name_zh, x.name_en), a.stage))].join('');
    const statuses = ['new', 'confirmed', 'reclassified', 'closed'].map(x => opt(x, t(`rd.status.${x}`), a.rd_status)).join('');
    return `<div class="fd-rd"><div class="card-title mb-s">${esc(t('fails.rd.title'))} <span class="muted small-text">${esc(t(`rd.classifier.${a.classifier}`))}${a.rd_by ? ` · ${esc(a.rd_by)} ${esc(fmtDateTime(a.rd_at))}` : ''}</span></div>
        <div class="fd-rd-grid">
            <div class="field"><label for="fdRdCat">${esc(t('fails.rd.category'))}</label><select class="select" id="fdRdCat">${cats}</select></div>
            <div class="field"><label for="fdRdStage">${esc(t('fails.rd.stage'))}</label><select class="select" id="fdRdStage">${stages}</select></div>
            <div class="field"><label for="fdRdStatus">${esc(t('fails.rd.status'))}</label><select class="select" id="fdRdStatus">${statuses}</select></div>
            <div class="field"><label for="fdRdOwner">${esc(t('fails.rd.owner'))}</label><input class="input" id="fdRdOwner" maxlength="64" value="${esc(a.rd_owner || '')}"></div>
            <div class="field wide"><label for="fdRdNote">${esc(t('fails.rd.note'))}</label><input class="input" id="fdRdNote" maxlength="500" value="${esc(a.rd_note || '')}"></div>
            <div class="field wide"><label for="fdRdAction">${esc(t('fails.rd.action'))}</label><input class="input" id="fdRdAction" maxlength="200" value="${esc(a.rd_action || '')}"></div>
        </div>
        <div class="toolbar"><button class="btn primary" type="button" id="btnFdRdSave">${esc(t('fails.rd.save'))}</button>
            <a class="btn small ghost" href="#rd" id="fdRdOpen">${esc(t('fails.rd.open'))}</a></div>
        <div class="form-msg" id="fdRdMsg" aria-live="polite"></div></div>`;
}

async function saveRd() {
    const a = fd.data;
    const want = { category: $('#fdRdCat').value, stage: $('#fdRdStage').value || null, rd_status: $('#fdRdStatus').value,
                   rd_owner: $('#fdRdOwner').value.trim() || null, rd_note: $('#fdRdNote').value.trim() || null,
                   rd_action: $('#fdRdAction').value.trim() || null };
    const changes = Object.fromEntries(Object.entries(want).filter(([k, v]) => (a[k] ?? null) !== v));
    const msg = $('#fdRdMsg');
    if (!Object.keys(changes).length) { msg.textContent = t('rd.batchNothing'); return; }
    $('#btnFdRdSave').disabled = true;
    try {
        await request('/api/rd/fails', { method: 'PATCH', body: { ids: [fd.id], changes } });
        fd.data = await apiGet(`/api/fail-analysis/${fd.id}`);
        renderDetail();
        toast(t('fails.rd.saved'));
        fd.onChange?.();
    } catch (err) {
        msg.textContent = err.message;
        $('#btnFdRdSave').disabled = false;
    }
}

async function saveDisposition() {
    const status = $('#fdStatus').value;
    const note = $('#fdNote').value.trim();
    const msg = $('#fdMsg');
    const btn = $('#btnFdSave');
    btn.disabled = true;
    msg.textContent = '';
    try {
        const r = await request('/api/fail-disposition', { method: 'PUT', body: { record_id: fd.id, status, note: note || null } });
        fd.data.disposition = r.disposition;
        fd.data.history = r.history;
        renderDetail();
        toast(t('fails.fd.saved'));
        fd.onChange?.();
    } catch (err) {
        msg.textContent = err.message;
        btn.disabled = false;
    }
}

/** Open the FAIL detail window for one FAIL test run; onChange runs after a disposition is saved. */
export async function openFailDetail(recordId, onChange) {
    fd.id = Number(recordId);
    fd.onChange = onChange;
    fd.lastFocus = document.activeElement;
    $('#failModal').hidden = false;
    $('#fdTitle').textContent = t('common.loading');
    $('#fdSub').textContent = '';
    $('#fdBody').innerHTML = `<div class="state-msg">${esc(t('common.loading'))}</div>`;
    try {
        if (auth.isAdmin() || auth.isRd()) await loadRdLists().catch(() => {});
        fd.data = await apiGet(`/api/fail-analysis/${fd.id}`);
        renderDetail();
        $('#btnFdClose').focus();
    } catch (err) {
        $('#fdBody').innerHTML = `<div class="state-msg error">${esc(err.message)}</div>`;
    }
}

export function closeFailDetail() {
    $('#failModal').hidden = true;
    fd.data = null;
    fd.lastFocus?.focus?.();
}

function initDetail() {
    $('#btnFdClose').addEventListener('click', closeFailDetail);
    $('#failModal').addEventListener('mousedown', e => { if (e.target.id === 'failModal') closeFailDetail(); });
    $('#btnFdLog').addEventListener('click', async () => {
        try { await download(`/api/raw-log/${fd.id}`, `record_${fd.id}.txt`); } catch (err) { toast(err.message, 'bad'); }
    });
}

function init(app) {
    appRef = app;
    initDetail();
    $('#failSearch').addEventListener('input', debounce(() => { state.page = 1; load(); }, 300));
    $$('#flDispSeg button').forEach(b => b.addEventListener('click', () => { state.disp = b.dataset.disp; state.page = 1; load(); }));
    $('#failRows').addEventListener('click', async e => {
        const detail = e.target.closest('[data-detail]');
        if (detail) { openFailDetail(detail.dataset.detail, () => load()); return; }
        const btn = e.target.closest('[data-log]');
        if (!btn) return;
        try { await download(`/api/raw-log/${btn.dataset.log}`, `record_${btn.dataset.log}.txt`); }
        catch (err) { toast(err.message, 'bad'); }
    });
}

export default {
    id: 'fails', scoped: true, init, load: app => load(app),
    isBusy: () => !$('#failModal').hidden,
    onAuthChange: () => { if (fd.data && !$('#failModal').hidden) renderDetail(); },
    onLangChange: () => { if (fd.data && !$('#failModal').hidden) renderDetail(); },
};
