// The filter bar's state → API parameters. No DOM access (unit-tested in Node).
//
// range:      'all' | 'today' | 'week' | 'month' | 'custom'
// workOrder:  '' (all) or a work order; the dashboard starts on the latest work order
// product:    '' (all) or a product model (WiFi has this extra dimension)
// from / to:  ISO dates for 'custom' (both inclusive)
import { isoDate } from './format.js';

export const RANGES = ['all', 'today', 'week', 'month', 'custom'];

/** Inclusive date range for a preset, relative to `today` (local time). Weeks start on Monday. */
export function presetRange(range, today = new Date()) {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    if (range === 'today') return { from: isoDate(d), to: isoDate(d) };
    if (range === 'week') {
        const monday = new Date(d);
        monday.setDate(d.getDate() - ((d.getDay() + 6) % 7));
        const sunday = new Date(monday);
        sunday.setDate(monday.getDate() + 6);
        return { from: isoDate(monday), to: isoDate(sunday) };
    }
    if (range === 'month') {
        const first = new Date(d.getFullYear(), d.getMonth(), 1);
        const last = new Date(d.getFullYear(), d.getMonth() + 1, 0);
        return { from: isoDate(first), to: isoDate(last) };
    }
    return { from: '', to: '' };
}

/** {date_from, date_to, work_order, product_model} with empty values left out. */
export function scopeParams(state, today = new Date()) {
    const params = {};
    let { from, to } = state.range === 'custom' ? state : presetRange(state.range, today);
    if (from && to && from > to) [from, to] = [to, from];
    if (from) params.date_from = from;
    if (to) params.date_to = to;
    if (state.workOrder) params.work_order = state.workOrder;
    if (state.product) params.product_model = state.product;
    return params;
}

export function queryString(params) {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null)).toString();
    return qs ? `?${qs}` : '';
}

/** Stable key for caching page data per scope. */
export function scopeKey(state, today = new Date()) {
    return queryString(scopeParams(state, today));
}
