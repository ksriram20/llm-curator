/* llm-curator shared JS utilities — v0.2 */

/* ── Formatting ────────────────────────────────────────────────────────── */
function fmtDate(iso) {
    if (!iso) return '—';
    return new Date(iso).toLocaleString('en-IN', {
        timeZone: 'Asia/Kolkata',
        year: 'numeric', month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit'
    });
}

function fmtScore(v) {
    if (v === null || v === undefined) return '<span class="s-nil">—</span>';
    const pct = (parseFloat(v) * 100).toFixed(1);
    const cls = v >= 0.8 ? 's-hi' : v >= 0.5 ? 's-mid' : 's-lo';
    return `<span class="${cls}">${pct}%</span>`;
}

function fmtLatency(ms) {
    if (!ms) return '—';
    return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

/* ── Badges ─────────────────────────────────────────────────────────────── */
function freeBadge(isFree) {
    return isFree
        ? '<span class="bdg bdg-green">free</span>'
        : '<span class="bdg bdg-orange">paid</span>';
}

function routingBadge(inLitellm) {
    return inLitellm ? '<span class="bdg bdg-blue">routing</span>' : '';
}

function deprecatedBadge(dep) {
    return dep ? '<span class="bdg bdg-red">deprecated</span>' : '';
}

function severityBadge(s) {
    const map = { critical: 'bdg-red', warn: 'bdg-yellow', info: 'bdg-blue' };
    return `<span class="bdg ${map[s] || 'bdg-gray'}">${s}</span>`;
}

function statusBadge(s) {
    const map = { pending: 'bdg-yellow', applied: 'bdg-green', rejected: 'bdg-gray', superseded: 'bdg-gray' };
    return `<span class="bdg ${map[s] || 'bdg-gray'}">${s}</span>`;
}

/* ── API fetch ───────────────────────────────────────────────────────────── */
async function api(path) {
    const res = await fetch(path);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
}

/* ── Table render ────────────────────────────────────────────────────────── */
function renderTable(tbodyId, rows, colFns) {
    const tbody = document.getElementById(tbodyId);
    if (!tbody) return;
    if (!rows || rows.length === 0) {
        tbody.innerHTML = `<tr><td colspan="${colFns.length}" class="empty-state">No data yet.</td></tr>`;
        return;
    }
    tbody.innerHTML = rows.map(r => `<tr>${colFns.map(fn => `<td>${fn(r)}</td>`).join('')}</tr>`).join('');
}

/* ── Alert badge in sidebar ──────────────────────────────────────────────── */
async function loadAlertBadge() {
    try {
        const { count } = await api('/api/alerts/count');
        const el = document.getElementById('alert-badge');
        if (el && count > 0) { el.textContent = count; el.style.display = 'inline'; }
    } catch (_) {}
}

/* ── Active nav link ─────────────────────────────────────────────────────── */
function markActiveNav() {
    const p = location.pathname.replace(/\/$/, '') || '/';
    document.querySelectorAll('.sidebar-nav a').forEach(a => {
        const href = (a.getAttribute('href') || '').replace(/\/$/, '') || '/';
        if (href === p) a.classList.add('active');
    });
}

/* ── Init ────────────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
    markActiveNav();
    loadAlertBadge();
});
