// Speakers: table, ping, multicast preview/push, create/edit/delete.
import { api } from '../lib/api.js';
import { $, actionButton, esc, isVisible, modal, onAction, run, toast, withBusy } from '../lib/dom.js';
import { poller } from '../lib/poller.js';
import { makeSortable, sortedRows } from '../lib/sort.js';
import { isAdmin, onDataChange, refresh, state, zoneName } from '../state.js';
import { openBackups } from './backups.js';

// Brands whose driver supports per-slot paging volume (see
// services/drivers/*.py's supports_paging_volume).
const PAGING_VOLUME_BRANDS = ['fanvil'];
// Fixed dropdown values (see the <select> in dashboard.html) — anything
// else typed into the "Other" field is stored as-is.
const KNOWN_BRANDS = ['fanvil', 'algo', 'cyberdata', 'grandstream', 'axis', 'tiptel', 'akuvox'];

const SORT = {
    status: s => s.status,
    name: s => s.name,
    ip: s => s.ip_address.split('.').map(n => n.padStart(3, '0')).join('.'),
    zone: s => zoneNames(s),
    mcast: s => s.own_multicast_address,
    model: s => [s.brand, s.model].filter(Boolean).join(' '),
};

const zoneNames = (s) => s.zone_ids.map(zoneName).filter(Boolean).join(', ');

function statusCell(s) {
    const label = esc(t(`status.${s.status}`));
    // The device didn't take its latest paging list (offline, wrong
    // credentials...): its zones in the dashboard aren't what it plays.
    let sync = '';
    if (s.paging_sync_ok === false && s.paging_sync_error === 'pending') {
        sync = ` <span class="badge text-bg-info" title="${esc(t('speakers.syncPendingTitle'))}">${esc(t('speakers.syncPending'))}</span>`;
    } else if (s.paging_sync_ok === false) {
        sync = ` <span class="badge text-bg-warning" title="${esc(t('speakers.outOfSyncTitle', { error: s.paging_sync_error || '' }))}">${esc(t('speakers.outOfSync'))}</span>`;
    }
    return `<span class="status-dot status-${esc(s.status)}" title="${label}" aria-hidden="true"></span><span class="status-dot-label">${label}</span>${sync}`;
}

function actions(s) {
    const admin = isAdmin();
    let deviceAction = '';
    if (admin && s.supports_auto_config) {
        deviceAction = actionButton('push', s.id, 'bi-cloud-upload', 'action.applyNow', 'btn-outline-secondary', 'speakers.applyNowTitle');
    } else if (admin) {
        deviceAction = `<span class="text-muted small fst-italic me-2" title="${esc(t('speakers.manualConfigTitle'))}">${esc(t('action.manualConfig'))}</span>`;
    }
    return [
        actionButton('ping', s.id, 'bi-broadcast', 'action.ping'),
        actionButton('preview', s.id, 'bi-eye', 'action.preview', 'btn-outline-secondary', 'speakers.previewTitle'),
        deviceAction,
        admin && s.supports_config_backup ? actionButton('backups', s.id, 'bi-archive', 'action.backup', 'btn-outline-secondary', 'speakers.backupTitle') : '',
        actionButton('edit', s.id, 'bi-pencil', 'action.edit', 'btn-outline-primary'),
        actionButton('delete', s.id, 'bi-trash', 'action.delete', 'btn-outline-danger'),
    ].join('');
}

export function render() {
    $('speakers-body').innerHTML = sortedRows('speakers', state.speakers, SORT).map(s => `
        <tr>
            <td>${statusCell(s)}</td>
            <td>${esc(s.name)}</td>
            <td>${esc(s.ip_address)}</td>
            <td class="col-secondary">${esc(zoneNames(s) || '—')}</td>
            <td class="col-secondary"><code>${esc(s.own_multicast_address)}:${esc(s.own_multicast_port)}</code></td>
            <td class="col-secondary">${esc([s.brand, s.model].filter(Boolean).join(' ') || '—')}</td>
            <td class="table-actions text-end">${actions(s)}</td>
        </tr>`).join('') || `<tr><td colspan="7" class="text-muted">${esc(t('empty.speakers'))}</td></tr>`;
}

function effectiveBrand() {
    const selected = $('speaker-brand').value;
    return selected === 'other' ? $('speaker-brand-other').value.trim() : selected;
}
function toggleBrandFields() {
    $('speaker-brand-other').classList.toggle('d-none', $('speaker-brand').value !== 'other');
    $('speaker-volume-wrap').classList.toggle('d-none', !PAGING_VOLUME_BRANDS.includes(effectiveBrand().toLowerCase()));
}

function resetForm() {
    $('speaker-form').reset();
    $('speaker-id').value = '';
    $('speaker-mcast-port').value = 5004;
    $('speaker-zones').textContent = '—';
    toggleBrandFields();
}

