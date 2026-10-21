// Work Orders: one row per work order with its status (state + light), progress against the plan,
// the one-line status note (L1) and, under "All columns", the detailed numbers.
// `status` comes from the server rules (services/wo_rules.py) and covers the whole work order.
import { apiGet } from '../core/api.js';
import { esc, fmtDateTime, fmtInt, fmtNum, fmtPct, yieldLevel } from '../core/format.js';
import { getLang, joinList, t } from '../core/i18n.js';
import { detailHref, queryString } from '../core/scope.js';
import { $, $$, debounce, tableState } from '../core/ui.js';

const COLS_KEY = 'wifi-wo-cols';
const BASIC = ['pick', 'status', 'wo', 'product', 'plan', 'yield', 'fail', 'note', 'last', 'report'];
const ALL = ['pick', 'status', 'wo', 'product', 'plan', 'yield', 'pass', 'fail', 'retry', 'avg24', 'avg5', 'duration', 'uph', 'note', 'last', 'report'];
const HEAD = {
    pick: ['wo.col.pick'], status: ['wo.col.status'], wo: ['wo.col.wo'], product: ['wo.col.product'], plan: ['wo.col.plan', 'num'],
    yield: ['wo.col.yield', 'num'], pass: ['PASS', 'num'], fail: ['FAIL', 'num'], retry: ['wo.col.retry', 'num'],
    avg24: ['wo.col.avg24', 'num'], avg5: ['wo.col.avg5', 'num'], duration: ['wo.col.duration', 'num'],
    uph: ['wo.col.uphTarget', 'num'], note: ['wo.col.note'], last: ['wo.col.last'], report: ['wo.col.report'],
};
let rows = [];
let seq = 0;
let appRef;
let mode = 'basic';
const picked = [];          // up to two [work order, product] keys to compare

/** "1.5 h" / "2.3 d" from seconds. */
export function fmtDuration(sec) {
    if (sec == null) return '—';
    const hours = Number(sec) / 3600;
    return hours < 24 ? t('wo.hours', { n: hours.toFixed(1) }) : t('wo.days', { n: (hours / 24).toFixed(1) });
}

/** A flag as text in the UI language (same wording as the server's rule notes). */
export function flagText(flag) {
    return t(`wo.flag.${flag.code}`, flag.values || {});
}

function columns() { return mode === 'all' ? ALL : BASIC; }

