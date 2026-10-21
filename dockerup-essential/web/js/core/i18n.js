// UI language: English or 繁體中文 (default follows the browser; ?lang= wins). Every user-visible string lives in
// strings.en.js / strings.zh.js (same keys; a unit test checks them).
//   t('key', {name: value})  in JS;  data-i18n="key" (text), data-i18n-placeholder,
//   data-i18n-title, data-i18n-aria  in HTML (applyI18n() re-applies them on a switch).
// Server messages carry both languages: {error, error_en} / {message, message_en}; pick one with localized().
import en from './strings.en.js';
import zh from './strings.zh.js';

export const STRINGS = { en, zh };
export const LANGS = ['en', 'zh'];
const STORAGE_KEY = 'wifi-lang';
const HTML_LANG = { en: 'en', zh: 'zh-Hant-TW' };

let lang = 'en';
const listeners = new Set();

export function getLang() { return lang; }

/** ?lang=en|zh in the address wins (the line TV's fixed address, Q6), then this browser's last choice,
 *  then the browser's own language (Chinese → 中文), else English. */
export function initialLang(search = '', stored = null, browserLangs = []) {
    const fromUrl = new URLSearchParams(search).get('lang');
    if (LANGS.includes(fromUrl)) return fromUrl;
    if (LANGS.includes(stored)) return stored;
    return String(browserLangs[0] || '').toLowerCase().startsWith('zh') ? 'zh' : 'en';
}

/** This browser's choice (address, then saved, then the browser language). */
export function preferredLang() {
    let stored = null;
    try { stored = localStorage.getItem(STORAGE_KEY); } catch { /* storage blocked */ }
    const browser = typeof navigator !== 'undefined' ? (navigator.languages?.length ? navigator.languages : [navigator.language]) : [];
    return initialLang(location.search, stored, browser);
}

export function initLang() {
    setLang(preferredLang(), { save: false, notify: false });
}

export function setLang(next, { save = true, notify = true } = {}) {
    if (!LANGS.includes(next)) return;
    lang = next;
    if (typeof document !== 'undefined') document.documentElement.lang = HTML_LANG[lang];
    if (save) { try { localStorage.setItem(STORAGE_KEY, lang); } catch { /* storage blocked */ } }
    if (notify) listeners.forEach(fn => fn(lang));
}

export function onLangChange(fn) { listeners.add(fn); }

export function t(key, vars) {
    return tLang(lang, key, vars);
}

/** t() in a given language (e.g. text copied from the report in the language it is shown in). */
export function tLang(language, key, vars) {
    let s = (STRINGS[language] || STRINGS.en)[key];
    if (s === undefined) s = STRINGS.zh[key];
    if (s === undefined) {
        console.warn('missing string', key);
        return key;
    }
    if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (vars[k] !== undefined ? String(vars[k]) : m));
    return s;
}

/** A server message in the UI language: the English text when there is one and English is on. */
export function localized(zhText, enText) {
    return lang === 'en' && enText ? enText : zhText;
}

/** Joins a list with the language's separator (、 / ,). */
export function joinList(items) {
    return items.join(t('common.listSep'));
}

export function applyI18n(root = document) {
    root.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
    root.querySelectorAll('[data-i18n-placeholder]').forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
    root.querySelectorAll('[data-i18n-title]').forEach(el => { el.title = t(el.dataset.i18nTitle); });
    root.querySelectorAll('[data-i18n-aria]').forEach(el => { el.setAttribute('aria-label', t(el.dataset.i18nAria)); });
}
