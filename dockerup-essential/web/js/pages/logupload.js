// Log Upload, a three-step wizard (5D, Q16): ① zip or folder → /api/log-upload/analyze | folder/start+files+finish
// ② confirm product, work order (or NOWO) and planned quantity in the form (/validate: rules R1–R16); the assistant
// beside it cross-checks the file-name guesses, the stored plan (Q4) and the warnings by rules, and the AI chat
// (/chat, same rules) opens only on request (Q17, LLM budget) ③ /commit (NDJSON progress, cancellable) → results,
// this batch against the product's history (rules; "Explain with AI" on request), the work order, per-file list
// (CSV/TXT), undo. A finished step folds into one line. Uploaders see and undo only their own batches.
// Server texts (checks, chat opening, errors) come in both languages; the AI replies in the UI language.
import { apiDelete, apiGet, apiPost, download, errorText, eventText, getToken, readNdjson, request, streamPost, toError } from '../core/api.js';
import * as auth from '../core/auth.js';
import { esc, fmtDateTime, fmtInt, fmtNum } from '../core/format.js';
import { getLang, localized, t } from '../core/i18n.js';
import { detailHref } from '../core/scope.js';
import { $, $$, debounce, toast } from '../core/ui.js';

const NEW_PRODUCT = '__new__';
const BATCH_FILES = 500;
const BATCH_BYTES = 50 * 1024 * 1024;

const st = {
    uploadId: null, owner: null, facts: null, products: [], committedId: null, busy: false, running: false,
    validation: null, validateSeq: 0, mode: 'form', aiAvailable: false, step: 'product',
    chat: [], chatBusy: false, extractFails: 0, openings: {}, fileLabel: null, result: null, reading: null, readingAi: null,
    wizard: 1,
};
const statusText = {};          // id → [text or () => text, kind], re-rendered on a language switch
let appRef;
let pendingHistoryUser = null;   // set by Accounts → "Uploads" before the options exist

function setStatus(id, text, kind = '') {
    statusText[id] = [text, kind];
    const el = $(id);
    el.textContent = typeof text === 'function' ? text() : text;
    el.className = `status-line ${kind}`;
}

/* ─── Access ─── */
function renderAccess() {
    const user = auth.currentUser();
    $('#luLogin').hidden = !!user;
    $('#luDenied').hidden = !user || auth.canUpload();
    $('#luPanel').hidden = !user || !auth.canUpload();
    const admin = auth.isAdmin();
    $('#luHistoryUser').hidden = !admin;
    $('#luHistoryFilterLabel').hidden = !admin;
    $('#luHistoryTitle').textContent = admin ? t('lu.history') : t('lu.historyMine');
    $('#luHistoryWhoHead').hidden = !admin;
    if (st.uploadId && !st.busy && (!user || user.username !== st.owner)) resetFlow();
}

/* ─── Step 1: choose + analyze ─── */
function resetFlow() {
    Object.assign(st, { uploadId: null, owner: null, facts: null, committedId: null, validation: null, chat: [], extractFails: 0,
                        openings: {}, fileLabel: null, result: null, step: 'product', running: false, reading: null, readingAi: null });
    st.validateSeq += 1;
    renderChat();
    $('#luChatInput').value = '';
    $('#luFile').value = '';
    $('#luFolder').value = '';
    $('#luFileInfo').textContent = t('lu.noFile');
    setStatus('#luAnalyzeStatus', '');
    $('#luFacts').hidden = true;
    $('#luStepConfirm').hidden = true;
    $('#luStepProgress').hidden = true;
    $('#luProduct').value = '';
    $('#luProductNew').value = '';
    $('#luProductNew').hidden = true;
    $('#luWo').value = '';
    $('#luQty').value = '';
    renderExtract();
    $('#luAck').checked = false;
    $('#luIssues').innerHTML = '';
    $('#luAckRow').hidden = true;
    $('#luAlign').hidden = true;
    renderChips();
    setInputsDisabled(false);
    updateUploadButton();
    $('#luReading').hidden = true;
    $('#btnLuViewWo').hidden = true;
    renderAssist();
    showStep(1);
}

/* ─── Wizard: one step on screen; the finished ones fold into the summary line ─── */
function showStep(n) {
    st.wizard = n;
    $('#luStep1').hidden = n !== 1;
    $('#luSummary').hidden = n === 1;
    $('#luStepConfirm').hidden = n !== 2;
    $('#luStepProgress').hidden = n !== 3;
    $('#luConfirmedLine').hidden = n !== 3;
    $$('#luSteps li[data-step]').forEach(li => {
        const k = Number(li.dataset.step);
        li.classList.toggle('done', k < n);
        li.classList.toggle('current', k === n);
    });
    renderSummary();
}

function renderSummary() {
    const f = st.facts;
    $('#luSummaryText').textContent = st.fileLabel || '';
    $('#luSummaryFacts').textContent = f ? t('lu.summaryFacts', { logs: fmtInt(f.logs), units: fmtInt(f.unique_sn), pass: fmtInt(f.pass),
                                                                  fail: fmtInt(f.fail), existing: fmtInt(f.existing_total) }) : '';
    const r = st.validation?.resolved;
    const qty = formFields().planned_qty;
    $('#luConfirmedLine').textContent = r ? t('lu.confirmedLine', { pm: r.product || '—', wo: r.work_order || '—', qty: qty ? fmtInt(qty) : '—' }) : '';
    $('#btnLuReset').hidden = st.wizard === 3 && st.running;
}

