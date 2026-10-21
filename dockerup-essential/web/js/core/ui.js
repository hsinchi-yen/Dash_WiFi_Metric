// Small DOM helpers shared by every page: state messages, form dialog, toast, debounce.
import { joinList, t } from './i18n.js';

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function debounce(fn, ms) {
    let id;
    return (...args) => { clearTimeout(id); id = setTimeout(() => fn(...args), ms); };
}

/** Replace an element's content with a loading / empty / error message (error offers 重試). */
export function setState(el, kind, message, onRetry) {
    if (!el) return;
    el.innerHTML = '';
    const box = document.createElement('div');
    box.className = `state-msg ${kind === 'error' ? 'error' : ''}`;
    const text = document.createElement('div');
    text.textContent = message || (kind === 'loading' ? t('common.loading') : kind === 'empty' ? t('common.empty') : t('common.loadFailed'));
    box.appendChild(text);
    if (kind === 'error' && onRetry) {
        const btn = document.createElement('button');
        btn.className = 'btn small';
        btn.textContent = t('common.retry');
        btn.addEventListener('click', onRetry);
        box.appendChild(btn);
    }
    el.appendChild(box);
}

/** One table row spanning all columns, for loading / empty / error states inside <tbody>. */
export function tableState(tbody, colspan, kind, message, onRetry) {
    tbody.innerHTML = '';
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = colspan;
    setState(td, kind, message, onRetry);
    tr.appendChild(td);
    tbody.appendChild(tr);
}

let toastTimer;
export function toast(message, kind = '') {
    let el = $('#toast');
    if (!el) {
        el = document.createElement('div');
        el.id = 'toast';
        el.setAttribute('role', 'status');
        document.body.appendChild(el);
    }
    el.className = `toast ${kind}`;
    el.textContent = message;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 3500);
}

/* ─── Form dialog ───────────────────────────────────────────────────────────
   openFormModal({ title, desc, fields: [{ name, label, type, value, required,
   autocomplete, hint, maxlength, options: [{ value, label }] (type 'select') }], submitLabel, danger, onSubmit(values) → error text | null }) */
const formState = { onSubmit: null, lastFocus: null };

export function openFormModal({ title, desc = '', fields, submitLabel, onSubmit, danger = false }) {
    formState.onSubmit = onSubmit;
    formState.lastFocus = document.activeElement;
    $('#formModalTitle').textContent = title;
    const descEl = $('#formModalDesc');
    descEl.textContent = desc;
    descEl.hidden = !desc;
    const box = $('#formModalFields');
    box.innerHTML = '';
    for (const f of fields) {
        const row = document.createElement('div');
        row.className = 'field';
        const label = document.createElement('label');
        label.htmlFor = `fm_${f.name}`;
        label.textContent = f.label + (f.required ? ' *' : '');
        label.dataset.required = f.required ? '1' : '';
        let input;
        if (f.type === 'select') {
            input = document.createElement('select');
            Object.assign(input, { id: `fm_${f.name}`, name: f.name, className: 'select' });
            (f.options || []).forEach(o => input.appendChild(new Option(o.label, o.value)));
            input.value = f.value ?? (f.options?.[0]?.value || '');
        } else {
            input = document.createElement('input');
            Object.assign(input, {
                id: `fm_${f.name}`, name: f.name, type: f.type || 'text', value: f.value ?? '',
                autocomplete: f.autocomplete || 'off', spellcheck: false, className: 'input',
            });
        }
        if (f.maxlength) input.maxLength = f.maxlength;
        row.append(label, input);
        if (f.hint) {
            const hint = document.createElement('div');
            hint.className = 'field-hint';
            hint.textContent = f.hint;
            row.appendChild(hint);
        }
        box.appendChild(row);
    }
    $('#formModalMsg').textContent = '';
    const submit = $('#btnFormModalSubmit');
    submit.textContent = submitLabel || t('common.ok');
    submit.className = `btn ${danger ? 'danger' : 'primary'}`;
    submit.disabled = false;
    $('#formModal').hidden = false;
    box.querySelector('input')?.focus();
}

export function closeFormModal() {
    $('#formModal').hidden = true;
    formState.onSubmit = null;
    formState.lastFocus?.focus?.();
}

export function initFormModal() {
    $('#formModalForm').addEventListener('submit', async e => {
        e.preventDefault();
        if (!formState.onSubmit) return;
        const values = {};
        $$('#formModalFields input, #formModalFields select').forEach(input => { values[input.name] = input.value; });
        const missing = $$('#formModalFields label')
            .filter(l => l.dataset.required && !values[l.htmlFor.slice(3)].trim())
            .map(l => l.textContent.replace(/ \*$/, ''));
        const msg = $('#formModalMsg');
        if (missing.length) { msg.textContent = t('common.required', { fields: joinList(missing) }); return; }
        const submit = $('#btnFormModalSubmit');
        submit.disabled = true;
        msg.textContent = '';
        try {
            const error = await formState.onSubmit(values);
            if (error) msg.textContent = error;
            else closeFormModal();
        } catch (err) {
            msg.textContent = err.message;
        } finally {
            submit.disabled = false;
        }
    });
    $('#btnFormModalCancel').addEventListener('click', closeFormModal);
    $('#formModal').addEventListener('mousedown', e => { if (e.target.id === 'formModal') closeFormModal(); });
}

/** Escape closes the top-most open modal. */
export function initEscapeClose(handlers) {
    document.addEventListener('keydown', e => {
        if (e.key !== 'Escape') return;
        for (const [id, close] of handlers) {
            if (!document.getElementById(id).hidden) { close(); break; }
        }
    });
}
