// Settings (administrator): yield thresholds, working hours, status rules and the hourly target per
// product. GET /api/settings → form; Save sends only what changed (PUT, audited on the server).
import { apiGet, request } from '../core/api.js';
import * as auth from '../core/auth.js';
import { esc, fmtInt, fmtNum, setThresholds } from '../core/format.js';
import { t } from '../core/i18n.js';
import { $, $$, toast } from '../core/ui.js';

const NUMBER_FIELDS = ['yield_good', 'yield_warn', 'active_minutes', 'done_idle_hours', 'idle_warn_minutes', 'fail_streak', 'tp_drop_pct', 'behind_pct'];
const INT_FIELDS = new Set(['active_minutes', 'done_idle_hours', 'idle_warn_minutes', 'fail_streak', 'tp_drop_pct', 'behind_pct']);
const TIME_FIELDS = ['work_start', 'work_end'];
const st = { data: null };
let appRef;

function renderAccess() {
    const signedIn = auth.isSignedIn();
    const admin = auth.isAdmin();
    $('#setLogin').hidden = signedIn;
    $('#setDenied').hidden = !signedIn || admin;
    $('#setPanel').hidden = !admin;
}

const field = name => $(`#setPanel [name="${name}"]`);

function fillForm(values) {
    for (const name of [...NUMBER_FIELDS, ...TIME_FIELDS]) field(name).value = values[name] ?? '';
    renderPreview();
}

function renderUph() {
    const tbody = $('#setUphRows');
    const products = st.data?.products || [];
    if (!products.length) { tbody.innerHTML = `<tr><td colspan="5" class="muted">${esc(t('common.empty'))}</td></tr>`; return; }
    tbody.innerHTML = products.map(p => `<tr>
        <td class="nowrap"><strong>${esc(p.product_model)}</strong></td>
        <td class="num">${fmtNum(p.history_median, 1)}</td>
        <td class="num">${p.work_orders || 0}</td>
        <td><input class="input set-uph" type="number" min="1" max="10000" step="0.1" inputmode="decimal"
                   data-product="${esc(p.product_model)}" value="${p.override ?? ''}" placeholder="${p.history_median ?? ''}"
                   aria-label="${esc(`${p.product_model} ${t('set.col.override')}`)}"></td>
        <td class="num nowrap">${fmtNum(p.in_use, 1)} <span class="muted small-text">${esc(p.source === 'setting' ? t('set.useSetting') : p.source === 'history' ? t('set.useHistory') : '')}</span></td>
    </tr>`).join('');
}

function renderPreview() {
    const good = Number(field('yield_good').value);
    const warn = Number(field('yield_warn').value);
    const sample = [good, (good + warn) / 2, warn - 1].filter(Number.isFinite);
    const level = v => (v >= good ? 'good' : v >= warn ? 'warn' : 'bad');
    $('#setPreview').innerHTML = sample.map(v => `<span class="badge ${level(v)}">${esc(v.toFixed(1))}%</span>`).join(' ');
}

/** Only what differs from the stored settings. */
function changes() {
    const cur = st.data.settings;
    const out = {};
    for (const name of NUMBER_FIELDS) {
        const raw = field(name).value.trim();
        if (raw === '') continue;
        const v = INT_FIELDS.has(name) ? Math.round(Number(raw)) : Number(raw);
        if (Number.isFinite(v) && v !== Number(cur[name])) out[name] = v;
    }
    for (const name of TIME_FIELDS) {
        const v = field(name).value;
        if (v && v !== cur[name]) out[name] = v;
    }
    const targets = {};
    let changedTarget = false;
    $$('#setUphRows .set-uph').forEach(input => {
        const product = input.dataset.product;
        const raw = input.value.trim();
        const before = cur.uph_targets?.[product];
        const v = raw === '' ? null : Number(raw);
        if (v !== null && Number.isFinite(v)) targets[product] = v;
        if ((before ?? null) !== v) changedTarget = true;
    });
    if (changedTarget) out.uph_targets = targets;
    return out;
}

function markErrors(fields) {
    $$('#setPanel .input').forEach(i => i.classList.remove('invalid'));
    Object.keys(fields || {}).forEach(name => field(name)?.classList.add('invalid'));
}

async function save(e) {
    e?.preventDefault();
    const msg = $('#setMsg');
    const body = changes();
    if (!Object.keys(body).length) { msg.textContent = t('set.noChange'); msg.classList.remove('bad-text'); return; }
    const btn = $('#btnSetSave');
    btn.disabled = true;
    msg.textContent = '';
    try {
        const data = await request('/api/settings', { method: 'PUT', body });
        st.data = data;
        markErrors({});
        fillForm(data.settings);
        renderUph();
        setThresholds({ yield_good: data.settings.yield_good, yield_warn: data.settings.yield_warn });
        toast(t('set.saved', { n: Object.keys(data.changed || {}).length }));
    } catch (err) {
        markErrors(err.data?.fields);
        msg.classList.add('bad-text');
        msg.textContent = err.message;
    } finally {
        btn.disabled = false;
    }
}

const PURPOSES = ['wo_note', 'wo_report', 'wo_answer', 'fail_category', 'upload_fields', 'upload_reply', 'old_report'];
const purposeName = p => (PURPOSES.includes(p) ? t(`set.llm.p.${p}`) : t('set.llm.p.other'));

async function loadUsage() {
    const tbody = $('#setLlmRows');
    try {
        const u = await apiGet('/api/llm-usage?days=7');
        $('#setLlmNote').textContent = t('set.llmLast24', { n: fmtInt(u.last_24h_total) });
        tbody.innerHTML = u.days.length ? u.days.map(r => `<tr><td class="nowrap">${esc(r.day)}</td><td>${esc(purposeName(r.purpose))}</td>
            <td class="num">${fmtInt(r.calls)}</td><td class="num ${r.failed ? 'bad-text' : 'muted'}">${fmtInt(r.failed)}</td>
            <td class="num">${fmtInt(r.avg_ms)}</td></tr>`).join('') : `<tr><td colspan="5" class="muted">${esc(t('set.llmNone'))}</td></tr>`;
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="5" class="bad-text">${esc(err.message)}</td></tr>`;
    }
}

async function loadSettings() {
    try {
        st.data = await apiGet('/api/settings');
        fillForm(st.data.settings);
        renderUph();
        $('#setMsg').textContent = '';
        loadUsage();
    } catch (err) {
        $('#setMsg').textContent = t('common.loadFailedMsg', { error: err.message });
    }
}

function init(app) {
    appRef = app;
    auth.bindLoginForm({ user: $('#setUser'), pass: $('#setPass'), button: $('#btnSetLogin'), message: $('#setLoginMsg') });
    $('#setPanel').addEventListener('submit', save);
    ['yield_good', 'yield_warn'].forEach(n => field(n).addEventListener('input', renderPreview));
    $('#btnSetDefaults').addEventListener('click', () => {
        if (!st.data) return;
        fillForm(st.data.defaults);
        $$('#setUphRows .set-uph').forEach(i => { i.value = ''; });
        $('#setMsg').classList.remove('bad-text');
        $('#setMsg').textContent = t('set.defaultsNote');
    });
}

async function load() {
    renderAccess();
    if (auth.isAdmin()) await loadSettings();
}

export default {
    id: 'settings', scoped: false, init, load,
    onAuthChange: () => { renderAccess(); if (appRef?.page?.id === 'settings' && auth.isAdmin()) loadSettings(); },
    onLangChange: () => { if (st.data) renderUph(); },
};
