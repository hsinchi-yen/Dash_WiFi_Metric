// Number / date formatting and the shared quality thresholds. No DOM access (unit-tested in Node).

// Defaults match api/config.py; replaced from GET /api/meta at startup.
export const THRESHOLDS = { yield_good: 98, yield_warn: 95, retry_good: 3, retry_warn: 5 };

export function setThresholds(t) {
    if (t && typeof t === 'object') Object.assign(THRESHOLDS, t);
}

const isNum = v => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));

/** 'good' | 'warn' | 'bad' | '' — green ≥ 98, amber ≥ 95, red below (by default). */
export function yieldLevel(value) {
    if (!isNum(value)) return '';
    const v = Number(value);
    return v >= THRESHOLDS.yield_good ? 'good' : v >= THRESHOLDS.yield_warn ? 'warn' : 'bad';
}

/** Retry rate: lower is better — green ≤ 3, amber ≤ 5, red above (by default). */
export function retryLevel(value) {
    if (!isNum(value)) return '';
    const v = Number(value);
    return v <= THRESHOLDS.retry_good ? 'good' : v <= THRESHOLDS.retry_warn ? 'warn' : 'bad';
}

export function fmtInt(value) {
    return isNum(value) ? Math.round(Number(value)).toLocaleString('en-US') : '—';
}

export function fmtNum(value, digits = 2) {
    return isNum(value) ? Number(value).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—';
}

export function fmtPct(value, digits = 2) {
    return isNum(value) ? `${Number(value).toFixed(digits)}%` : '—';
}

export function fmtSigned(value, digits = 2) {
    if (!isNum(value)) return '—';
    const v = Number(value);
    return `${v > 0 ? '+' : ''}${v.toFixed(digits)}`;
}

const pad = n => String(n).padStart(2, '0');

/** Accepts ISO strings ('2026-08-21 16:38:31', '2026-08-21T08:38:31+00:00') or Date. */
export function toDate(value) {
    if (!value) return null;
    if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
    const s = String(value);
    const d = /^\d{4}-\d{2}-\d{2}$/.test(s) ? new Date(`${s}T00:00:00`) : new Date(s.replace(' ', 'T'));
    return Number.isNaN(d.getTime()) ? null : d;
}

export function fmtDate(value) {
    const d = toDate(value);
    return d ? `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` : '—';
}

export function fmtShortDate(value) {
    const d = toDate(value);
    return d ? `${pad(d.getMonth() + 1)}/${pad(d.getDate())}` : '—';
}

export function fmtTime(value) {
    const d = toDate(value);
    return d ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : '—';
}

/** Sidebar clock date, numeric in both languages: 2026/10/20（週二） or 2026/10/20 (Tue). */
export function fmtClockDate(value, lang = 'zh') {
    const d = toDate(value);
    if (!d) return '—';
    if (lang !== 'en') return d.toLocaleDateString('zh-TW', { year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short' });
    return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} (${d.toLocaleDateString('en-US', { weekday: 'short' })})`;
}

export function fmtDateTime(value) {
    const d = toDate(value);
    return d ? `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}` : '—';
}

/** Labels for a time series: hourly points show the date on the first hour of each day. */
export function bucketLabels(points, unit) {
    let lastDay = null;
    return points.map(p => {
        if (unit !== 'hour') return fmtShortDate(p.t);
        const day = fmtShortDate(p.t);
        const label = day === lastDay ? fmtTime(p.t) : `${day} ${fmtTime(p.t)}`;
        lastDay = day;
        return label;
    });
}

/** ISO date (local) for API parameters. */
export function isoDate(d) {
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** HTML-escape text for template strings (quotes too, so it is safe inside attributes). */
export function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}
