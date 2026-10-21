// Reports (5E, L5): weekly and monthly reports — the facts by rules and a short summary (the LLM writes it once per
// report, numbers checked). The schedule writes the last finished week and month; the administrator can write any
// other one. Download as Markdown (中文 / English) or print.
import { apiGet, apiPost, download } from '../core/api.js';
import * as auth from '../core/auth.js';
import { esc, fmtDateTime, fmtInt, fmtNum, fmtShortDate, fmtSigned, yieldLevel } from '../core/format.js';
import { getLang, localized, t } from '../core/i18n.js';
import { detailHref } from '../core/scope.js';
import { $, tableState, toast } from '../core/ui.js';

const st = { items: [], current: null };
const LIGHT = { bad: '🔴', warn: '🟡', good: '🟢' };

function kindName(k) { return t(k === 'week' ? 'rep.week' : 'rep.month'); }

function renderList() {
    const tbody = $('#repRows');
    if (!st.items.length) { tableState(tbody, 6, 'empty', t('rep.none')); return; }
    tbody.innerHTML = st.items.map((r, i) => `<tr class="${st.current && st.current.kind === r.kind && st.current.period_start === r.period_start ? 'rd-picked' : ''}">
        <td class="nowrap"><button class="link-btn" type="button" data-open="${i}">${esc(kindName(r.kind))} ${esc(r.period_start.slice(0, 4))} ${esc(fmtShortDate(r.period_start))}–${esc(fmtShortDate(r.period_end))}</button></td>
        <td class="num">${fmtInt(r.units)}</td><td class="num ${r.yield_pct != null ? `v-${yieldLevel(r.yield_pct)}` : ''}">${fmtNum(r.yield_pct, 2)}</td>
        <td class="num">${fmtInt(r.attention)}</td><td class="small-text nowrap">${esc(r.source === 'llm' ? t('wo.ai') : t('rep.byRule'))}</td>
        <td class="nowrap small-text muted">${esc(fmtDateTime(r.generated_at))}</td></tr>`).join('');
}

function renderReport() {
    const r = st.current;
    const box = $('#repView');
    if (!r) { box.hidden = true; return; }
    box.hidden = false;
    const f = r.facts;
    const tt = f.totals, p = f.previous;
    $('#repTitle').textContent = `${kindName(f.kind)} ${f.from} ~ ${f.to}`;
    $('#repMeta').textContent = t('rep.meta', { time: fmtDateTime(r.generated_at), by: r.source === 'llm' ? t('rep.byAi') : t('rep.byRule') });
    const summary = (getLang() === 'zh' ? r.summary?.zh : r.summary?.en) || '';
    const row = (label, a, b, digits = 0) => `<tr><th>${esc(label)}</th><td class="num">${digits ? fmtNum(a, digits) : fmtInt(a)}</td>
        <td class="num">${digits ? fmtNum(b, digits) : fmtInt(b)}</td>
        <td class="num">${a != null && b != null ? esc(fmtSigned(a - b, digits)) : '—'}</td></tr>`;
    $('#repBody').innerHTML = `
        <p class="rep-summary">${r.source === 'llm' ? `<span class="ai-tag">${esc(t('wo.ai'))}</span>` : ''}${esc(summary)}</p>
        <div class="grid cols-2">
            <div><div class="card-title mb-s">${esc(t('rep.numbers'))}</div>
                <div class="table-wrap"><table><thead><tr><th></th><th class="num">${esc(t('rep.this'))}</th><th class="num">${esc(t('rep.prev', { from: f.previous_from, to: f.previous_to }))}</th><th class="num">${esc(t('rep.change'))}</th></tr></thead><tbody>
                ${row(t('rep.units'), tt.units, p.units)}${row(t('rep.yield'), tt.yield_pct, p.yield_pct, 2)}${row(t('rep.fail'), tt.fail, p.fail)}
                ${row(t('rep.retry'), tt.retry_rate, p.retry_rate, 2)}${row('2.4G Mbps', tt.avg_24g, p.avg_24g, 1)}${row('5G Mbps', tt.avg_5g, p.avg_5g, 1)}
                ${row(t('rep.wos'), tt.work_orders, p.work_orders)}${row(t('rep.days'), tt.test_days, p.test_days)}</tbody></table></div></div>
            <div><div class="card-title mb-s">${esc(t('rep.fails'))}</div>
                ${f.fail_categories.length ? `<div class="table-wrap"><table><thead><tr><th>${esc(t('fails.col.category'))}</th><th class="num">${esc(t('rd.runs'))}</th>
                    <th class="num">${esc(t('wd.col.units'))}</th><th class="num">${esc(t('fails.retest.pass_on_retest'))}</th></tr></thead><tbody>
                    ${f.fail_categories.map(c => `<tr><td>${esc(localized(c.name_zh, c.name_en) || c.category)}</td><td class="num">${fmtInt(c.runs)}</td>
                        <td class="num">${fmtInt(c.units)}</td><td class="num">${fmtInt(c.passed_retest)}</td></tr>`).join('')}</tbody></table></div>`
                    : `<p class="muted">${esc(t('rep.noFails'))}</p>`}
                <p class="muted small-text mt-s">${esc(t('rep.still', { n: fmtInt(f.still_failing), handled: fmtInt(f.handled) }))}</p></div>
        </div>
        <div class="card-title mb-s mt">${esc(t('rep.wosTitle', { n: f.work_orders.length }))}</div>
        <div class="table-wrap"><table><thead><tr><th></th><th>${esc(t('wo.col.wo'))}</th><th>${esc(t('wo.col.product'))}</th><th class="num">${esc(t('rep.units'))}</th>
            <th class="num">${esc(t('rep.yield'))}</th><th>${esc(t('rep.period'))}</th></tr></thead><tbody>
            ${f.work_orders.map(w => `<tr><td>${LIGHT[w.light] || ''}</td><td><a class="wo-link" href="${esc(detailHref(w.work_order, w.product_model))}">${esc(w.work_order)}</a></td>
                <td>${esc(w.product_model || '—')}</td><td class="num">${fmtInt(w.units)}</td><td class="num">${fmtNum(w.yield_pct, 2)}</td>
                <td class="small-text muted nowrap">${esc(w.first_test)} ~ ${esc(w.last_test)}</td></tr>`).join('')}</tbody></table></div>`;
    $('#btnRepRegen').hidden = !auth.isAdmin();
}

