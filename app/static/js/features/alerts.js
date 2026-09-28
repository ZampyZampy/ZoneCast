// Attention-grabbing alerts (failed schedules, low disk) shown once per
// browser session — sessionStorage, so they come back on the next real
// login rather than on every tab switch within the same visit.
import { api } from '../lib/api.js';
import { $, esc } from '../lib/dom.js';

function alertText(a) {
    const key = `alert.${a.code}`;
    const text = t(key, a.params || {});
    return text === key ? a.message : text;
}

export async function showOnce() {
    let shown = false;
    try { shown = sessionStorage.getItem('zc_alerts_shown') === '1'; } catch (err) { /* private mode: show them */ }
    if (shown) return;
    try {
        const items = await api('/api/system/alerts');
        if (!items.length) return;
        $('startup-alerts').innerHTML = items.map(a => `
            <div class="alert alert-${esc(a.severity)} alert-dismissible fade show" role="alert">
                <i class="bi bi-exclamation-triangle-fill me-2" aria-hidden="true"></i>${esc(alertText(a))}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="${esc(t('common.close'))}"></button>
            </div>`).join('');
        try { sessionStorage.setItem('zc_alerts_shown', '1'); } catch (err) { /* non-fatal */ }
    } catch (err) { /* non-fatal: never block the dashboard over this */ }
}

// Shown when the initial data load fails (server restarting, network
// down): the tables would otherwise just stay silently empty.
export function showLoadError(message, retry) {
    const box = $('startup-alerts');
    box.innerHTML = `<div class="alert alert-danger d-flex align-items-center gap-3" role="alert">
        <span class="flex-grow-1">${esc(t('app.loadError'))} ${esc(message)}</span>
        <button type="button" class="btn btn-sm btn-outline-danger" id="load-retry-btn">${esc(t('common.retry'))}</button>
    </div>`;
    $('load-retry-btn').addEventListener('click', () => { box.innerHTML = ''; retry(); });
}