function setInputsDisabled(disabled) {
    ['#luProduct', '#luProductNew', '#luWo', '#luQty', '#luAck', '#btnLuReset', '#btnLuMode', '#btnLuFolder'].forEach(id => { $(id).disabled = disabled; });
    setChatDisabled(disabled || st.chatBusy);
    $('#luDrop').classList.toggle('disabled', disabled);
}

function uploadWithProgress(url, formData, onProgress) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', url);
        if (getToken()) xhr.setRequestHeader('X-Tweak-Token', getToken());
        xhr.upload.onprogress = e => { if (e.lengthComputable) onProgress(Math.round(e.loaded * 100 / e.total)); };
        xhr.onload = () => {
            let body = {};
            try { body = JSON.parse(xhr.responseText || '{}'); } catch { /* non-JSON error page */ }
            resolve({ status: xhr.status, body });
        };
        xhr.onerror = () => reject(new Error(t('err.network')));
        xhr.send(formData);
    });
}

function beginAnalyze(label) {
    resetFlow();
    st.fileLabel = label;
    $('#luFileInfo').textContent = label;
    st.busy = true;
    setInputsDisabled(true);
}

async function finishAnalyze(body) {
    st.uploadId = body.upload_id;
    st.owner = auth.currentUser()?.username;
    st.facts = body.facts;
    st.products = body.products || [];
    st.openings = { zh: body.chat_opening || '', en: body.chat_opening_en || '' };
    renderFacts(body.facts);
    renderProductOptions();
    setStatus('#luAnalyzeStatus', () => t('lu.analyzed'), 'ok');
    showStep(2);
    renderWoPlaceholder();
    st.aiAvailable = await checkAi();
    setMode('form');          // the form first (Q17); the AI assistant opens on request (each chat turn costs two LLM calls)
}

function endAnalyze() {
    st.busy = false;
    setInputsDisabled(false);
    updateUploadButton();
    if (st.uploadId) focusInput();
}

async function analyzeZip(file) {
    beginAnalyze(`${file.name}${t('common.dot')}${(file.size / 1048576).toFixed(1)} MB`);
    if (!/\.zip$/i.test(file.name)) { setStatus('#luAnalyzeStatus', () => t('lu.notZip'), 'error'); endAnalyze(); return; }
    setStatus('#luAnalyzeStatus', () => t('lu.uploading', { pct: 0 }));
    try {
        const fd = new FormData();
        fd.append('file', file);
        const { status, body } = await uploadWithProgress('/api/log-upload/analyze', fd, pct => {
            setStatus('#luAnalyzeStatus', () => (pct < 100 ? t('lu.uploading', { pct }) : t('lu.analyzing')));
        });
        if (status === 401) { auth.logout(); return; }
        if (status !== 200) { setStatus('#luAnalyzeStatus', () => errorText(body, status), 'error'); return; }
        await finishAnalyze(body);
    } catch (err) {
        setStatus('#luAnalyzeStatus', err.message, 'error');
    } finally {
        endAnalyze();
    }
}

async function analyzeFolder(fileList) {
    const files = [...fileList];
    const bytes = files.reduce((a, f) => a + f.size, 0);
    const top = files[0]?.webkitRelativePath?.split('/')[0] || 'folder';
    beginAnalyze(`${top}/${t('common.dot')}${files.length} files${t('common.dot')}${(bytes / 1048576).toFixed(1)} MB`);
    if (!files.length) { setStatus('#luAnalyzeStatus', () => t('lu.emptyFolder'), 'error'); endAnalyze(); return; }
    try {
        const start = await apiPost('/api/log-upload/folder/start', { name: top, files: files.length, bytes });
        const uploadId = start.upload_id;
        const batches = [];
        let cur = [], curBytes = 0;
        for (const f of files) {
            if (cur.length >= (start.batch_max_files || BATCH_FILES) || (curBytes + f.size > BATCH_BYTES && cur.length)) { batches.push(cur); cur = []; curBytes = 0; }
            cur.push(f);
            curBytes += f.size;
        }
        if (cur.length) batches.push(cur);
        let sent = 0;
        for (const batch of batches) {
            const fd = new FormData();
            batch.forEach(f => { fd.append('files', f, f.name); fd.append('paths', f.webkitRelativePath || f.name); });
            const done = sent;
            const { status, body } = await uploadWithProgress(`/api/log-upload/folder/${uploadId}/files`, fd, pct => {
                setStatus('#luAnalyzeStatus', () => t('lu.sendingBatch', { done: fmtInt(done + Math.round(batch.length * pct / 100)), total: fmtInt(files.length) }));
            });
            if (status === 401) { auth.logout(); return; }
            if (status !== 200) { setStatus('#luAnalyzeStatus', () => errorText(body, status), 'error'); return; }
            sent += batch.length;
        }
        setStatus('#luAnalyzeStatus', () => t('lu.analyzing'));
        const body = await apiPost(`/api/log-upload/folder/${uploadId}/finish`);
        await finishAnalyze(body);
    } catch (err) {
        setStatus('#luAnalyzeStatus', err.message, 'error');
    } finally {
        endAnalyze();
    }
}

function renderWoPlaceholder() {
    const inferred = st.facts?.inferred_wo;
    $('#luWo').placeholder = inferred ? t('lu.woPhInferred', { wo: inferred }) : t('lu.woPh');
}

