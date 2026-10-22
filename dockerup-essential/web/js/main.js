// App shell: hash routing, the filter bar (range + product + work order), data freshness,
// theme, language, clock, idle rotation.
import { apiGet, apiPost, onApiStatus } from './core/api.js';
import * as auth from './core/auth.js';
import * as charts from './core/charts.js';
import { fmtClockDate, fmtDate, fmtShortDate, fmtTime, setThresholds } from './core/format.js';
import { applyI18n, getLang, initLang, onLangChange, setLang, t } from './core/i18n.js';
import { presetRange, scopeKey, scopeParams } from './core/scope.js';
import { $, $$, initEscapeClose, initFormModal, toast, closeFormModal } from './core/ui.js';
import report from './report.js';
import overview from './pages/overview.js';
import workorders from './pages/workorders.js';
import fails, { closeFailDetail } from './pages/fails.js';
import rf from './pages/rf.js';
import production from './pages/production.js';
import dbtweak from './pages/dbtweak.js';
import logupload from './pages/logupload.js';
import products from './pages/products.js';
import accounts from './pages/accounts.js';
import settings from './pages/settings.js';
import wodetail from './pages/wodetail.js';
import rd from './pages/rd.js';
import reports from './pages/reports.js';
import * as alerts from './alerts.js';
import * as askbox from './askbox.js';

const PAGES = [overview, workorders, fails, reports, rf, production, logupload, products, dbtweak, accounts, settings, wodetail, rd];
const byId = Object.fromEntries(PAGES.map(p => [p.id, p]));
// The line TV: the Trend page's two views (Trend ↔ Forecast, 30 s each), first for the latest work order, then for all
// work orders, so the scope changes every 60 s. A TV address with ?tv=1 rotates at once instead of after 5 idle minutes.
const TV = new URLSearchParams(location.search).get('tv') === '1';
const IDLE_MS = TV ? 0 : 5 * 60 * 1000;
const ROTATE_MS = 30 * 1000;
const ROTATION = [
    { hash: 'overview', wo: 'latest' }, { hash: 'overview?view=forecast', wo: 'latest' },
    { hash: 'overview', wo: 'all' }, { hash: 'overview?view=forecast', wo: 'all' },
];

export const app = {
    page: null,
    scope: { range: 'all', workOrder: '', product: '', from: '', to: '' },
    options: { work_orders: [], products: [], latest_work_order: null },
    meta: null,
    params() { return scopeParams(this.scope); },
    scopeKey() { return scopeKey(this.scope); },
    latestWorkOrder() { return this.options.latest_work_order || ''; },
    openReport(wo, product) { report.open(wo, product); },
    reload() { return loadPage(); },
    /** Set the filter bar (e.g. a detail page's "all failures" link) before switching pages. */
    setScope(next) {
        Object.assign(this.scope, next);
        $('#dateFrom').value = this.scope.from || '';
        $('#dateTo').value = this.scope.to || '';
        renderRange();
        renderProductOptions();
        renderWoOptions();
        updateScopeLabel();
    },
    refreshOptions: () => loadOptions(),
};

/* ─── Routing ─── */
/** Where an address without a page goes: the TV, anyone signed out and the administrator → Trend;
 *  uploaders → Log Upload, RD → Advanced Analysis. */
function landingPage() {
    const user = auth.currentUser();
    if (!user || TV) return 'overview';
    return { uploader: 'logupload', rd: 'rd' }[user.role] || 'overview';
}