async function openReport(item) {
    try {
        st.current = await apiGet(`/api/period-reports/${item.kind}/${item.period_start}`);
        renderList();
        renderReport();
    } catch (err) { toast(err.message, 'bad'); }
}

async function loadList() {
    try {
        st.items = (await apiGet('/api/period-reports')).items;
        renderList();
        if (!st.current && st.items.length) openReport(st.items.find(x => x.units) || st.items[0]);   // the newest with tests
    } catch (err) {
        tableState($('#repRows'), 6, 'error', t('common.loadFailedMsg', { error: err.message }), loadList);
    }
}

async function generate(kind, start, useLlm = true) {
    const btn = $('#btnRepGenerate');
    btn.disabled = true;
    $('#repGenMsg').textContent = t('rep.working');
    try {
        st.current = await apiPost('/api/period-reports/generate', { kind, start, use_llm: useLlm });
        $('#repGenMsg').textContent = '';
        await loadList();
        renderReport();
    } catch (err) {
        $('#repGenMsg').textContent = err.message;
    } finally {
        btn.disabled = false;
    }
}

function renderAccess() {
    $('#repAdmin').hidden = !auth.isAdmin();
}

function init() {
    $('#repRows').addEventListener('click', e => { const b = e.target.closest('[data-open]'); if (b) openReport(st.items[Number(b.dataset.open)]); });
    $('#btnRepMdZh').addEventListener('click', () => st.current && download(`/api/period-reports/${st.current.kind}/${st.current.period_start}.md?lang=zh`, 'report_zh.md').catch(err => toast(err.message, 'bad')));
    $('#btnRepMdEn').addEventListener('click', () => st.current && download(`/api/period-reports/${st.current.kind}/${st.current.period_start}.md?lang=en`, 'report_en.md').catch(err => toast(err.message, 'bad')));
    $('#btnRepPrint').addEventListener('click', () => window.print());
    $('#btnRepRegen').addEventListener('click', () => st.current && generate(st.current.kind, st.current.period_start));
    $('#btnRepGenerate').addEventListener('click', () => {
        const d = $('#repGenDate').value;
        if (!d) { $('#repGenMsg').textContent = t('rep.pickDate'); return; }
        generate($('#repGenKind').value, d);
    });
}

async function load() {
    renderAccess();
    await loadList();
}

export default {
    id: 'reports', scoped: false, init, load,
    onAuthChange: () => { renderAccess(); if (st.current) renderReport(); },
    onLangChange: () => { renderList(); renderReport(); },
};