function renderFacts(f) {
    const facts = [
        [t('lu.fact.logs'), fmtInt(f.logs)],
        [t('lu.fact.units'), fmtInt(f.unique_sn)],
        [t('lu.fact.pf'), `${fmtInt(f.pass)} / ${fmtInt(f.fail)}`],
        [t('lu.fact.existing'), fmtInt(f.existing_total)],
        [t('lu.fact.ignored'), fmtInt(f.ignored_total)],
        [t('lu.fact.period'), f.first_test ? `${f.first_test.slice(0, 16)} ~ ${f.last_test.slice(0, 16)}` : '—'],
    ];
    $('#luFactGrid').innerHTML = facts.map(([k, v]) =>
        `<div class="tile"><div class="tile-label">${esc(k)}</div><div class="tile-value small-value">${esc(v)}</div></div>`).join('');
    const parts = [f.inferred_product ? t('lu.inferredPm', { pm: f.inferred_product }) : '', f.inferred_wo ? t('lu.inferredWo', { wo: f.inferred_wo }) : ''].filter(Boolean);
    $('#luInferred').textContent = parts.length ? t('lu.inferred', { parts: parts.join(t('common.listSep')) }) : '';
    $('#luInferred').hidden = !parts.length;
    const ignored = f.ignored || [];
    $('#luIgnoredBox').hidden = !ignored.length;
    $('#luIgnoredSummary').textContent = t('lu.ignoredSummary', { n: f.ignored_total });
    $('#luIgnoredRows').innerHTML = ignored.map(i => {
        const key = `lu.reason.${i.reason}`;
        return `<tr><td class="truncate" title="${esc(i.name)}">${esc(i.name)}</td><td>${esc(t(key) === key ? (i.reason || '') : t(key))}</td></tr>`;
    }).join('');
    $('#luFacts').hidden = false;
}

function renderProductOptions() {
    const sel = $('#luProduct');
    const current = sel.value;
    sel.innerHTML = '';
    sel.appendChild(new Option(t('lu.productSelect'), ''));
    st.products.forEach(p => sel.appendChild(new Option(p.units ? `${p.canonical}  (${fmtInt(p.units)})` : p.canonical, p.canonical)));
    sel.appendChild(new Option(t('lu.productNew'), NEW_PRODUCT));
    sel.value = [...sel.options].some(o => o.value === current) ? current : '';
    $('#luProductNew').hidden = sel.value !== NEW_PRODUCT;
}

/** The product text the form stands for (select, or the typed new name). */
function productValue() {
    const sel = $('#luProduct').value;
    return sel === NEW_PRODUCT ? $('#luProductNew').value.trim() : sel;
}

function setProductValue(text) {
    const sel = $('#luProduct');
    if (!text) { sel.value = ''; $('#luProductNew').value = ''; }
    else if ([...sel.options].some(o => o.value === text)) { sel.value = text; }
    else { sel.value = NEW_PRODUCT; $('#luProductNew').value = text; }
    $('#luProductNew').hidden = sel.value !== NEW_PRODUCT;
}

/* ─── Step 2 (form): server rules ─── */
function formFields() {
    return { product: productValue(), work_order: $('#luWo').value.trim(), planned_qty: $('#luQty').value.trim() };
}

async function validate() {
    if (!st.uploadId || st.committedId) return;
    const mine = ++st.validateSeq;
    try {
        const data = await apiPost('/api/log-upload/validate', { upload_id: st.uploadId, ...formFields() });
        if (mine !== st.validateSeq) return;
        st.validation = data;
        renderIssues(data.issues);
    } catch (err) {
        if (mine !== st.validateSeq) return;
        st.validation = null;
        renderIssues([{ level: 'error', message: err.message }]);
    }
    updateUploadButton();
}

function renderIssues(issues) {
    const icon = { error: '❌', warn: '⚠️', info: 'ℹ️' };
    const order = { error: 0, warn: 1, info: 2 };
    // In chat mode the assistant asks for missing values itself; "please enter …" errors would just repeat it.
    const hidden = new Set(['product_empty', 'wo_empty', 'qty_empty']);
    $('#luIssues').innerHTML = issues
        .filter(i => !(st.mode === 'chat' && hidden.has(i.reason)) && i.code !== 'R14')
        .sort((a, b) => order[a.level] - order[b.level])
        .map(i => `<li class="${esc(i.level)}">${icon[i.level] || ''} ${esc(localized(i.message, i.message_en))}${
            (i.suggestions || []).length ? ` <span class="chips">${i.suggestions.map(s => `<button class="btn small" type="button" data-pick="${esc(s)}">${esc(s)}</button>`).join('')}</span>` : ''}</li>`).join('');
    const needsAck = issues.some(i => i.level === 'warn');
    $('#luAckRow').hidden = !needsAck;
    if (!needsAck) $('#luAck').checked = false;
    renderAlign(issues.find(i => i.code === 'R14'));
    renderPlanHint();
    renderAssist();
}

/** The work order's stored plan (administrator or an earlier batch, Q4): offered, never filled in silently. */
function renderPlanHint() {
    const plan = st.validation?.plan;
    const el = $('#luPlanHint');
    const qty = formFields().planned_qty;
    if (!plan) { el.hidden = true; el.innerHTML = ''; return; }
    const who = plan.by_admin ? t('lu.planByAdmin', { who: plan.updated_by || '—' }) : t('lu.planByBatch', { who: plan.updated_by || '—' });
    const same = String(plan.planned_qty) === String(qty).replace(/,/g, '');
    el.hidden = false;
    el.innerHTML = `${esc(t('lu.planHint', { qty: fmtInt(plan.planned_qty), who, time: fmtDateTime(plan.updated_at) }))}${same ? '' :
        ` <button class="btn small" type="button" data-use-plan="${esc(plan.planned_qty)}">${esc(t('lu.usePlan', { qty: fmtInt(plan.planned_qty) }))}</button>`}`;
}

