// System tab (admin only): network, time/NTP, host resources, version,
// full export/import.
import { api, download, upload } from '../lib/api.js';
import { $, esc, fillSelect, isVisible, run, toast, withBusy } from '../lib/dom.js';
import { fmtGiB } from '../lib/format.js';
import { poller } from '../lib/poller.js';
import { onTabHidden, onTabShown } from './nav.js';

let lastTime = null;
let timezonesLoaded = false;
let networkLoaded = false;
let netCountdownTimer = null;
const resourcesPoller = poller(loadResources, 15000, { when: () => isVisible($('tab-system')) });

// ---------- Host resources ----------
const barClass = (pct) => (pct >= 90 ? 'bg-danger' : pct >= 75 ? 'bg-warning' : 'bg-success');

function setBar(id, pct) {
    const bar = $(id);
    bar.style.width = `${pct}%`;
    bar.className = `progress-bar ${barClass(pct)}`;
    bar.setAttribute('aria-valuenow', String(pct));
}

async function loadResources() {
    try {
        const r = await api('/api/system/resources');
        $('res-error').classList.add('d-none');
        $('res-disk-label').textContent = `${fmtGiB(r.disk_used_bytes)} / ${fmtGiB(r.disk_total_bytes)} GiB (${r.disk_percent}%)`;
        setBar('res-disk-bar', r.disk_percent);
        $('res-mem-label').textContent = `${fmtGiB(r.mem_used_bytes)} / ${fmtGiB(r.mem_total_bytes)} GiB (${r.mem_percent}%)`;
        setBar('res-mem-bar', r.mem_percent);
        $('res-cpu-count').textContent = r.cpu_count;
        $('res-cpu-label').textContent = `${r.cpu_percent}%`;
        setBar('res-cpu-bar', r.cpu_percent);
    } catch (err) {
        $('res-error').textContent = err.message;
        $('res-error').classList.remove('d-none');
    }
}

// ---------- Time / NTP ----------
export function renderTimeLabels() {
    if (!lastTime) return;
    $('sys-sync-status').innerHTML = lastTime.ntp_synchronized
        ? `<span class="badge bg-success">${esc(t('system.synced'))}</span>`
        : `<span class="badge bg-warning text-dark">${esc(t('system.notSynced'))}</span>`;
    $('sys-ntp-servers-display').textContent = lastTime.ntp_servers.length ? lastTime.ntp_servers.join(', ') : t('system.noneConfigured');
}

async function loadTime() {
    try {
        const status = await api('/api/system/time');
        lastTime = status;
        $('sys-local-time').textContent = status.local_time;
        $('sys-timezone').textContent = status.timezone;
        $('sys-scheduler-timezone').textContent = status.scheduler_timezone || '—';
        $('sys-timezone-mismatch').classList.toggle('d-none', !status.scheduler_timezone || status.scheduler_timezone === status.timezone);
        renderTimeLabels();
        $('sys-ntp-controls').classList.toggle('d-none', !status.controllable);
        $('sys-time-readonly-note').classList.toggle('d-none', status.controllable);
        $('sys-network-card').classList.toggle('d-none', !status.controllable);
        if (!status.controllable) return;
        $('sys-ntp-toggle').checked = !!status.ntp_enabled;
        const ntpInput = $('sys-ntp-servers-input');
        if (document.activeElement !== ntpInput) ntpInput.value = status.ntp_servers.join(', ');
        if (!timezonesLoaded) {
            timezonesLoaded = true;
            try {
                const zones = await api('/api/system/time/timezones');
                fillSelect($('sys-timezone-select'), zones, { value: z => z, label: z => z });
                $('sys-timezone-select').value = status.timezone;
            } catch (err) { /* select stays empty */ }
        }
        // The network form is filled once, not on every time refresh:
        // re-filling it would throw away an admin's unsaved edits there.
        if (!networkLoaded) { networkLoaded = true; loadNetwork(); }
    } catch (err) {
        $('sys-local-time').textContent = t('system.notAvailable');
        $('sys-sync-status').innerHTML = `<span class="text-danger small">${esc(err.message)}</span>`;
    }
}

// ---------- Network ----------
function showTrialBox(seconds) {
    const box = $('net-trial-box');
    box.classList.remove('d-none');
    let remaining = seconds;
    $('net-countdown').textContent = Math.max(remaining, 0);
    clearInterval(netCountdownTimer);
    netCountdownTimer = setInterval(() => {
        remaining -= 1;
        $('net-countdown').textContent = Math.max(remaining, 0);
        if (remaining <= 0) { clearInterval(netCountdownTimer); box.classList.add('d-none'); }
    }, 1000);
}

async function loadNetwork() {
    try {
        const n = await api('/api/system/network');
        fillSelect($('net-interface'), n.available_interfaces, { value: i => i, label: i => i });
        $('net-interface').value = n.interface;
        $('net-address').value = n.address_cidr || '';
        $('net-gateway').value = n.gateway || '';
        $('net-dns').value = (n.dns_servers || []).join(', ');
    } catch (err) { /* card hidden when not controllable */ }
    // A change applied from ANY session shows up here too, so reconnecting
    // at the new address in a fresh page still offers the Confirm button.
    try {
        const p = await api('/api/system/network/pending');
        if (p.pending_seconds !== null && p.pending_seconds !== undefined) showTrialBox(p.pending_seconds);
        else $('net-trial-box').classList.add('d-none');
    } catch (err) { /* non-fatal */ }
}