function renderHead() {
    $('#woHead').innerHTML = `<tr>${columns().map(c => {
        const [key, cls] = HEAD[c];
        const label = key.includes('.') ? t(key) : key;
        return `<th${cls ? ` class="${cls}"` : ''}>${esc(label)}</th>`;
    }).join('')}</tr>`;
    $$('#woColsSeg button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.cols === mode)));
}

function statusCell(st) {
    if (!st) return '<td>—</td>';
    const flags = st.flags || [];
    const title = flags.length ? t('wo.flags', { list: joinList(flags.map(flagText)) }) : t('wo.noFlags');
    return `<td class="nowrap"><span class="wo-state light-${esc(st.light)}" title="${esc(title)}"><span class="wo-dot"></span>${esc(t(`wo.state.${st.state}`))}</span></td>`;
}

function planCell(r) {
    const st = r.status;
    if (!st?.planned) return `<td class="num">${fmtInt(r.total)} <span class="muted small-text">· ${esc(t('wo.planNone'))}</span></td>`;
    const pct = st.achieved_pct;
    const cls = pct >= 100 ? 'good-text' : '';
    return `<td class="num nowrap">${esc(t('wo.planOf', { tested: fmtInt(st.total), planned: fmtInt(st.planned) }))}
        <span class="muted small-text ${cls}">· ${fmtNum(pct, 0)}%</span></td>`;
}

function noteCell(st) {
    const note = st?.note;
    if (!note) return '<td>—</td>';
    const text = getLang() === 'zh' ? note.zh : note.en;
    const flags = (st.flags || []).map(flagText);
    const title = [text, flags.length ? t('wo.flags', { list: joinList(flags) }) : '', note.pending ? t('wo.notePending') : '']
        .filter(Boolean).join('\n');
    const ai = note.source === 'llm' ? `<span class="ai-tag" title="${esc(t('wo.aiTitle'))}">${esc(t('wo.ai'))}</span>` : '';
    const rd = note.rd ? ` <span class="rd-action">${esc(t('wo.rdNote', { text: note.rd.text }))}</span>` : '';
    return `<td class="wo-note" title="${esc(title + (note.rd ? `\n${t('wo.rdNote', { text: note.rd.text })}` : ''))}">${ai}<span>${esc(text)}${rd}</span></td>`;
}

function uphCell(r) {
    const st = r.status;
    const target = st?.uph_target;
    const src = st?.uph_target_source ? t(`wo.targetSource.${st.uph_target_source}`) : '';
    const actual = st?.uph;
    const low = actual != null && target && actual < target * 0.8;
    return `<td class="num nowrap" title="${esc(src)}"><span class="${low ? 'warn-text' : ''}">${fmtNum(actual, 1)}</span>
        <span class="muted small-text">/ ${fmtNum(target, 1)}</span></td>`;
}

function cell(c, r) {
    switch (c) {
    case 'status': return statusCell(r.status);
    case 'pick': {
        const on = picked.some(k => k[0] === r.work_order && k[1] === (r.product_model || ''));
        return `<td><input type="checkbox" class="wo-pick" data-wo="${esc(r.work_order)}" data-pm="${esc(r.product_model || '')}"${on ? ' checked' : ''}
            aria-label="${esc(t('wo.pickAria', { wo: r.work_order }))}"></td>`;
    }
    case 'wo': return `<td class="nowrap"><a class="wo-link" href="${esc(detailHref(r.work_order, r.product_model))}"><strong>${esc(r.work_order)}</strong></a></td>`;
    case 'product': return `<td class="nowrap">${esc(r.product_model || '—')}</td>`;
    case 'plan': return planCell(r);
    case 'yield': return `<td class="num"><span class="badge ${yieldLevel(r.yield_pct)}">${fmtPct(r.yield_pct)}</span></td>`;
    case 'pass': return `<td class="num good-text">${fmtInt(r.passed)}</td>`;
    case 'fail': return `<td class="num ${r.failed ? 'bad-text' : 'muted'}">${fmtInt(r.failed)}</td>`;
    case 'retry': return `<td class="num">${fmtPct(r.retry_rate)}</td>`;
    case 'avg24': return `<td class="num">${fmtNum(r.avg_24g, 1)}</td>`;
    case 'avg5': return `<td class="num">${fmtNum(r.avg_5g, 1)}</td>`;
    case 'duration': return `<td class="num nowrap">${esc(fmtDuration(r.duration_sec))}</td>`;
    case 'uph': return uphCell(r);
    case 'note': return noteCell(r.status);
    case 'last': return `<td class="nowrap">${esc(fmtDateTime(r.end_time))}</td>`;
    case 'report': return `<td><a class="btn small" href="${esc(detailHref(r.work_order, r.product_model, { focus: 'report' }))}">✨ ${esc(t('wo.report'))}</a></td>`;
    default: return '<td></td>';
    }
}

function renderPicked() {
    const btn = $('#btnWoCompare');
    btn.textContent = t('wo.compareBtn', { n: picked.length });
    btn.disabled = picked.length !== 2;
}

function render() {
    renderHead();
    renderPicked();
    const q = $('#woSearch').value.trim().toLowerCase();
    const shown = q ? rows.filter(r => `${r.work_order} ${r.product_model || ''}`.toLowerCase().includes(q)) : rows;
    $('#woCount').textContent = t('wo.count', { n: fmtInt(shown.length) });
    const tbody = $('#woRows');
    if (!shown.length) { tableState(tbody, columns().length, 'empty'); return; }
    tbody.innerHTML = shown.map(r => `<tr class="wo-row light-${esc(r.status?.light || 'none')}">${columns().map(c => cell(c, r)).join('')}</tr>`).join('');
}

async function load(app) {
    const mine = ++seq;
    const tbody = $('#woRows');
    if (!rows.length) { renderHead(); tableState(tbody, columns().length, 'loading'); }
    try {
        const data = await apiGet(`/api/work-order-summary${queryString(app.params())}`);
        if (mine !== seq) return;
        rows = data;
        render();
    } catch (err) {
        if (mine !== seq) return;
        rows = [];
        tableState(tbody, columns().length, 'error', t('common.loadFailedMsg', { error: err.message }), () => load(app));
    }
}

function init(app) {
    appRef = app;
    try { mode = localStorage.getItem(COLS_KEY) === 'all' ? 'all' : 'basic'; } catch { /* storage blocked */ }
    $('#woSearch').addEventListener('input', debounce(render, 200));
    $$('#woColsSeg button').forEach(b => b.addEventListener('click', () => {
        mode = b.dataset.cols;
        try { localStorage.setItem(COLS_KEY, mode); } catch { /* storage blocked */ }
        render();
    }));
    $('#woRows').addEventListener('change', e => {
        const box = e.target.closest('.wo-pick');
        if (!box) return;
        const key = [box.dataset.wo, box.dataset.pm];
        const i = picked.findIndex(k => k[0] === key[0] && k[1] === key[1]);
        if (box.checked) {
            if (picked.length >= 2) { box.checked = false; return; }
            picked.push(key);
        } else if (i >= 0) picked.splice(i, 1);
        renderPicked();
    });
    $('#btnWoCompare').addEventListener('click', () => {
        if (picked.length !== 2) return;
        const [a, b] = picked;
        location.hash = detailHref(a[0], a[1], { cmp: b[0], ...(b[1] ? { cpm: b[1] } : {}) });
    });
}

export default { id: 'workorders', scoped: true, init, load, onLangChange: () => { renderHead(); renderPicked(); if (rows.length) render(); } };