function edit(id) {
    const s = state.speakers.find(x => x.id === id);
    if (!s) return;
    resetForm();
    $('speaker-id').value = s.id;
    $('speaker-name').value = s.name;
    $('speaker-ip').value = s.ip_address;
    $('speaker-zones').textContent = zoneNames(s) || t('common.none');
    $('speaker-mcast-addr').value = s.own_multicast_address;
    $('speaker-mcast-port').value = s.own_multicast_port;
    const brand = (s.brand || '').toLowerCase();
    if (brand && !KNOWN_BRANDS.includes(brand)) {
        $('speaker-brand').value = 'other';
        $('speaker-brand-other').value = s.brand;
    } else {
        $('speaker-brand').value = brand;
    }
    $('speaker-model').value = s.model || '';
    $('speaker-location').value = s.location || '';
    $('speaker-http-user').value = s.http_username || '';
    $('speaker-paging-volume').value = s.paging_volume || '';
    toggleBrandFields();
    modal('speaker-modal').show();
}

async function ping(id, btn) {
    await withBusy(btn, async () => {
        try {
            const s = await api(`/api/speakers/${id}/ping`, { method: 'POST' });
            const online = s.status === 'online';
            toast(t(online ? 'toast.pingOnline' : 'toast.pingOffline', { name: s.name }), online ? 'success' : 'danger');
            const idx = state.speakers.findIndex(x => x.id === s.id);
            if (idx !== -1) { state.speakers[idx] = s; render(); }
        } catch (err) { toast(err.message, 'danger'); }
    }, { busyLabel: t('action.checking') });
}

async function preview(id) {
    try {
        const entries = await api(`/api/speakers/${id}/multicast-preview`);
        const lines = entries.map(e => `#${e.index}  ${e.address}:${e.port}  "${e.label}"  (${t('speakers.previewPriority')} ${e.priority})`).join('\n');
        alert(`${t('speakers.previewIntro')}\n\n${lines}`);
    } catch (err) { toast(err.message, 'danger'); }
}

async function push(id, btn) {
    await withBusy(btn, async () => {
        try {
            const res = await api(`/api/speakers/${id}/push-config`, { method: 'POST' });
            toast(`${t('toast.configApplied')} (${res.applied.length})`);
        } catch (err) {
            const code = err.detail && err.detail.code;
            if (code === 'speakers.push_unsupported') {
                toast(err.message, 'warning');
            } else if (code === 'speakers.push_failed') {
                const { applied, failed } = err.detail.params;
                toast(`${t('toast.configPartial')}: ${applied.length}/${applied.length + failed.length}`, 'warning');
                alert(`${t('toast.configPartial')}: ${applied.length} ${t('toast.confirmed')}, ${failed.length} ${t('toast.failed')}.\n\n${t('toast.failedList')}: ${failed.join(', ')}\n\n${t('toast.retryHint')}`);
            } else {
                toast(err.message, 'danger');
            }
        }
        try { await refresh('speakers'); } catch (err) { /* the badge catches up on the next refresh */ }
    }, { busyLabel: t('action.applying') });
}

// Pushes to the devices run in the background after a save: while any is
// pending, re-read the speakers so "Updating…" turns into the outcome.
const SYNC_POLL_MS = 3000;
const syncPoller = poller(() => refresh('speakers'), SYNC_POLL_MS, {
    when: () => state.speakers.some(s => s.paging_sync_ok === false && s.paging_sync_error === 'pending')
        && (isVisible($('tab-speakers')) || isVisible($('tab-zones'))),
});

export function init() {
    syncPoller.start();
    makeSortable('speakers', SORT, render);
    onDataChange((changed) => {
        if (changed.has('speakers') || changed.has('zones')) render();
    });
    $('speaker-brand').addEventListener('change', toggleBrandFields);
    $('speaker-brand-other').addEventListener('input', toggleBrandFields);
    $('new-speaker-btn').addEventListener('click', resetForm);

    $('speaker-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const id = $('speaker-id').value;
        const payload = {
            name: $('speaker-name').value,
            ip_address: $('speaker-ip').value,
            own_multicast_address: $('speaker-mcast-addr').value,
            own_multicast_port: Number($('speaker-mcast-port').value),
            brand: effectiveBrand(),
            model: $('speaker-model').value,
            location: $('speaker-location').value,
            http_username: $('speaker-http-user').value,
            paging_volume: $('speaker-paging-volume').value || null,
        };
        // On edit the stored password stays unless a new one is typed (the
        // API never sends the stored password back to the browser).
        const pass = $('speaker-http-pass').value;
        if (!id || pass) payload.http_password = pass;
        run(async () => {
            await api(id ? `/api/speakers/${id}` : '/api/speakers', { method: id ? 'PUT' : 'POST', body: payload });
            modal('speaker-modal').hide();
            await refresh('speakers');
        }, { button: e.submitter, success: t('toast.speakerSaved') });
    });

    onAction($('speakers-body'), {
        ping: (id, btn) => ping(id, btn),
        preview: (id) => preview(id),
        push: (id, btn) => push(id, btn),
        backups: (id) => openBackups(id),
        edit: (id) => edit(id),
        delete: (id, btn) => {
            if (!confirm(t('confirm.deleteSpeaker'))) return;
            run(async () => {
                await api(`/api/speakers/${id}`, { method: 'DELETE' });
                await refresh('speakers', 'zones', 'overlaps');  // its zones' schedules may no longer overlap
            }, { button: btn });
        },
    });
}
