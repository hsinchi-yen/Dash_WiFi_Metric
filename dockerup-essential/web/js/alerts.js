// The anomaly banner under the top bar (5E, L8; Q22: on the dashboard only). GET /api/alerts every minute; rules
// only. Each item links to its work order or the Fail List; × hides the banner until the set of alerts changes.
// ?replay=2026-01-13T11:00 in the page address shows the banner as it was at that moment (review a past day).
import { apiGet } from './core/api.js';
import { esc, fmtDateTime, fmtInt, fmtNum } from './core/format.js';
import { localized, t } from './core/i18n.js';
import { detailHref } from './core/scope.js';
import { $ } from './core/ui.js';

const KEY = 'wifi-alerts-dismissed';
let last = null;
let appRef;

function signature(d) {
    return JSON.stringify(d.items.map(i => [i.code, i.values.wo || '', i.values.category || '', i.values.n || 0]));
}

function text(i) {
    const v = i.values;
    const vars = { ...v, yield_pct: fmtNum(v.yield_pct, 2), name: localized(v.name_zh, v.name_en) || v.category || '',
                   band: v.band === '24g' ? '2.4G' : '5G', n: fmtInt(v.n), units: fmtInt(v.units) };
    return t(`al.${i.code}`, vars);
}

function render() {
    const bar = $('#alertBar');
    const d = last;
    let dismissed = null;
    try { dismissed = sessionStorage.getItem(KEY); } catch { /* storage blocked */ }
    if (!d || !d.items.length || dismissed === signature(d)) { bar.hidden = true; return; }
    bar.hidden = false;
    bar.className = `alert-bar lv-${d.level}`;
    const items = d.items.slice(0, 6).map((i, k) => {
        const href = i.link?.fails ? '#fails' : i.link?.wo ? detailHref(i.link.wo, i.link.pm) : '';
        return `<a class="alert-item lv-${esc(i.level)}" href="${esc(href || '#overview')}" data-alert="${k}">${esc(text(i))}</a>`;
    }).join('');
    const more = d.items.length > 6 ? `<span class="muted">${esc(t('al.more', { n: d.items.length - 6 }))}</span>` : '';
    const title = REPLAY ? t('al.replay', { time: fmtDateTime(d.now) }) : t('al.title', { h: d.hours });
    bar.innerHTML = `<span class="alert-title">${esc(title)}</span>${items}${more}
        <button class="icon-btn" type="button" id="btnAlertClose" aria-label="${esc(t('common.close'))}">×</button>`;
}

const REPLAY = (() => {
    const v = new URLSearchParams(location.search).get('replay') || '';
    return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(v) ? v : '';
})();

export async function refresh() {
    try {
        last = await apiGet(`/api/alerts${REPLAY ? `?now=${encodeURIComponent(REPLAY)}` : ''}`);
        render();
    } catch { /* the banner just stays as it was */ }
}

export function init(app) {
    appRef = app;
    $('#alertBar').addEventListener('click', e => {
        if (e.target.closest('#btnAlertClose')) {
            try { sessionStorage.setItem(KEY, signature(last)); } catch { /* storage blocked */ }
            render();
            return;
        }
        const a = e.target.closest('[data-alert]');
        const item = a && last?.items[Number(a.dataset.alert)];
        if (item?.link?.fails && item.link.wo) appRef.setScope({ range: 'all', product: item.link.pm || '', workOrder: item.link.wo });
    });
    refresh();
    setInterval(refresh, 60 * 1000);
}

export function onLangChange() { render(); }
