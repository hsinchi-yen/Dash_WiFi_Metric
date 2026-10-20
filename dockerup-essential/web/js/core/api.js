// HTTP helpers: JSON calls with one error shape, NDJSON streams, authenticated downloads.
// Every response updates the sidebar's API status light. Server messages come in both
// languages ({error, error_en}); errors carry the one for the current UI language.
import { localized, t } from './i18n.js';

let token = null;
let onUnauthorized = () => {};
const statusListeners = new Set();

export function setToken(value) { token = value || null; }
export function getToken() { return token; }
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }
export function onApiStatus(fn) { statusListeners.add(fn); }

function report(ok, detail) {
    statusListeners.forEach(fn => { try { fn(ok, detail); } catch { /* listener errors never break a request */ } });
}

export class ApiError extends Error {
    constructor(message, status = 0, code = 'error', data = {}) {
        super(message);
        this.status = status;
        this.code = code;
        this.data = data;
    }
}

/** fetch with the session token; 401 signs the browser out. Throws ApiError on network failure. */
export async function request(path, { method = 'GET', body, headers = {}, signal, raw = false } = {}) {
    const h = { ...headers };
    if (token) h['X-Tweak-Token'] = token;
    let payload = body;
    if (body !== undefined && !(body instanceof FormData) && typeof body !== 'string') {
        h['Content-Type'] = 'application/json';
        payload = JSON.stringify(body);
    }
    let res;
    try {
        res = await fetch(path, { method, headers: h, body: payload, signal });
    } catch (err) {
        if (err.name === 'AbortError') throw err;
        report(false, 'network');
        throw new ApiError(t('err.network'), 0, 'network');
    }
    report(res.status < 500, res.status);
    if (res.status === 401 && token) onUnauthorized();
    if (raw) return res;
    if (!res.ok) throw await toError(res);
    if (res.status === 204) return null;
    return res.json();
}

/** The message of an error body ({error, error_en}) in the UI language. */
export function errorText(data, status) {
    return localized(data?.error, data?.error_en) || t('err.http', { status });
}

/** The message of an NDJSON event ({message, message_en}) in the UI language. */
export function eventText(ev) {
    return localized(ev?.message, ev?.message_en);
}

export async function toError(res) {
    let data = {};
    try { data = await res.json(); } catch { /* not JSON */ }
    return new ApiError(errorText(data, res.status), res.status, data.code || 'error', data);
}

export const apiGet = (path, opts) => request(path, { ...opts, method: 'GET' });
export const apiPost = (path, body, opts) => request(path, { ...opts, method: 'POST', body });
export const apiPatch = (path, body, opts) => request(path, { ...opts, method: 'PATCH', body });
export const apiDelete = (path, opts) => request(path, { ...opts, method: 'DELETE' });

/** Read an NDJSON response, calling onEvent(obj) per line. Returns when the stream ends. */
export async function readNdjson(res, onEvent) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf('\n')) >= 0) {
            const line = buf.slice(0, nl).trim();
            buf = buf.slice(nl + 1);
            if (line) onEvent(JSON.parse(line));
        }
    }
    if (buf.trim()) onEvent(JSON.parse(buf));
}

/** POST and stream NDJSON events. Throws ApiError for non-2xx answers (before streaming). */
export async function streamPost(path, body, onEvent, opts = {}) {
    const res = await request(path, { ...opts, method: 'POST', body, raw: true });
    if (!res.ok) throw await toError(res);
    await readNdjson(res, onEvent);
}

/** Download a file through fetch (so the session token stays in a header, not the URL). */
export async function download(path, fallbackName = 'download.txt') {
    const res = await request(path, { raw: true });
    if (!res.ok) throw await toError(res);
    const blob = await res.blob();
    const disposition = res.headers.get('Content-Disposition') || '';
    const match = /filename="?([^";]+)"?/.exec(disposition);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = match ? match[1] : fallbackName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}
