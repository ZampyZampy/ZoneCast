// Event log (admin only): filters, export links, clear, retention.
import { api } from '../lib/api.js';
import { $, esc, run, toast } from '../lib/dom.js';
import { fmtDateTime } from '../lib/format.js';
import { onTabShown } from './nav.js';

const LEVEL_BADGE = { INFO: 'bg-secondary', WARNING: 'bg-warning text-dark', ERROR: 'bg-danger', CRITICAL: 'bg-danger' };
let rows = [];

function filterParams() {
    const params = new URLSearchParams();
    const level = $('logs-level-filter').value;
    const q = $('logs-search').value.trim();
    if (level) params.set('level', level);
    if (q) params.set('q', q);
    return params;
}

function syncExportLinks() {
    for (const format of ['csv', 'json']) {
        const params = filterParams();
        params.set('format', format);
        $(`logs-export-${format}`).href = `/api/logs/export?${params}`;
    }
}

export function render() {
    $('logs-body').innerHTML = rows.map(r => `
        <tr>
            <td class="text-nowrap small">${fmtDateTime(r.created_at)}</td>
            <td><span class="badge ${LEVEL_BADGE[r.level] || 'bg-secondary'}">${esc(r.level)}</span></td>
            <td class="small text-muted col-secondary">${esc(r.logger_name)}</td>
            <td class="small">${esc(r.message)}</td>
        </tr>`).join('') || `<tr><td colspan="4" class="text-muted">${esc(t('empty.events'))}</td></tr>`;
}

export async function load() {
    const params = filterParams();
    params.set('limit', '300');
    syncExportLinks();
    await run(async () => {
        rows = await api(`/api/logs?${params}`);
        render();
    });
}

async function loadRetention() {
    try {
        const s = await api('/api/logs/settings');
        $('logs-retention-never').checked = s.retention_days === null;
        $('logs-retention-days').value = s.retention_days ?? '';
        $('logs-retention-days').disabled = s.retention_days === null;
    } catch (err) { /* non-fatal: the form just keeps its defaults */ }
}

export function init() {
    onTabShown('logs', () => { load(); loadRetention(); });
    $('logs-refresh').addEventListener('click', (e) => run(load, { button: e.currentTarget }));
    $('logs-level-filter').addEventListener('change', load);
    let searchTimer;
    $('logs-search').addEventListener('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(load, 400);
    });
    $('logs-clear-btn').addEventListener('click', (e) => {
        if (!confirm(t('confirm.clearLogs'))) return;
        run(async () => {
            const res = await api('/api/logs', { method: 'DELETE' });
            await load();
            return res;
        }, { button: e.currentTarget, success: (res) => t('toast.logsCleared', { n: res.deleted }) });
    });
    $('logs-retention-never').addEventListener('change', (e) => { $('logs-retention-days').disabled = e.target.checked; });
    $('logs-retention-save').addEventListener('click', (e) => {
        const never = $('logs-retention-never').checked;
        const days = $('logs-retention-days').value;
        if (!never && !days) { toast(t('toast.retentionMissing'), 'danger'); return; }
        run(() => api('/api/logs/settings', {
            method: 'PUT',
            body: { retention_days: never ? null : parseInt(days, 10) },
        }), { button: e.currentTarget, success: t('toast.retentionSaved') });
    });
}