function pageFromHash() {
    const id = location.hash.replace(/^#/, '').split('?')[0];
    return byId[id] ? id : landingPage();
}

async function show(id) {
    const page = byId[id];
    app.page = page;
    $$('.page').forEach(s => { s.hidden = s.id !== `page-${id}`; });
    $$('.nav-item[data-page]').forEach(a => {
        if (a.dataset.page === id) a.setAttribute('aria-current', 'page');
        else a.removeAttribute('aria-current');
    });
    $('#pageTitle').textContent = t(`page.${id}`);
    document.title = `${t(`page.${id}`)} · ${t('app.title')}`;
    $('#filterBar').hidden = !page.scoped;
    $('#ovViewSeg').hidden = id !== 'overview';
    updateScopeLabel();
    await loadPage();
}

async function loadPage() {
    const page = app.page;
    if (!page) return;
    try {
        await page.load(app);
    } catch (err) {
        console.error(`page ${page.id} failed`, err);
    }
}

/* ─── Filter bar ─── */
// The product shown in the scope: the product filter, or the product of the selected work order (the default view is
// the latest work order with "all products", which alone would not say which product is on screen).
function scopeProduct() {
    if (app.scope.product) return app.scope.product;
    const wo = app.scope.workOrder && app.options?.work_orders?.find(w => w.work_order === app.scope.workOrder);
    return wo?.product_model || '';
}

function updateScopeLabel() {
    const el = $('#pageScope');
    el.replaceChildren();
    if (!app.page?.scoped) return;
    const product = scopeProduct();
    const all = !product && !app.scope.workOrder;     // all products, all work orders (the TV's other view)
    if (product || all) {
        const badge = document.createElement('span');
        badge.className = all ? 'scope-product all' : 'scope-product';
        badge.title = all ? '' : t('filter.productAria');
        badge.textContent = all ? t('filter.scopeAllWo') : product;
        el.appendChild(badge);
    }
    const parts = [];
    if (app.scope.workOrder) {     // the number next to the product; "work order" is in its tooltip (room on the TV)
        const latest = app.scope.workOrder === app.options?.latest_work_order ? t('filter.latest') : '';
        parts.push(`${app.scope.workOrder}${latest}`);
    }
    const { from, to } = app.scope.range === 'custom' ? app.scope : presetRange(app.scope.range);
    if (from || to) parts.push(from === to ? from : `${from || '…'} ~ ${to || '…'}`);
    const text = parts.join(t('common.dot'));
    if (text) {
        const span = document.createElement('span');
        span.className = 'scope-rest';
        span.textContent = text;
        span.title = app.scope.workOrder ? `${t('filter.woAria')} ${text}` : text;
        el.appendChild(span);
        // squeezed to a few pixels by a long language on one row: leave it out (the number is in the work-order list)
        requestAnimationFrame(() => { if (span.scrollWidth > span.clientWidth && span.clientWidth < 56) span.hidden = true; });
    }
}

function renderRange() {
    $$('#rangeSeg button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.range === app.scope.range)));
    $('#customRange').hidden = app.scope.range !== 'custom';
}

function renderWoOptions() {
    const sel = $('#woSelect');
    sel.innerHTML = '';
    sel.appendChild(new Option(t('filter.allWo'), ''));
    const list = app.scope.product
        ? app.options.work_orders.filter(w => w.product_model === app.scope.product)
        : app.options.work_orders;
    list.forEach(w => {
        const latest = w.work_order === app.options.latest_work_order ? t('filter.latest') : '';
        // the product in each option unless a product is already chosen
        const label = t(app.scope.product ? 'filter.woOption' : 'filter.woOptionProduct', { wo: w.work_order, latest,
            product: w.product_model || '—', from: fmtShortDate(w.first_date), to: fmtShortDate(w.last_date) });
        sel.appendChild(new Option(label, w.work_order));
    });
    sel.value = app.scope.workOrder;
    if (sel.value !== app.scope.workOrder) { app.scope.workOrder = ''; sel.value = ''; }
}

function renderProductOptions() {
    const sel = $('#productSelect');
    sel.innerHTML = '';
    sel.appendChild(new Option(t('filter.allProducts'), ''));
    app.options.products.forEach(p => sel.appendChild(new Option(p.product_model, p.product_model)));
    sel.value = app.scope.product;
    if (sel.value !== app.scope.product) { app.scope.product = ''; sel.value = ''; }
}

function scopeChanged() {
    renderRange();
    updateScopeLabel();
    loadPage();
}

function initFilters() {
    $$('#rangeSeg button').forEach(b => b.addEventListener('click', () => {
        app.scope.range = b.dataset.range;
        if (b.dataset.range === 'custom' && !app.scope.from) {
            const latest = app.options.work_orders[0];
            app.scope.from = latest ? fmtDate(latest.first_date) : fmtDate(new Date());
            app.scope.to = latest ? fmtDate(latest.last_date) : fmtDate(new Date());
            $('#dateFrom').value = app.scope.from;
            $('#dateTo').value = app.scope.to;
        }
        scopeChanged();
    }));
    for (const [id, key] of [['#dateFrom', 'from'], ['#dateTo', 'to']]) {
        $(id).addEventListener('change', e => { app.scope[key] = e.target.value; scopeChanged(); });
    }
    $('#woSelect').addEventListener('change', e => { app.scope.workOrder = e.target.value; scopeChanged(); });
    $('#productSelect').addEventListener('change', e => {
        app.scope.product = e.target.value;
        renderWoOptions();            // the work-order list follows the product
        scopeChanged();
    });
}

async function loadOptions() {
    try {
        app.options = await apiGet('/api/filter-options');
    } catch {
        app.options = app.options || { work_orders: [], products: [], latest_work_order: null };
    }
    renderProductOptions();
    renderWoOptions();
}

/* ─── Freshness, status lights ─── */
async function loadMeta() {
    try {
        const meta = await apiGet('/api/meta');
        // New data, or settings changed elsewhere (thresholds, status rules): the visible page reloads.
        const changed = app.meta && (app.meta.stats_refreshed_at !== meta.stats_refreshed_at
            || app.meta.settings_version !== meta.settings_version);
        app.meta = meta;
        setThresholds(meta.thresholds);
        renderFreshness();
        setLlm(meta.llm?.connected);
        return changed;
    } catch {
        return false;
    }
}

function renderFreshness() {
    const at = app.meta?.stats_refreshed_at;
    const el = $('#freshText');      // short in the top bar; the full date and time in the tooltip
    el.textContent = at ? t('fresh.short', { time: fmtTime(at) }) : t('fresh.unknown');
    el.title = at ? t('fresh.label', { time: `${fmtShortDate(at)} ${fmtTime(at)}` }) : '';
}

const lights = { api: null, llm: null };

function renderLights() {
    if (lights.api !== null) $('#apiText').textContent = lights.api ? t('status.apiOk') : t('status.apiErr');
    if (lights.llm !== null) $('#llmText').textContent = lights.llm ? t('status.llmOk') : t('status.llmErr');
}

function setLlm(ok) {
    lights.llm = !!ok;
    $('#llmDot').className = `dot ${ok ? 'ok' : 'err'}`;
    renderLights();
    report.setLlm(!!ok);
}

function initStatus() {
    onApiStatus(ok => {
        lights.api = !!ok;
        $('#apiDot').className = `dot ${ok ? 'ok' : 'err'}`;
        renderLights();
    });
    $('#btnRefresh').addEventListener('click', async () => {
        const btn = $('#btnRefresh');
        btn.disabled = true;
        btn.title = t('fresh.refreshing');
        try {
            await apiPost('/api/stats/refresh');
            await Promise.all([loadMeta(), loadOptions()]);
            await loadPage();
            toast(t('fresh.done'));
        } catch (err) {
            toast(err.message, 'bad');
        } finally {
            btn.disabled = false;
            btn.title = t('fresh.refresh');
        }
    });
    // New data (scheduled view refresh, ingest): reload the visible page quietly. A live page (Today) also
    // reloads every two minutes without new data: "last hour" and "idle" move with the clock.
    let ticks = 0;
    setInterval(async () => {
        ticks += 1;
        const changed = await loadMeta();
        if (changed) await loadOptions();
        if (busy()) return;
        if ((changed && (app.page?.scoped || app.page?.live)) || (app.page?.live && ticks % 2 === 0)) loadPage();
    }, 60 * 1000);
}

/* ─── Theme, clock ─── */
function currentTheme() { return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'; }

function renderThemeButton() {
    $('#btnTheme').textContent = currentTheme() === 'dark' ? t('theme.toLight') : t('theme.toDark');
}

function initTheme() {
    renderThemeButton();
    $('#btnTheme').addEventListener('click', () => {
        const next = currentTheme() === 'dark' ? 'light' : 'dark';
        document.documentElement.dataset.theme = next;
        try { localStorage.setItem('wifi-theme', next); } catch { /* storage blocked */ }
        renderThemeButton();
        charts.rethemeAll();
    });
}

function tickClock() {
    const now = new Date();
    $('#clockTime').textContent = now.toTimeString().slice(0, 5);
    $('#clockDate').textContent = fmtClockDate(now, getLang());
}

function initClock() {
    tickClock();
    setInterval(tickClock, 10 * 1000);
}

/* ─── Language: English / 中文 (default: ?lang=, then the saved choice, then the browser); switching keeps the page, filters and sign-in ─── */
function initLanguage() {
    $('#btnLang').addEventListener('click', () => setLang(getLang() === 'en' ? 'zh' : 'en'));
    onLangChange(() => {
        applyI18n();
        renderThemeButton();
        tickClock();
        renderFreshness();
        renderLights();
        renderProductOptions();
        renderWoOptions();
        renderAuthBox(auth.currentUser());
        if (app.page) {
            $('#pageTitle').textContent = t(`page.${app.page.id}`);
            document.title = `${t(`page.${app.page.id}`)} · ${t('app.title')}`;
        }
        updateScopeLabel();
        report.onLangChange();
        alerts.onLangChange();
        askbox.onLangChange();
        PAGES.forEach(p => p.onLangChange?.(app));
        if (app.page && !app.page.isBusy?.()) loadPage();
    });
}

/* ─── Signed-in user box (one sign-in for Log Upload, products, DB Maintenance, accounts) ─── */
function renderAuthBox(user) {
    $('#authBox').hidden = !user;
    if (user) {
        $('#authName').textContent = t('auth.user', { name: auth.displayName(user) });
        $('#authName').title = auth.displayName(user);
        $('#authRole').textContent = auth.isAdmin() ? t('auth.admin') : auth.isRd() ? t('auth.rd') : t('auth.uploader');
        $('#btnChangePw').hidden = auth.isAdmin();   // the administrator password lives in the server environment
    }
    // Administrator-only pages stay reachable by address; the menu just does not advertise them.
    $$('.nav-item[data-admin-only]').forEach(a => { a.hidden = !auth.isAdmin(); });
    $$('.nav-item[data-rd-only]').forEach(a => { a.hidden = !(auth.isAdmin() || auth.isRd()); });
}

function renderAuth(user, reason) {
    renderAuthBox(user);
    if (reason === 'expired') toast(t('auth.expired'), 'bad');
    PAGES.forEach(p => p.onAuthChange?.(user, reason, app));
}

/* ─── Idle rotation (at once with ?tv=1, else after 5 idle minutes): every 30 s the next of
       latest work order · Trend → latest · Forecast → all work orders · Trend → all · Forecast ─── */
const idle = { last: Date.now() };

function rotationStep() {
    // where the screen is now in ROTATION (-1: somewhere else, e.g. a product or another work order was chosen)
    const here = location.hash.replace(/^#/, '') || 'overview';
    const wo = !app.scope.workOrder && !app.scope.product ? 'all'
        : app.scope.workOrder === app.latestWorkOrder() && !app.scope.product ? 'latest' : '';
    return ROTATION.findIndex(s => s.hash === here && s.wo === wo);
}

function busy() {
    return !$('#reportModal').hidden || !$('#formModal').hidden || askbox.isOpen() || PAGES.some(p => p.isBusy?.());
}

function initIdleRotation() {
    const touch = () => { idle.last = Date.now(); };
    ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart'].forEach(ev =>
        window.addEventListener(ev, touch, { passive: true }));
    setInterval(() => {
        if (Date.now() - idle.last < IDLE_MS || busy() || document.hidden) return;
        if (app.page?.id === 'wo') return;   // a work-order detail page is being read: leave it where it is
        const next = ROTATION[(rotationStep() + 1) % ROTATION.length];
        const wo = next.wo === 'latest' ? app.latestWorkOrder() : '';
        if (app.scope.workOrder !== wo || app.scope.product) {
            app.scope.product = '';
            app.scope.workOrder = wo;
            renderProductOptions();
            renderWoOptions();
            renderRange();
            updateScopeLabel();
        }
        if (location.hash !== `#${next.hash}`) location.hash = next.hash;
        else loadPage();
    }, ROTATE_MS);
}

/* ─── Start ─── */
async function start() {
    initLang();
    applyI18n();
    initFormModal();
    initEscapeClose([['formModal', closeFormModal], ['failModal', closeFailDetail], ['askModal', askbox.close], ['reportModal', () => report.close()]]);
    charts.applyDefaults();
    initTheme();
    initLanguage();
    initClock();
    initStatus();
    initFilters();
    renderRange();
    report.init(app);
    askbox.init(app);
    alerts.init(app);
    PAGES.forEach(p => p.init?.(app));
    auth.onAuthChange(renderAuth);
    $('#btnLogout').addEventListener('click', auth.logout);
    $('#btnChangePw').addEventListener('click', auth.openChangePassword);
    window.addEventListener('hashchange', () => show(pageFromHash()));

    await Promise.all([loadMeta(), loadOptions(), auth.restore()]);
    app.scope.workOrder = app.latestWorkOrder();     // default view: the latest work order
    renderWoOptions();
    initIdleRotation();
    await show(pageFromHash());
}

// Chart.js is a deferred classic script; module scripts run after it.
start();
