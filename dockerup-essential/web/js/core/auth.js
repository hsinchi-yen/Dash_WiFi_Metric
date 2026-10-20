// Sign-in for DB Maintenance. Interim (until the account system): the user's own PostgreSQL
// login, verified with POST /api/db-tweak/login and sent with every DB Tweak request. It is kept
// in sessionStorage: a reload stays signed in, closing the tab signs out.
// The account system will replace this file with session tokens and roles (admin / uploader).
import { apiPost } from './api.js';
import { t } from './i18n.js';

const STORE_KEY = 'wifi-dbtweak-v1';
const listeners = new Set();
let creds = null;   // { username, password }

export const currentUser = () => (creds ? { username: creds.username, role: 'admin' } : null);
export const isAdmin = () => !!creds;
export const isSignedIn = () => !!creds;
/** Body fields every DB Tweak request carries. */
export const credentials = () => (creds ? { username: creds.username, password: creds.password } : {});

export function onAuthChange(fn) { listeners.add(fn); }

function notify(reason) {
    const user = currentUser();
    listeners.forEach(fn => { try { fn(user, reason); } catch (err) { console.error(err); } });
}

function save() {
    try {
        if (creds) sessionStorage.setItem(STORE_KEY, JSON.stringify(creds));
        else sessionStorage.removeItem(STORE_KEY);
    } catch { /* storage blocked: the session just won't survive a reload */ }
}

export async function login(username, password) {
    await apiPost('/api/db-tweak/login', { username, password });
    creds = { username, password };
    save();
    notify('login');
    return currentUser();
}

export function logout() {
    creds = null;
    save();
    notify('logout');
}

/** A 401 from a DB Tweak call means the stored login no longer works. */
export function expire() {
    if (!creds) return;
    creds = null;
    save();
    notify('expired');
}

export async function restore() {
    let stored = null;
    try { stored = JSON.parse(sessionStorage.getItem(STORE_KEY) || 'null'); } catch { /* storage blocked */ }
    if (stored?.username && stored?.password) {
        try {
            await apiPost('/api/db-tweak/login', stored);
            creds = stored;
        } catch {
            creds = null;
            save();
        }
    }
    notify('restore');
}

export function displayName(u = currentUser()) {
    return u ? u.username : '';
}

/** Wire a login form (user / password inputs + button + message element). */
export function bindLoginForm({ user: userInput, pass, button, message }) {
    const submit = async () => {
        const name = userInput.value.trim();
        const pw = pass.value;
        if (!name || !pw) { message.textContent = t('auth.enterBoth'); return; }
        button.disabled = true;
        message.textContent = '';
        try {
            await login(name, pw);
            pass.value = '';
        } catch (err) {
            message.textContent = err.message;
        } finally {
            button.disabled = false;
        }
    };
    button.addEventListener('click', submit);
    pass.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });
}