/** The assistant's checks, by rules (no LLM call): file-name guesses against the input, the plan, duplicates, what blocks the upload. */
function renderAssist() {
    const box = $('#luAssistChecks');
    if (!st.facts) { box.innerHTML = ''; return; }
    const f = st.facts;
    const fields = formFields();
    const items = [];
    const add = (level, text, action = '') => items.push(`<li class="${level}">${esc(text)}${action}</li>`);
    const use = (attr, value) => ` <button class="btn small" type="button" ${attr}="${esc(value)}">${esc(t('lu.chk.use', { v: value }))}</button>`;
    if (f.inferred_product) {
        if (!fields.product) add('info', t('lu.chk.pmGuess', { v: f.inferred_product }), use('data-use-pm', f.inferred_product));
        else if (fields.product.toLowerCase() === f.inferred_product.toLowerCase() || st.validation?.resolved?.product === f.inferred_product) add('ok', t('lu.chk.pmSame'));
        else add('warn', t('lu.chk.pmDiff', { guess: f.inferred_product, v: fields.product }), use('data-use-pm', f.inferred_product));
    } else add('info', t('lu.chk.pmNone'));
    if (f.inferred_wo) {
        if (!fields.work_order) add('info', t('lu.chk.woGuess', { v: f.inferred_wo }), use('data-use-wo', f.inferred_wo));
        else if (fields.work_order === f.inferred_wo) add('ok', t('lu.chk.woSame'));
        else add('warn', t('lu.chk.woDiff', { guess: f.inferred_wo, v: fields.work_order }), use('data-use-wo', f.inferred_wo));
    }
    if (f.existing_total) add('info', t('lu.chk.existing', { n: fmtInt(f.existing_total), logs: fmtInt(f.logs) }));
    const plan = st.validation?.plan;
    if (fields.work_order && st.validation) {
        if (plan) add('ok', t('lu.chk.plan', { qty: fmtInt(plan.planned_qty), tested: fmtInt(plan.tested) }));
        else add('info', fields.planned_qty ? t('lu.chk.newPlan', { qty: fmtInt(fields.planned_qty) }) : t('lu.chk.noPlan'));
    }
    const issues = st.validation?.issues || [];
    const errors = issues.filter(i => i.level === 'error').length;
    const warns = issues.filter(i => i.level === 'warn').length;
    if (st.validation) {
        if (errors) add('error', t('lu.chk.errors', { n: errors }));
        else if (warns) add('warn', t('lu.chk.warns', { n: warns }));
        else add('ok', t('lu.chk.ready'));
    }
    box.innerHTML = items.join('');
}

function renderAlign(a) {
    $('#luAlign').hidden = !a;
    if (!a) return;
    const gapText = a.state === 'met' ? t('lu.align.met') : a.state === 'short' ? t('lu.align.short', { n: fmtInt(-a.gap) }) : t('lu.align.over', { n: fmtInt(a.gap) });
    const cls = a.state === 'met' ? 'good-text' : a.state === 'short' ? 'warn-text' : 'bad-text';
    const tiles = [[t('lu.align.planned'), fmtInt(a.planned), ''], [t('lu.align.batch'), fmtInt(a.batch_sn), ''],
                   [t('lu.align.cumulative'), fmtInt(a.cumulative), ''], [t('lu.align.gap'), gapText, cls]];
    $('#luAlignGrid').innerHTML = tiles.map(([k, v, c]) =>
        `<div class="tile"><div class="tile-label">${esc(k)}</div><div class="tile-value small-value ${c}">${esc(v)}</div></div>`).join('');
}

function updateUploadButton() {
    const v = st.validation;
    $('#btnLuUpload').disabled = st.busy || st.chatBusy || !!st.committedId || !v || !v.can_upload
        || (v.needs_ack && !$('#luAck').checked);
}

/* ─── Step 2 (AI chat) ─── */
async function checkAi() {
    try { return !!(await apiGet('/api/llm-status')).connected; } catch { return false; }
}

function setMode(mode, note = '') {
    const prev = st.mode;
    st.mode = mode;
    const chat = mode === 'chat';
    $('#luChatPanel').hidden = !chat;
    $('#luAssistChecks').hidden = chat;
    renderModeLabels();
    if (note) st.chat.push({ role: 'note', content: note });
    if (chat) {
        if (!st.chat.some(m => m.role === 'assistant')) st.chat.unshift({ role: 'assistant', content: localized(st.openings.zh, st.openings.en) });
        const f = formFields();
        if (prev === 'form' && (f.product || f.work_order || f.planned_qty)) {
            st.chat.push({ role: 'note', content: t('lu.formValues', { pm: f.product || '—', wo: f.work_order || '—', qty: f.planned_qty || '—' }) });
        }
    }
    renderChat();
    renderChips();
    renderExtract();
    if (!chat) validate();
    else if (st.validation) renderIssues(st.validation.issues);
    else { $('#luIssues').innerHTML = ''; $('#luAlign').hidden = true; }
    updateUploadButton();
    if (!st.busy) focusInput();
}

function renderModeLabels() {
    const chat = st.mode === 'chat';
    $('#luModeLabel').textContent = chat ? t('lu.modeChat') : t('lu.modeForm');
    $('#luModeHint').textContent = chat ? t('lu.chatHint') : (st.aiAvailable ? '' : t('lu.aiOffline'));
    const btn = $('#btnLuMode');
    btn.textContent = chat ? t('lu.toForm') : t('lu.toChat');
    btn.hidden = !(st.aiAvailable || chat);
}

