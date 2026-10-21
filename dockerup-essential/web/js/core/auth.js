// One sign-in for DB Maintenance, Log Upload, products and accounts.
// POST /api/auth/login returns a random session token (sent as X-Tweak-Token). It is kept in
// sessionStorage: a reload stays signed in, closing the tab signs out.
// role 'admin' → everything; role 'uploader' → Log Upload (own batches) + adding products;
// role 'rd' → failure analysis (reads everything, no uploads).
import { apiGet, apiPost, getToken, setToken, setUnauthorizedHandler } from './api.js';
import { t } from './i18n.js';
import { openFormModal } from './ui.js';

const STORE_KEY = 'wifi-auth-v1';
const listeners = new Set();
let user = null;   // { username, role, display_name }

export const currentUser = () => user;
export const isAdmin = () => user?.role === 'admin';
export const isSignedIn = () => !!user;
export const isRd = () => user?.role === 'rd';
/** Log Upload and adding products: uploaders and the administrator (RD accounts analyse, they do not upload). */
export const canUpload = () => user?.role === 'admin' || user?.role === 'uploader';

export function onAuthChange(fn) { listeners.add(fn); }

function notify(reason) {
    listeners.forEach(fn => { try { fn(user, reason); } catch (err) { console.error(err); } });
}

function save() {
    try {
        if (getToken()) sessionStorage.setItem(STORE_KEY, getToken());
        else sessionStorage.removeItem(STORE_KEY);
    } catch { /* storage blocked: the session just won't survive a reload */ }
}

export async function login(username, password) {
    const data = await apiPost('/api/auth/login', { username, password });
    setToken(data.token);
    user = { username: data.username, role: data.role, display_name: data.display_name };
    save();
    notify('login');
    return user;
}

function clear(reason) {
    setToken(null);
    user = null;
    save();
    notify(reason);
}

export function logout() {
    const token = getToken();
    clear('logout');
    if (token) fetch('/api/auth/logout', { method: 'POST', headers: { 'X-Tweak-Token': token } }).catch(() => {});
}

export async function restore() {
    let token = null;
    try { token = sessionStorage.getItem(STORE_KEY); } catch { /* storage blocked */ }
    if (token) {
        setToken(token);
        try {
            const me = await apiGet('/api/auth/me');
            user = { username: me.username, role: me.role, display_name: me.display_name };
        } catch (err) {
            setToken(null);
            if (err.status === 401) save();   // expired or revoked: forget it
        }
    }
    notify('restore');
}

// Any 401 while signed in: expired, revoked, or the account was disabled.
setUnauthorizedHandler(() => clear('expired'));

export function displayName(u = user) {
    if (!u) return '';
    return u.display_name && u.display_name !== u.username ? t('common.nameWithId', { name: u.display_name, id: u.username }) : u.username;
}

export function openChangePassword() {
    openFormModal({
        title: t('auth.changePw'),
        desc: t('auth.changePwDesc'),
        fields: [
            { name: 'old_password', label: t('auth.oldPw'), type: 'password', required: true, autocomplete: 'current-password' },
            { name: 'new_password', label: t('auth.newPw'), type: 'password', required: true, autocomplete: 'new-password', hint: t('auth.pwHint') },
            { name: 'new_password2', label: t('auth.newPw2'), type: 'password', required: true, autocomplete: 'new-password' },
        ],
        submitLabel: t('auth.changePw'),
        onSubmit: async v => {
            if (v.new_password !== v.new_password2) return t('auth.pwMismatch');
            try {
                await apiPost('/api/auth/change-password', { old_password: v.old_password, new_password: v.new_password });
            } catch (err) {
                return err.message;
            }
            alert(t('auth.pwChanged'));
            return null;
        },
    });
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
