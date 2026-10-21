// Fail List: FAIL units (latest test per SN), paged on the server, with search and log download.
import { apiGet, download } from '../core/api.js';
import { esc, fmtDateTime, fmtInt, fmtNum, fmtSigned } from '../core/format.js';
import { joinList, localized, t } from '../core/i18n.js';
import { detailHref, queryString } from '../core/scope.js';
import { $, debounce, tableState, toast } from '../core/ui.js';

const PAGE_SIZE = 50;
const COLS = 13;
const state = { page: 1, key: '' };
let seq = 0;
let appRef;

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
    const title = t(`fails.retest.${a.retest_outcome || 'not_retested'}`);
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

async function load(app = appRef) {
    const key = app.scopeKey();
    if (key !== state.key) { state.key = key; state.page = 1; }
    const mine = ++seq;
    const tbody = $('#failRows');
    const params = { ...app.params(), page: state.page, page_size: PAGE_SIZE };
    const search = $('#failSearch').value.trim();
    if (search) params.search = search;
    try {
        const data = await apiGet(`/api/fail-list${queryString(params)}`);
        if (mine !== seq) return;
        state.page = data.page;
        $('#failCount').textContent = t('fails.count', { n: fmtInt(data.total) });
        if (!data.items.length) tableState(tbody, COLS, 'empty');
        else {
            tbody.innerHTML = data.items.map(r => `<tr>
                <td class="nowrap">${esc(fmtDateTime(r.test_time))}</td>
                <td class="nowrap">${r.work_order ? `<a class="wo-link" href="${esc(detailHref(r.work_order, r.product_model))}">${esc(r.work_order)}</a>` : '—'}</td>
                <td class="nowrap">${esc(r.product_model || '—')}</td>
                <td><code>${esc(r.sn)}</code></td>
                <td><code>${esc(r.wifi_mac || '—')}</code></td>
                <td class="nowrap">${categoryBadge(r.analysis, r.fail_reason)}</td>
                <td class="nowrap">${runSeqHtml(r.analysis)}</td>
                <td class="nowrap small-text">${esc(stationText(r.analysis))}</td>
                <td class="num">${fmtNum(r.avg_24g, 1)}</td>
                <td class="num">${fmtNum(r.avg_5g, 1)}</td>
                <td class="num">${fmtSigned(r.rssi_delta_5g, 1)}</td>
                <td class="num">${fmtInt(r.sn_runs)}</td>
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

function init(app) {
    appRef = app;
    $('#failSearch').addEventListener('input', debounce(() => { state.page = 1; load(); }, 300));
    $('#failRows').addEventListener('click', async e => {
        const btn = e.target.closest('[data-log]');
        if (!btn) return;
        try { await download(`/api/raw-log/${btn.dataset.log}`, `record_${btn.dataset.log}.txt`); }
        catch (err) { toast(err.message, 'bad'); }
    });
}

export default { id: 'fails', scoped: true, init, load: app => load(app) };