function renderChips() {
    const box = $('#luChips');
    const show = st.mode === 'chat' && st.uploadId && !st.committedId && st.step === 'product' && st.products.length;
    box.hidden = !show;
    if (!show) { box.innerHTML = ''; return; }
    box.innerHTML = `<span class="muted small-text">${esc(t('lu.chips'))}</span> ` +
        st.products.map(p => `<button class="btn small" type="button" data-chip="${esc(p.canonical)}">${esc(p.canonical)}</button>`).join('');
}

function focusInput() {
    if (st.mode === 'chat') $('#luChatInput').focus();
    else {
        const f = formFields();
        $(!f.product ? '#luProduct' : !f.work_order ? '#luWo' : '#luQty').focus();
    }
}

function bubble(role, text) {
    const div = document.createElement('div');
    div.className = `msg ${role}`;
    div.textContent = text;
    return div;
}

function renderChat() {
    const log = $('#luChatLog');
    log.innerHTML = '';
    st.chat.forEach(m => log.appendChild(bubble(m.role, m.content)));
    log.scrollTop = log.scrollHeight;
}

function renderExtract() {
    const f = formFields();
    $('#luExtract').textContent = (f.product || f.work_order || f.planned_qty)
        ? t('lu.extract', { pm: f.product || t('lu.notGiven'), wo: f.work_order || t('lu.notGiven'),
                            qty: f.planned_qty ? `${fmtInt(f.planned_qty)} ${t('common.unit')}` : t('lu.notGiven') })
        : t('lu.extractNone');
}

function setChatDisabled(disabled) {
    $('#luChatInput').disabled = disabled;
    $('#btnLuChatSend').disabled = disabled;
}

async function chatSend(text) {
    const input = $('#luChatInput');
    text = (text ?? input.value).trim();
    if (!text || st.chatBusy || st.busy || !st.uploadId || st.committedId) return;
    input.value = '';
    st.chat.push({ role: 'user', content: text });
    renderChat();
    st.chatBusy = true;
    setChatDisabled(true);
    $('#btnLuMode').disabled = true;
    updateUploadButton();
    const log = $('#luChatLog');
    const typing = bubble('assistant typing', '');
    log.appendChild(typing);
    log.scrollTop = log.scrollHeight;
    let reply = null, fallback = null;
    try {
        await streamPost('/api/log-upload/chat', {
            upload_id: st.uploadId,
            messages: st.chat.filter(m => m.role !== 'note'),
            known: formFields(),
            lang: getLang(),
        }, ev => {
            if (ev.type === 'extract') {
                setProductValue(ev.product || '');
                $('#luWo').value = ev.work_order || '';
                $('#luQty').value = ev.planned_qty != null && ev.planned_qty !== '' ? String(ev.planned_qty) : '';
                st.step = ev.step || 'product';
                st.extractFails = ev.extract_ok ? 0 : st.extractFails + 1;
                renderExtract();
                renderChips();
            } else if (ev.type === 'validation') {
                st.validateSeq += 1;   // drop any in-flight form validation
                st.validation = ev;
                renderIssues(ev.issues);
            } else if (ev.type === 'delta') {
                typing.textContent += ev.text;
                log.scrollTop = log.scrollHeight;
            } else if (ev.type === 'replace') {
                typing.textContent = ev.text;
            } else if (ev.type === 'done') {
                reply = ev.reply;
            } else if (ev.type === 'fallback') {
                fallback = localized(ev.reason, ev.reason_en);
            }
        });
        if (!reply && !fallback) fallback = t('lu.fallbackDefault');
    } catch (err) {
        fallback = err.message;
    } finally {
        typing.remove();
        st.chatBusy = false;
        setChatDisabled(false);
        $('#btnLuMode').disabled = false;
        if (!st.uploadId) {
            // flow was reset meanwhile (e.g. signed out)
        } else if (reply && !fallback && st.extractFails < 3) {
            st.chat.push({ role: 'assistant', content: reply });
            renderChat();
            updateUploadButton();
            $('#luChatInput').focus();
        } else {
            if (reply) st.chat.push({ role: 'assistant', content: reply });
            st.aiAvailable = false;
            setMode('form', t('lu.fallback', { reason: fallback || t('lu.fallbackDefault') }));
        }
    }
}

/* ─── Step 3: commit + progress stream ─── */
async function commit() {
    const f = formFields();
    if (!confirm(t('lu.confirm', { pm: st.validation?.resolved?.product || f.product, wo: st.validation?.resolved?.work_order || f.work_order, qty: f.planned_qty }))) return;
    st.busy = true;
    st.running = true;
    setInputsDisabled(true);
    updateUploadButton();
    showStep(3);
    $('#luResultGrid').hidden = true;
    $('#luResultNote').textContent = '';
    $('#luFailureBox').hidden = true;
    $('#btnLuRevert').hidden = true;
    $('#btnLuNext').hidden = true;
    $('#luItemsRow').hidden = true;
    $('#btnLuCancel').hidden = false;
    $('#btnLuCancel').disabled = false;
    const fill = $('#luProgressFill');
    fill.className = '';
    fill.style.width = '0%';
    setStatus('#luProgressText', () => t('lu.prepare'));
    let finished = false;
    try {
        const res = await request('/api/log-upload/commit', {
            method: 'POST', raw: true,
            body: { upload_id: st.uploadId, ...f, ack_warnings: $('#luAck').checked, mode: st.mode },
        });
        if (!res.ok) {
            const err = await toError(res);
            if (err.data.issues) { st.validation = err.data; renderIssues(err.data.issues); }
            showStep(2);            // nothing was written: back to the form, the reason stays visible
            setStatus('#luAnalyzeStatus', () => errorText(err.data, err.status), 'error');
            const msg = document.createElement('li');
            msg.className = 'error';
            msg.textContent = `❌ ${errorText(err.data, err.status)}`;
            $('#luIssues').prepend(msg);
            return;
        }
        await readNdjson(res, ev => { if (handleEvent(ev)) finished = true; });
        if (!finished) setStatus('#luProgressText', () => t('lu.disconnected'), 'error');
    } catch (err) {
        setStatus('#luProgressText', err.message, 'error');
    } finally {
        st.busy = false;
        st.running = false;
        $('#btnLuCancel').hidden = true;
        if (!st.committedId) setInputsDisabled(false);
        updateUploadButton();
        loadHistory();
    }
}