// ---------- Version / changelog ----------
export async function loadVersion() {
    const link = $('version-link');
    try {
        const v = await api('/api/system/version');
        link.textContent = `v${v.version}`;
        $('changelog-body').innerHTML = v.changelog.map(entry => `
            <div class="mb-3">
                <div class="fw-semibold">v${esc(entry.version)} <span class="text-muted small fw-normal">${esc(entry.date)}</span></div>
                <ul class="small mb-0">${entry.changes.map(c => `<li>${esc(c)}</li>`).join('')}</ul>
            </div>`).join('');
    } catch (err) { link.textContent = '?'; }
}

function bindTimeControls() {
    const after = () => loadTime();
    $('sys-manual-time-save').addEventListener('click', (e) => {
        const value = $('sys-manual-time').value;
        if (!value) { toast(t('toast.selectDateTime'), 'danger'); return; }
        run(async () => {
            const res = await api('/api/system/time/manual', { method: 'POST', body: { datetime_local: value } });
            await after();
            return res;
        }, { button: e.currentTarget, success: (res) => (res.ntp_disabled ? t('toast.timeSetNtpOff') : t('toast.timeSet')) });
    });
    $('sys-ntp-servers-save').addEventListener('click', (e) => {
        const servers = $('sys-ntp-servers-input').value.split(/[,\s]+/).map(s => s.trim()).filter(Boolean);
        run(async () => {
            await api('/api/system/time/ntp-servers', { method: 'POST', body: { servers } });
            await after();
        }, { button: e.currentTarget, success: t('toast.ntpServersUpdated') });
    });
    $('sys-ntp-toggle').addEventListener('change', async (e) => {
        const toggle = e.target;
        try {
            await api('/api/system/time/ntp', { method: 'POST', body: { enabled: toggle.checked } });
            toast(toggle.checked ? t('toast.ntpEnabled') : t('toast.ntpDisabled'));
            await after();
        } catch (err) {
            toast(err.message, 'danger');
            toggle.checked = !toggle.checked;
        }
    });
    $('sys-timezone-save').addEventListener('click', (e) => {
        const tz = $('sys-timezone-select').value;
        if (!tz) return;
        run(async () => {
            await api('/api/system/time/timezone', { method: 'POST', body: { timezone: tz } });
            await after();
        }, { button: e.currentTarget, success: t('toast.timezoneUpdated') });
    });
}

function bindNetworkControls() {
    $('net-apply-btn').addEventListener('click', (e) => {
        const errBox = $('net-error');
        errBox.classList.add('d-none');
        const payload = {
            interface: $('net-interface').value,
            address_cidr: $('net-address').value.trim(),
            gateway: $('net-gateway').value.trim(),
            dns_servers: $('net-dns').value.split(',').map(s => s.trim()).filter(Boolean),
        };
        if (!confirm(`${t('confirm.netApply')} "${payload.interface}"? ${t('confirm.netApplyEnd')}`)) return;
        withBusy(e.currentTarget, async () => {
            try {
                const result = await api('/api/system/network/apply', { method: 'POST', body: payload });
                showTrialBox(result.watchdog_seconds);
            } catch (err) {
                errBox.textContent = err.message;
                errBox.classList.remove('d-none');
            }
        });
    });
    $('net-confirm-btn').addEventListener('click', (e) => run(async () => {
        await api('/api/system/network/confirm', { method: 'POST' });
        clearInterval(netCountdownTimer);
        $('net-trial-box').classList.add('d-none');
    }, { button: e.currentTarget, success: t('toast.netConfirmed') }));
}

function bindExportImport() {
    $('export-download-btn').addEventListener('click', (e) => {
        const errBox = $('export-error');
        errBox.classList.add('d-none');
        withBusy(e.currentTarget, async () => {
            try {
                await download('/api/system/export', { password: $('export-password').value }, 'zonecast_export.zcbundle');
                $('export-password').value = '';
                toast(t('toast.exportDownloaded'));
            } catch (err) {
                errBox.textContent = err.message;
                errBox.classList.remove('d-none');
            }
        });
    });
    $('import-upload-btn').addEventListener('click', (e) => {
        const errBox = $('import-error');
        const infoBox = $('import-info');
        errBox.classList.add('d-none');
        infoBox.classList.add('d-none');
        const fileInput = $('import-file');
        if (!fileInput.files.length) { toast(t('toast.chooseFile'), 'danger'); return; }
        if (!confirm(t('confirm.import'))) return;
        const fd = new FormData();
        fd.append('file', fileInput.files[0]);
        fd.append('password', $('import-password').value);
        withBusy(e.currentTarget, async () => {
            try {
                const data = await upload('/api/system/import', fd);
                infoBox.textContent = data.message;
                infoBox.classList.remove('d-none');
                toast(t('toast.importStarted'));
            } catch (err) {
                errBox.textContent = err.message;
                errBox.classList.remove('d-none');
            }
        });
    });
}

export function init() {
    bindTimeControls();
    bindNetworkControls();
    bindExportImport();
    onTabShown('system', () => { loadTime(); loadResources(); resourcesPoller.start(); });
    onTabHidden('system', () => resourcesPoller.stop());
}
