// AI report window (one for the whole app). Reports are generated in the background and
// stored on the server, so opening one is a read (GET /api/reports/{wo}); the first time a
// work order is opened, or after its data changed, the window shows the old text (if any)
// and polls until the new report is ready. Text is rendered as text, never HTML.
import { ApiError, apiGet, apiPost } from './core/api.js';
import { esc, fmtDateTime, fmtInt, fmtNum, fmtPct, yieldLevel } from './core/format.js';
import { getLang, t } from './core/i18n.js';
import { $, $$, toast } from './core/ui.js';

const POLL_MS = 3000;
const st = { wo: null, product: '', lang: 'zh', data: null, facts: null, llm: false, session: 0, timer: null, error: null };

function renderHeader() {
    $('#rpTitle').textContent = st.wo ? t('rp.title', { wo: st.wo }) : t('rp.titleGeneric');
    const d = st.data;
    const has = !!d?.text;
    $('#rpGenerated').textContent = has ? t('rp.generated', { time: fmtDateTime(d.generated_at) }) : '';
    $$('#rpLang button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.lang === st.lang)));
    $('#btnRpCopy').disabled = !has;
    $('#btnRpDownload').disabled = !has;
    $('#btnRpRegen').disabled = !st.llm || !!d?.generating || d?.status === 'no_data';
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
    const banner = $('#rpBanner');
    const body = $('#rpBody');
    banner.innerHTML = '';
    body.innerHTML = '';
    const notices = [];
    const d = st.data;
    if (st.error) notices.push(['bad', t('rp.error', { error: st.error })]);
    if (!st.llm) notices.push(['warn', t('rp.offline')]);
    if (d?.status === 'no_data') notices.push(['bad', t('rp.noData')]);
    if (d?.error && !d?.generating) notices.push(['bad', t('rp.error', { error: d.error })]);
    if (d?.stale) notices.push(['warn', d.generating ? t('rp.staleGenerating') : t('rp.stale')]);
    banner.innerHTML = notices.map(([kind, text]) => `<div class="notice ${kind} mb">${esc(text)}</div>`).join('');
    if (d?.text) {
        body.textContent = d.text;
    } else if (d?.generating) {
        body.innerHTML = `<div class="rp-pending"><span class="spinner"></span>${esc(t('rp.generating'))}</div>`;
    } else if (!d && !st.error) {
        body.innerHTML = `<div class="rp-pending"><span class="spinner"></span>${esc(t('common.loading'))}</div>`;
    }
}

function render() { renderHeader(); renderBody(); }

async function loadFacts() {
    try {
        const rows = await apiGet(`/api/work-order-summary?work_order=${encodeURIComponent(st.wo)}${st.product ? `&product_model=${encodeURIComponent(st.product)}` : ''}`);
        st.facts = rows[0] || null;
    } catch {
        st.facts = null;
    }
    renderFacts();
}

function query() {
    return new URLSearchParams({ lang: st.lang, ...(st.product ? { product_model: st.product } : {}) }).toString();
}

async function fetchReport() {
    const session = st.session;
    clearTimeout(st.timer);
    try {
        const data = await apiGet(`/api/reports/${encodeURIComponent(st.wo)}?${query()}`);
        if (session !== st.session) return;
        st.data = data;
        st.error = null;
    } catch (err) {
        if (session !== st.session) return;
        st.error = err instanceof ApiError ? err.message : String(err);
    }
    render();
    if (st.data?.generating && !st.error) st.timer = setTimeout(fetchReport, POLL_MS);
}

async function regenerate() {
    const session = st.session;
    try {
        const data = await apiPost(`/api/reports/${encodeURIComponent(st.wo)}/generate?${query()}`);
        if (session !== st.session) return;
        st.data = data;
        st.error = null;
        render();
        st.timer = setTimeout(fetchReport, POLL_MS);
    } catch (err) {
        toast(err.message, 'bad');
    }
}

function markdown() {
    const d = st.data;
    if (!d?.text) return '';
    const f = st.facts;
    const facts = f ? [
        `| ${t('rp.fact.units')} | ${t('rp.fact.yield')} | PASS | FAIL | ${t('rp.fact.retry')} | ${t('rp.fact.avg24')} | ${t('rp.fact.avg5')} |`,
        '|---|---|---|---|---|---|---|',
        `| ${fmtInt(f.total)} | ${fmtPct(f.yield_pct)} | ${fmtInt(f.passed)} | ${fmtInt(f.failed)} | ${fmtPct(f.retry_rate)} | ${fmtNum(f.avg_24g, 1)} | ${fmtNum(f.avg_5g, 1)} |`,
        '',
    ] : [];
    return [`# ${t('rp.title', { wo: st.wo })}`, '', t('rp.generated', { time: fmtDateTime(d.generated_at) }), '', ...facts, d.text, ''].join('\n');
}

function open(wo, product = '') {
    st.session++;
    clearTimeout(st.timer);
    st.wo = wo;
    st.product = product || '';
    st.lang = getLang() === 'en' ? 'en' : 'zh';
    st.data = null;
    st.facts = null;
    st.error = null;
    $('#reportModal').hidden = false;
    render();
    renderFacts();
    loadFacts();
    fetchReport();
}

function close() {
    st.session++;
    clearTimeout(st.timer);
    $('#reportModal').hidden = true;
}

function init() {
    $('#btnRpClose').addEventListener('click', close);
    $('#reportModal').addEventListener('mousedown', e => { if (e.target.id === 'reportModal') close(); });
    $$('#rpLang button').forEach(b => b.addEventListener('click', () => {
        if (st.lang === b.dataset.lang) return;
        st.lang = b.dataset.lang;
        st.session++;
        st.data = null;
        render();
        fetchReport();
    }));
    $('#btnRpRegen').addEventListener('click', regenerate);
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
    setLlm(ok) { st.llm = ok; if (!$('#reportModal').hidden) render(); },
    onLangChange() { if (!$('#reportModal').hidden) { renderFacts(); render(); } },
};