/** Returns true once the stream reached a terminal event. */
function handleEvent(ev) {
    const fill = $('#luProgressFill');
    if (ev.type === 'start') {
        setStatus('#luProgressText', () => t('lu.start', { pm: ev.product, wo: ev.work_order, total: fmtInt(ev.total) }));
    } else if (ev.type === 'progress') {
        fill.style.width = `${Math.round(ev.done * 100 / Math.max(ev.total, 1))}%`;
        setStatus('#luProgressText', () => t('lu.progress', { done: fmtInt(ev.done), total: fmtInt(ev.total), uploaded: fmtInt(ev.uploaded), skipped: fmtInt(ev.skipped), failed: fmtInt(ev.failed) }));
    } else if (ev.type === 'done' || ev.type === 'error') {
        st.committedId = st.uploadId;
        if (ev.type === 'done' && ev.status === 'cancelled') {
            fill.className = 'error';
            setStatus('#luProgressText', () => t('lu.cancelled', { done: fmtInt(ev.done), total: fmtInt(ev.total) }), 'error');
            appRef.refreshOptions();
        } else if (ev.type === 'done') {
            fill.style.width = '100%';
            fill.className = 'done';
            setStatus('#luProgressText', () => t('lu.done', { sec: ev.elapsed_sec }), 'ok');
            appRef.refreshOptions();
        } else {
            fill.className = 'error';
            setStatus('#luProgressText', () => t('lu.error', { message: eventText(ev) }), 'error');
        }
        renderResults(ev);
        $('#btnLuRevert').hidden = !(ev.uploaded > 0);
        $('#btnLuNext').hidden = false;
        $('#luItemsRow').hidden = false;
        const r = st.validation?.resolved;
        const wo = ev.work_order || r?.work_order;
        if (wo && ev.uploaded > 0) {
            const a = $('#btnLuViewWo');
            a.hidden = false;
            a.href = detailHref(wo, ev.product || r?.product);
            a.textContent = t('lu.viewWo', { wo });
        }
        if (ev.uploaded > 0) loadReading();
        return true;
    }
    return false;
}

function renderResults(ev) {
    st.result = ev;
    const cards = [[t('lu.res.new'), ev.uploaded, 'good-text'], [t('lu.res.skipped'), ev.skipped, ''],
                   [t('lu.res.failed'), ev.failed, ev.failed ? 'bad-text' : ''], [t('lu.res.cancelled'), ev.cancelled || 0, ev.cancelled ? 'warn-text' : '']];
    $('#luResultGrid').innerHTML = cards.map(([k, v, cls]) =>
        `<div class="tile"><div class="tile-label">${esc(k)}</div><div class="tile-value ${cls}">${fmtInt(v)}</div></div>`).join('');
    $('#luResultGrid').hidden = false;
    const notes = [t('lu.dupPolicy')];
    if (ev.nul_files) notes.push(t('lu.nul', { files: fmtInt(ev.nul_files), bytes: fmtInt(ev.nul_bytes) }));
    $('#luResultNote').textContent = notes.join(' ');
    const failures = ev.failures || [];
    $('#luFailureBox').hidden = !failures.length;
    $('#luFailureSummary').textContent = t('lu.failSummary', { n: failures.length });
    $('#luFailureRows').innerHTML = failures.map(f => `<tr><td class="truncate" title="${esc(f.filename)}">${esc(f.filename)}</td><td>${esc(f.error)}</td></tr>`).join('');
}

/* ─── This batch against the product's history (rules; the LLM words it only on request) ─── */
async function loadReading() {
    const id = st.committedId;
    try {
        st.reading = await apiGet(`/api/log-upload/${id}/reading`);
        st.readingAi = null;
        renderReading();
    } catch { /* the results stay without the reading */ }
}

function readingText(p) {
    const v = p.values || {};
    const vars = { ...v, y: fmtNum(v.y, 2), hist: v.hist != null ? fmtNum(v.hist, v.y != null ? 2 : 1) : '—', p50: fmtNum(v.p50, 1),
                   top: localized(v.top_zh, v.top_en) || v.top || '' };
    return t(`lu.read.${p.code}`, vars);
}

function renderReading() {
    const r = st.reading;
    const box = $('#luReading');
    if (!r) { box.hidden = true; return; }
    box.hidden = false;
    const cls = { good: 'ok', warn: 'warn', bad: 'error', info: 'info', none: 'info' };
    $('#luReadPoints').innerHTML = r.points.map(p => `<li class="${cls[p.level] || 'info'}">${esc(readingText(p))}</li>`).join('');
    $('#btnLuReadAi').hidden = !st.aiAvailable || !!st.readingAi;
    const ai = $('#luReadAiText');
    ai.hidden = !st.readingAi;
    if (st.readingAi) ai.innerHTML = `<span class="ai-tag">${esc(t('wo.ai'))}</span>${esc(st.readingAi.text || t('lu.readAiEmpty'))}`;
}

