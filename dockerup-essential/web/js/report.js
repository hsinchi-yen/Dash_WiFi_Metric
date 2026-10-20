// AI report window (one for the whole app). This phase keeps the existing generator
// (GET /api/workorders/{wo}/ai-summary, one language per call) and adds what the old
// "terminal" window lacked: readable, selectable text, 中文 / EN switch, copy, download,
// regenerate, Esc to close. The facts tiles come from /api/work-order-summary.
import { ApiError, apiGet } from './core/api.js';
import { esc, fmtDateTime, fmtInt, fmtNum, fmtPct, yieldLevel } from './core/format.js';
import { getLang, t } from './core/i18n.js';
import { $, $$, toast } from './core/ui.js';

const TIMEOUT_MS = 190 * 1000;
const st = { wo: null, product: '', lang: 'zh', cache: {}, facts: null, loading: false, llm: false, controller: null, session: 0 };

function key(lang) { return `${st.wo}|${st.product}|${lang}`; }

function renderHeader() {
    $('#rpTitle').textContent = st.wo ? t('rp.title', { wo: st.wo }) : t('rp.titleGeneric');
    const entry = st.cache[key(st.lang)];
    const ok = !!entry && !entry.error;
    $('#rpGenerated').textContent = ok ? t('rp.generated', { time: fmtDateTime(entry.at) }) : '';
    $$('#rpLang button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.lang === st.lang)));
    $('#btnRpCopy').disabled = !ok;
    $('#btnRpDownload').disabled = !ok;
    $('#btnRpRegen').disabled = st.loading || !st.llm;
}

function renderFacts() {
    const box = $('#rpFacts');
    box.innerHTML = '';
    const f = st.facts;
    if (!f) return;
    const tiles = [
        [t('rp.fact.units'), fmtInt(f.total), ''],
        [t('rp.fact.yield'), fmtPct(f.yield_pct), `v-${yieldLevel(f.yield_pct)}`],
        ['PASS', fmtInt(f.passed), 'v-pass'],
        ['FAIL', fmtInt(f.failed), 'v-fail'],
        [t('rp.fact.retry'), fmtPct(f.retry_rate), ''],
        [t('rp.fact.avg24'), `${fmtNum(f.avg_24g, 1)} Mbps`, ''],
        [t('rp.fact.avg5'), `${fmtNum(f.avg_5g, 1)} Mbps`, ''],
    ];
    box.innerHTML = tiles.map(([k, v, cls]) => `<div class="rp-fact"><div class="k">${esc(k)}</div><div class="v ${cls}">${esc(v)}</div></div>`).join('');
}

function renderBody() {
    const body = $('#rpBody');
    const banner = $('#rpBanner');
    banner.innerHTML = '';
    body.innerHTML = '';
    if (!st.llm) {
        banner.innerHTML = `<div class="notice warn mb">${esc(t('rp.offline'))}</div>`;
    }
    const entry = st.cache[key(st.lang)];
    if (st.loading) {
        body.innerHTML = `<div class="rp-pending"><span class="spinner"></span>${esc(t('rp.loading'))}</div>`;
        return;
    }
    if (entry?.error) {
        banner.innerHTML += `<div class="notice bad mb">${esc(t('rp.error', { error: entry.error }))}</div>`;
        return;
    }
    if (entry) body.textContent = entry.text;
}

async function loadFacts() {
    try {
        const rows = await apiGet(`/api/work-order-summary?work_order=${encodeURIComponent(st.wo)}${st.product ? `&product_model=${encodeURIComponent(st.product)}` : ''}`);
        st.facts = rows[0] || null;
    } catch {
        st.facts = null;
    }
    renderFacts();
}

async function generate(force = false) {
    const k = key(st.lang);
    if (!force && st.cache[k] && !st.cache[k].error) { renderHeader(); renderBody(); return; }
    if (!st.llm && !force) {
        st.cache[k] = { error: t('status.llmErr'), at: new Date() };
        renderHeader(); renderBody();
        return;
    }
    st.controller?.abort();
    const controller = new AbortController();
    st.controller = controller;
    const session = ++st.session;
    st.loading = true;
    renderHeader(); renderBody();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    try {
        const qs = new URLSearchParams({ lang: st.lang, ...(st.product ? { product_model: st.product } : {}) });
        const data = await apiGet(`/api/workorders/${encodeURIComponent(st.wo)}/ai-summary?${qs}`, { signal: controller.signal });
        if (session !== st.session) return;
        st.cache[k] = { text: String(data.summary || '').trim(), at: new Date() };
    } catch (err) {
        if (session !== st.session) return;
        const message = err.name === 'AbortError' ? t('rp.timeout') : (err instanceof ApiError ? err.message : String(err));
        st.cache[k] = { error: message, at: new Date() };
    } finally {
        clearTimeout(timer);
        if (session === st.session) {
            st.loading = false;
            renderHeader(); renderBody();
        }
    }
}

function markdown() {
    const entry = st.cache[key(st.lang)];
    if (!entry?.text) return '';
    const f = st.facts;
    const facts = f ? [
        `| ${t('rp.fact.units')} | ${t('rp.fact.yield')} | PASS | FAIL | ${t('rp.fact.retry')} | ${t('rp.fact.avg24')} | ${t('rp.fact.avg5')} |`,
        '|---|---|---|---|---|---|---|',
        `| ${fmtInt(f.total)} | ${fmtPct(f.yield_pct)} | ${fmtInt(f.passed)} | ${fmtInt(f.failed)} | ${fmtPct(f.retry_rate)} | ${fmtNum(f.avg_24g, 1)} | ${fmtNum(f.avg_5g, 1)} |`,
        '',
    ] : [];
    return [`# ${t('rp.title', { wo: st.wo })}`, '', `${t('rp.generated', { time: fmtDateTime(entry.at) })}`, '', ...facts, entry.text, ''].join('\n');
}

function open(wo, product = '') {
    st.wo = wo;
    st.product = product || '';
    st.lang = getLang() === 'en' ? 'en' : 'zh';
    st.facts = null;
    $('#reportModal').hidden = false;
    renderHeader();
    renderFacts();
    renderBody();
    loadFacts();
    generate();
}

function close() {
    st.controller?.abort();
    st.session++;
    st.loading = false;
    $('#reportModal').hidden = true;
}

function init() {
    $('#btnRpClose').addEventListener('click', close);
    $('#reportModal').addEventListener('mousedown', e => { if (e.target.id === 'reportModal') close(); });
    $$('#rpLang button').forEach(b => b.addEventListener('click', () => {
        if (st.lang === b.dataset.lang) return;
        st.lang = b.dataset.lang;
        generate();
    }));
    $('#btnRpRegen').addEventListener('click', () => generate(true));
    $('#btnRpCopy').addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(markdown());
            toast(t('rp.copied'));
        } catch {
            toast(t('rp.copyFailed'), 'bad');
        }
    });
    $('#btnRpDownload').addEventListener('click', () => {
        const blob = new Blob([markdown()], { type: 'text/markdown;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `ai_report_${st.wo}_${st.lang}.md`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
}

export default {
    init, open, close,
    setLlm(ok) { st.llm = ok; if (!$('#reportModal').hidden) { renderHeader(); renderBody(); } },
    onLangChange() { if (!$('#reportModal').hidden) { renderHeader(); renderFacts(); renderBody(); } },
};
