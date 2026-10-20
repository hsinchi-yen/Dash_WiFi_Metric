// Chart.js helpers. Colours are read from the theme's CSS variables, so switching
// Dark / Light re-renders every chart with matching colours.
const charts = new Map();

export function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function palette() {
    return {
        pass: cssVar('--c-pass'), fail: cssVar('--c-fail'), stop: cssVar('--c-stop'),
        c1: cssVar('--c-1'), c2: cssVar('--c-2'), c3: cssVar('--c-3'), c4: cssVar('--c-4'),
        muted: cssVar('--c-muted'), grid: cssVar('--grid'), text: cssVar('--text'), text2: cssVar('--text-2'),
        good: cssVar('--good'), warn: cssVar('--warn'), bad: cssVar('--bad'),
    };
}

/** Translucent version of a hex colour. */
export function alpha(hex, a) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
    if (!m) return hex;
    const n = parseInt(m[1], 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

export function applyDefaults() {
    const C = window.Chart;
    if (!C) return;
    const p = palette();
    C.defaults.color = p.text2;
    C.defaults.borderColor = p.grid;
    C.defaults.font.family = getComputedStyle(document.body).fontFamily;
    C.defaults.font.size = 13;
    C.defaults.maintainAspectRatio = false;
    C.defaults.animation.duration = 300;
    C.defaults.plugins.legend.labels.boxWidth = 12;
    C.defaults.plugins.tooltip.padding = 10;
}

function clearMessage(canvas) {
    canvas.parentElement?.querySelector(':scope > .state-msg')?.remove();
}

/** Show a message over a chart area instead of an empty canvas. */
export function chartMessage(canvasId, text, kind = '') {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    destroy(canvasId);
    clearMessage(canvas);
    const div = document.createElement('div');
    div.className = `state-msg ${kind}`;
    div.textContent = text;
    canvas.parentElement.appendChild(div);
}

export function destroy(canvasId) {
    const chart = charts.get(canvasId);
    if (chart) { chart.destroy(); charts.delete(canvasId); }
}

/** (Re)create a chart. `build(palette)` returns the Chart.js config. */
export function render(canvasId, build) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !window.Chart) return null;
    destroy(canvasId);
    clearMessage(canvas);
    const chart = new window.Chart(canvas, build(palette()));
    chart.$build = build;
    charts.set(canvasId, chart);
    return chart;
}

/** After a theme switch: rebuild every live chart with the new colours. */
export function rethemeAll() {
    applyDefaults();
    for (const [id, chart] of [...charts]) {
        if (chart.$build) render(id, chart.$build);
    }
}

export const yesNoScale = { beginAtZero: true, ticks: { precision: 0 } };
