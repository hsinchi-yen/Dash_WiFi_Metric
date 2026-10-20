// Every t('key') / data-i18n key used by web/ must exist in both dictionaries, and the two
// dictionaries must have the same keys and {variables}. Run: node tests/js/i18n_keys.mjs
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'dockerup-essential', 'web');
const en = (await import(pathToFileURL(join(root, 'js/core/strings.en.js')))).default;
const zh = (await import(pathToFileURL(join(root, 'js/core/strings.zh.js')))).default;

function walk(dir, out = []) {
    for (const name of readdirSync(dir)) {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) { if (name !== 'vendor') walk(p, out); }
        else if (/\.(js|html)$/.test(name) && !/strings\.(en|zh)\.js$/.test(name)) out.push(p);
    }
    return out;
}

const used = new Set();
const dynamicPrefixes = new Set();
for (const file of walk(root)) {
    const src = readFileSync(file, 'utf8').split('\n').filter(l => !l.trimStart().startsWith('//')).join('\n');
    for (const m of src.matchAll(/\bt(?:Lang\([^,]+,)?\(\s*'([^']+)'/g)) used.add(m[1]);
    for (const m of src.matchAll(/data-i18n(?:-placeholder|-title|-aria)?="([^"]+)"/g)) used.add(m[1]);
    for (const m of src.matchAll(/\bt\(\s*`([^`$]*)\$\{/g)) dynamicPrefixes.add(m[1]);
}

let failures = 0;
const report = (msg) => { failures++; console.log('FAIL', msg); };
for (const k of used) {
    if (!(k in en)) report(`missing in en: ${k}`);
    if (!(k in zh)) report(`missing in zh: ${k}`);
}
for (const k of Object.keys(en)) if (!(k in zh)) report(`zh lacks: ${k}`);
for (const k of Object.keys(zh)) if (!(k in en)) report(`en lacks: ${k}`);
const vars = s => [...String(s).matchAll(/\{(\w+)\}/g)].map(m => m[1]).sort().join(',');
for (const k of Object.keys(en)) if (k in zh && vars(en[k]) !== vars(zh[k])) report(`variables differ: ${k}`);
for (const k of Object.keys(en)) if (/[一-鿿]/.test(en[k]) && !['lang.switch', 'lang.aria', 'rp.zh'].includes(k)) report(`Chinese in en: ${k}`);
console.log(`${used.size} keys used, ${Object.keys(en).length} en / ${Object.keys(zh).length} zh; dynamic prefixes: ${[...dynamicPrefixes].join(' ')}`);
console.log(failures ? `${failures} problem(s)` : 'i18n OK');
process.exit(failures ? 1 : 0);