async function readingAi() {
    const btn = $('#btnLuReadAi');
    btn.disabled = true;
    try {
        st.readingAi = await apiPost(`/api/log-upload/${st.committedId}/reading/ai?lang=${getLang()}`);
        renderReading();
    } catch (err) {
        toast(err.message, 'bad');
    } finally {
        btn.disabled = false;
    }
}

async function cancelUpload() {
    if (!st.uploadId || !st.running) return;
    $('#btnLuCancel').disabled = true;
    try {
        await apiPost(`/api/log-upload/${st.uploadId}/cancel`);
        toast(t('lu.cancelRequested'));
    } catch (err) {
        toast(err.message, 'bad');
        $('#btnLuCancel').disabled = false;
    }
}

async function revert(uploadId, label = '') {
    if (!uploadId || !confirm(t('lu.revertConfirm', { label: label ? t('common.paren', { text: label }) : '' }))) return false;
    try {
        const data = await apiDelete(`/api/log-upload/${uploadId}`);
        if (uploadId === st.committedId) setStatus('#luProgressText', () => t('lu.reverted', { n: fmtInt(data.reverted) }), 'ok');
        appRef.refreshOptions();
        loadHistory();
        return true;
    } catch (err) {
        alert(err.message);
        return false;
    }
}

async function downloadItems(uploadId, fmt) {
    try { await download(`/api/log-upload/${uploadId}/items?format=${fmt}`, `db_upload_log.${fmt}`); } catch (err) { toast(err.message, 'bad'); }
}

/* ─── History ─── */
const STATUS = { done: 'done', failed: 'failed', reverted: 'reverted', running: 'running', cancelled: 'warn' };

async function loadUploaderOptions() {
    try {
        const data = await apiGet('/api/admin/users');
        const sel = $('#luHistoryUser');
        const current = pendingHistoryUser ?? sel.value;
        pendingHistoryUser = null;
        sel.innerHTML = '';
        sel.appendChild(new Option(t('common.all'), ''));
        sel.appendChild(new Option(`${data.admin.username}${t('lu.adminRole')}`, data.admin.username));
        data.items.forEach(u => sel.appendChild(new Option(auth.displayName(u), u.username)));
        sel.value = [...sel.options].some(o => o.value === current) ? current : '';
        return data;
    } catch { return null; }
}

