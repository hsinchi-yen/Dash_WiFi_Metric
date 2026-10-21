// Ask the dashboard (5E, L6; Q25: everyone, rate limited, aggregates only). The top bar's 💬 opens it; a question
// goes to POST /api/ask: the LLM only turns it into a restricted query, the numbers come from the database, the
// answer sentence is written by rules. The window shows how the question was understood, so it can be checked, and
// opens the same scope on the Trend page.
import { apiPost } from './core/api.js';
import { esc, fmtInt, fmtNum } from './core/format.js';
import { getLang, localized, t } from './core/i18n.js';
import { $ } from './core/ui.js';

const st = { busy: false, items: [], lastFocus: null };
let appRef;

function understood(q) {
    const parts = [t(`ask.m.${q.metric}`)];
    const period = q.period === 'last_n_days' ? t('ask.p.last_n_days', { n: q.n }) : q.period === 'month' ? `${q.year}-${String(q.month).padStart(2, '0')}`
        : q.period === 'custom' ? `${q.date_from || '…'} ~ ${q.date_to || '…'}` : t(`ask.p.${q.period}`);
    parts.push(period);
    if (q.product) parts.push(q.product);
    if (q.work_order) parts.push(t('ask.wo', { wo: q.work_order }));
    if (q.group_by !== 'none') parts.push(t('ask.by', { g: t(`ask.g.${q.group_by}`) }));
    if (q.compare_previous) parts.push(t('ask.compare'));
    return parts.join(t('common.dot'));
}

function value(metric, v) {
    if (v == null) return '—';
    return ['units', 'pass', 'fail', 'work_orders'].includes(metric) ? fmtInt(v)
        : `${fmtNum(v, 2)}${['yield', 'fail_rate', 'retry_rate'].includes(metric) ? '%' : metric.startsWith('avg') ? ' Mbps' : ''}`;
}

function renderItem(it) {
    if (it.error) return `<div class="msg assistant refused">${esc(it.error)}</div>`;
    const r = it.result;
    if (!r) return '<div class="msg assistant typing"></div>';
    if (!r.supported) return `<div class="msg assistant refused">${esc(t('ask.unsupported'))}${r.query.reason ? ` <span class="muted">(${esc(r.query.reason)})</span>` : ''}</div>`;
    const q = r.query;
    let table = '';
    if (r.rows?.length) {
        table = `<table class="ask-table"><tbody>${r.rows.map(x => `<tr><td>${esc(x.key)}</td><td class="num">${esc(value(q.metric, x.value))}</td>
            <td class="num muted">${esc(t('ask.units', { n: fmtInt(x.units) }))}</td></tr>`).join('')}</tbody></table>`;
    } else if (r.categories?.length) {
        table = `<table class="ask-table"><tbody>${r.categories.map(c => `<tr><td>${esc(localized(c.name_zh, c.name_en) || c.category)}</td>
            <td class="num">${esc(t('ask.runs', { n: fmtInt(c.runs) }))}</td><td class="num muted">${esc(t('ask.units', { n: fmtInt(c.units) }))}</td></tr>`).join('')}</tbody></table>`;
    }
    const scope = r.from || r.to || q.product || q.work_order
        ? ` <button class="btn small" type="button" data-scope='${esc(JSON.stringify({ from: r.from, to: r.to, product: q.product, wo: q.work_order }))}'>${esc(t('ask.open'))}</button>` : '';
    return `<div class="msg assistant">${esc(getLang() === 'zh' ? r.answer.zh : r.answer.en)}
        <div class="muted small-text mt-s">${esc(t('ask.understood', { q: understood(q) }))}${scope}</div>${table}</div>`;
}

function render() {
    const log = $('#askLog');
    log.innerHTML = st.items.map(it => `<div class="msg user">${esc(it.q)}</div>${renderItem(it)}`).join('')
        || `<div class="msg note">${esc(t('ask.hint'))}</div>`;
    log.scrollTop = log.scrollHeight;
    $('#btnAskSend').disabled = st.busy;
}

async function send(text) {
    const input = $('#askInput');
    const q = (text ?? input.value).trim();
    if (!q || st.busy) return;
    input.value = '';
    const item = { q, result: null };
    st.items.push(item);
    st.busy = true;
    render();
    try {
        item.result = await apiPost('/api/ask', { question: q, lang: getLang() });
    } catch (err) {
        item.error = err.message;
    } finally {
        st.busy = false;
        render();
        input.focus();
    }
}

export function open() {
    st.lastFocus = document.activeElement;
    $('#askModal').hidden = false;
    render();
    $('#askInput').focus();
}

export function close() {
    $('#askModal').hidden = true;
    st.lastFocus?.focus?.();
}

export const isOpen = () => !$('#askModal').hidden;

export function init(app) {
    appRef = app;
    $('#btnAsk').addEventListener('click', open);
    $('#btnAskClose').addEventListener('click', close);
    $('#askModal').addEventListener('mousedown', e => { if (e.target.id === 'askModal') close(); });
    $('#btnAskSend').addEventListener('click', () => send());
    $('#askInput').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); send(); } });
    $('#askChips').addEventListener('click', e => { const b = e.target.closest('[data-q]'); if (b) send(b.dataset.q); });
    $('#askLog').addEventListener('click', e => {
        const b = e.target.closest('[data-scope]');
        if (!b) return;
        const s = JSON.parse(b.dataset.scope);
        appRef.setScope({ range: s.from || s.to ? 'custom' : 'all', from: s.from || '', to: s.to || '', product: s.product || '', workOrder: s.wo || '' });
        close();
        location.hash = 'overview';
        appRef.reload();
    });
    renderChips();
}

export function renderChips() {
    $('#askChips').innerHTML = ['ask.ex1', 'ask.ex2', 'ask.ex3', 'ask.ex4'].map(k => `<button class="btn small" type="button" data-q="${esc(t(k))}">${esc(t(k))}</button>`).join('');
}

export function onLangChange() { renderChips(); if (isOpen()) render(); }