async function loadHistory() {
    if (!auth.isSignedIn() || !auth.canUpload()) return;     // RD accounts have no upload history
    const tbody = $('#luHistoryRows');
    const filterUser = auth.isAdmin() ? $('#luHistoryUser').value : '';
    try {
        const data = await apiGet(`/api/log-upload/history?limit=20${filterUser ? `&username=${encodeURIComponent(filterUser)}` : ''}`);
        const cols = auth.isAdmin() ? 8 : 7;
        if (!data.items.length) { tbody.innerHTML = `<tr><td colspan="${cols}" class="muted">${esc(t('lu.none'))}</td></tr>`; return; }
        const me = auth.currentUser();
        tbody.innerHTML = data.items.map(r => {
            const canRevert = ['done', 'failed', 'cancelled'].includes(r.status) && r.uploaded > 0 && (auth.isAdmin() || r.username === me.username);
            const who = r.display_name && r.display_name !== r.username ? t('common.nameWithId', { name: r.display_name, id: r.username }) : (r.username || '—');
            const tip = r.status === 'reverted' && r.reverted_by ? t('lu.revertedBy', { user: r.reverted_by, time: fmtDateTime(r.reverted_at) }) : (r.error || '');
            const result = r.uploaded != null ? t('lu.histResult', { n: fmtInt(r.uploaded), s: fmtInt(r.skipped), f: fmtInt(r.failed) }) : '—';
            const planTip = r.planned_qty != null ? t('lu.histPlan', { qty: fmtInt(r.planned_qty) }) : '';
            return `<tr>
                <td class="nowrap">${esc(fmtDateTime(r.created_at))}</td>
                <td class="nowrap">${r.work_order && r.uploaded ? `<a class="wo-link" href="${esc(detailHref(r.work_order, r.product_model))}" title="${esc(planTip)}">${esc(r.work_order)}</a>` : esc(r.work_order || '—')}</td>
                <td class="nowrap">${esc(r.product_model || '—')}${r.new_product ? ` <span class="badge warn">${esc(t('lu.newProductTag'))}</span>` : ''}</td>
                <td class="truncate" title="${esc(r.source_name || '')}">${r.source_kind === 'folder' ? '📁 ' : '🗜 '}${esc(r.source_name || '—')}</td>
                <td class="nowrap num">${esc(result)}</td>
                <td class="nowrap"><span class="badge ${STATUS[r.status] || 'muted'}" title="${esc(tip)}">${esc(t(`lu.status.${r.status}`))}</span>${r.mode === 'chat' ? ` <span title="${esc(t('lu.viaChat'))}">🤖</span>` : ''}</td>
                ${auth.isAdmin() ? `<td class="nowrap">${esc(who)}</td>` : ''}
                <td class="nowrap">
                    <button class="btn small" type="button" data-items="${esc(r.id)}" title="${esc(t('lu.items.csv'))}">CSV</button>
                    ${canRevert ? `<button class="btn small" type="button" data-revert="${esc(r.id)}" data-label="${esc(r.work_order || '')}">${esc(t('lu.revert'))}</button>` : ''}
                </td>
            </tr>`;
        }).join('');
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="8" class="bad-text">${esc(err.message)}</td></tr>`;
    }
}

/* ─── Wiring ─── */
function init(app) {
    appRef = app;
    auth.bindLoginForm({ user: $('#luUser'), pass: $('#luPass'), button: $('#btnLuLogin'), message: $('#luLoginMsg') });
    const drop = $('#luDrop');
    drop.addEventListener('click', () => { if (!st.busy) $('#luFile').click(); });
    drop.addEventListener('keydown', e => { if ((e.key === 'Enter' || e.key === ' ') && !st.busy) { e.preventDefault(); $('#luFile').click(); } });
    drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('dragover'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
    drop.addEventListener('drop', e => {
        e.preventDefault();
        drop.classList.remove('dragover');
        const file = e.dataTransfer.files?.[0];
        if (file && !st.busy) analyzeZip(file);
    });
    $('#luFile').addEventListener('change', e => { const file = e.target.files?.[0]; if (file) analyzeZip(file); });
    $('#btnLuFolder').addEventListener('click', () => { if (!st.busy) $('#luFolder').click(); });
    $('#luFolder').addEventListener('change', e => { if (e.target.files?.length) analyzeFolder(e.target.files); });
    const debounced = debounce(validate, 300);
    const changed = () => { st.validation = null; updateUploadButton(); renderExtract(); renderAssist(); debounced(); };
    ['#luProductNew', '#luWo', '#luQty'].forEach(id => $(id).addEventListener('input', changed));
    $('#luProduct').addEventListener('change', () => {
        $('#luProductNew').hidden = $('#luProduct').value !== NEW_PRODUCT;
        if ($('#luProduct').value === NEW_PRODUCT) $('#luProductNew').focus();
        changed();
    });
    $('#luAck').addEventListener('change', updateUploadButton);
    $('#luIssues').addEventListener('click', e => {
        const btn = e.target.closest('[data-pick]');
        if (!btn) return;
        if (st.mode === 'chat') chatSend(btn.dataset.pick);
        else { setProductValue(btn.dataset.pick); changed(); }
    });
    $('#luChips').addEventListener('click', e => { const btn = e.target.closest('[data-chip]'); if (btn) chatSend(btn.dataset.chip); });
    $('#btnLuReset').addEventListener('click', resetFlow);
    $('#btnLuBack').addEventListener('click', resetFlow);
    $('#btnLuReadAi').addEventListener('click', readingAi);
    $('#luStepConfirm').addEventListener('click', e => {
        const plan = e.target.closest('[data-use-plan]');
        const pm = e.target.closest('[data-use-pm]');
        const wo = e.target.closest('[data-use-wo]');
        if (plan) $('#luQty').value = plan.dataset.usePlan;
        else if (pm) setProductValue(pm.dataset.usePm);
        else if (wo) $('#luWo').value = wo.dataset.useWo;
        else return;
        changed();
    });
    $('#btnLuNext').addEventListener('click', () => { resetFlow(); $('#luDrop').focus(); });
    $('#btnLuMode').addEventListener('click', () => { if (!st.chatBusy) setMode(st.mode === 'chat' ? 'form' : 'chat'); });
    $('#btnLuChatSend').addEventListener('click', () => chatSend());
    $('#luChatInput').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); chatSend(); } });
    $('#btnLuUpload').addEventListener('click', commit);
    $('#btnLuCancel').addEventListener('click', cancelUpload);
    $('#btnLuRevert').addEventListener('click', async () => { if (await revert(st.committedId)) $('#btnLuRevert').hidden = true; });
    $('#btnLuItemsCsv').addEventListener('click', () => downloadItems(st.committedId, 'csv'));
    $('#btnLuItemsTxt').addEventListener('click', () => downloadItems(st.committedId, 'txt'));
    $('#btnLuHistoryRefresh').addEventListener('click', loadHistory);
    $('#luHistoryUser').addEventListener('change', loadHistory);
    $('#luHistoryRows').addEventListener('click', async e => {
        const items = e.target.closest('[data-items]');
        if (items) { downloadItems(items.dataset.items, 'csv'); return; }
        const btn = e.target.closest('[data-revert]');
        if (btn && await revert(btn.dataset.revert, btn.dataset.label) && btn.dataset.revert === st.committedId) $('#btnLuRevert').hidden = true;
    });
    resetFlow();
}

async function load() {
    renderAccess();
    if (!auth.isSignedIn()) return;
    if (auth.isAdmin()) await loadUploaderOptions();
    await loadHistory();
}

/** Language switched: re-render what this page drew (the conversation so far stays as written). */
function onLangChange() {
    renderAccess();
    $('#luFileInfo').textContent = st.fileLabel || t('lu.noFile');
    for (const [id, [text, kind]] of Object.entries(statusText)) setStatus(id, text, kind);
    if (st.facts) { renderFacts(st.facts); renderWoPlaceholder(); renderProductOptions(); }
    renderModeLabels();
    renderChips();
    renderExtract();
    if (st.validation) renderIssues(st.validation.issues);
    else renderAssist();
    if (st.result) renderResults(st.result);
    renderSummary();
    renderReading();
    if (appRef?.page?.id === 'logupload' && st.uploadId && !st.committedId) loadHistory();   // load() is skipped while busy
}

export default {
    id: 'logupload', scoped: false, init, load, onLangChange,
    onAuthChange: () => { renderAccess(); if (appRef?.page?.id === 'logupload') load(); },
    isBusy: () => st.busy || st.chatBusy || (!!st.uploadId && !st.committedId),
    /** Used by Accounts → "Uploads". */
    showHistoryFor(username) { pendingHistoryUser = username; },
    loadUploaderOptions,
};
