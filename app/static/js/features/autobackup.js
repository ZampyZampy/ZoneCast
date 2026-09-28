// System > Automatic backup (admin only): schedule, password, where to
// copy, test, "back up now", status and the files kept on this server.
import { api } from '../lib/api.js';
import { $, esc, run, toast } from '../lib/dom.js';
import { fmtDateTime } from '../lib/format.js';
import { onTabShown } from './nav.js';

const DEFAULT_PORTS = { ftps: 21, ftp: 21, smb: 445 };
const POLL_MS = 3000;
let policy = null;
let pollTimer = null;

const errorText = (code, detail) => {
    const key = `error.${code}`;
    const text = t(key);
    return `${text === key ? code : text}${detail ? ` (${detail})` : ''}`;
};

function toggleFields() {
    const dest = $('ab-destination').value;
    const remote = dest !== 'local';
    $('ab-weekday-wrap').classList.toggle('d-none', $('ab-frequency').value !== 'weekly');
    $('ab-remote-fields').classList.toggle('d-none', !remote);
    document.querySelectorAll('.ab-remote-only').forEach(el => el.classList.toggle('d-none', !remote));
    $('ab-test').classList.toggle('d-none', !remote);
    $('ab-share-wrap').classList.toggle('d-none', dest !== 'smb');
    $('ab-smb-encrypt-wrap').classList.toggle('d-none', dest !== 'smb');
    $('ab-ftps-wrap').classList.toggle('d-none', dest !== 'ftps');
    $('ab-ftp-wrap').classList.toggle('d-none', dest !== 'ftp');
    $('ab-port').placeholder = DEFAULT_PORTS[dest] || '';
}

function fill(p) {
    policy = p;
    $('ab-foreign').classList.toggle('d-none', !p.foreign);
    $('ab-enabled').checked = p.enabled;
    $('ab-frequency').value = p.frequency;
    $('ab-weekday').value = String(p.weekday);
    $('ab-time').value = p.time_of_day.slice(0, 5);
    $('ab-media').checked = p.include_media;
    $('ab-keep-local').value = p.keep_local;
    $('ab-keep-remote').value = p.keep_remote;
    $('ab-bundle-password').value = '';
    $('ab-bundle-confirm').checked = false;
    $('ab-bundle-password-state').textContent = t(p.has_bundle_password ? 'autobackup.passwordStored' : 'autobackup.passwordMissing');
    $('ab-destination').value = p.destination;
    $('ab-host').value = p.host;
    $('ab-port').value = p.port ?? '';
    $('ab-share').value = p.share;
    $('ab-remote-dir').value = p.remote_dir;
    $('ab-username').value = p.username;
    $('ab-password').value = '';
    $('ab-password-state').textContent = p.has_password ? t('autobackup.passwordStored') : '';
    $('ab-smb-encrypt').checked = p.smb_encrypt;
    $('ab-insecure').checked = p.allow_insecure_ftp;
    $('ab-fingerprint').value = p.tls_fingerprint;
    toggleFields();
    renderStatus();
}

function renderStatus() {
    const p = policy;
    if (!p) return;
    const rows = [];
    const add = (labelKey, value) => rows.push(`<dt class="col-sm-4 fw-normal text-muted">${esc(t(labelKey))}</dt><dd class="col-sm-8 mb-1">${value}</dd>`);
    const badge = { ok: 'bg-success', failed: 'bg-danger', paused_foreign: 'bg-warning text-dark' }[p.last_status];
    add('autobackup.lastRun', p.running
        ? `<span class="badge bg-primary">${esc(t('autobackup.running'))}</span>`
        : (p.last_run_at ? `${esc(fmtDateTime(p.last_run_at))} <span class="badge ${badge || 'bg-secondary'}">${esc(t(`autobackup.status_${p.last_status}`))}</span>` : esc(t('autobackup.never'))));
    if (p.last_status === 'failed') add('autobackup.lastError', esc(errorText(p.last_error_code, p.last_error)));
    add('autobackup.lastSuccess', p.last_success_at ? esc(fmtDateTime(p.last_success_at)) : esc(t('autobackup.never')));
    if (p.last_warning) add('autobackup.lastWarning', esc(p.last_warning));
    if (p.enabled && p.next_run_at) add('autobackup.nextRun', esc(fmtDateTime(p.next_run_at)));
    $('ab-status').innerHTML = rows.join('');
}

function renderFiles(files) {
    $('ab-files').innerHTML = files.map(f => {
        const url = `/api/system/auto-backup/files/${encodeURIComponent(f.name)}`;
        return `<tr>
            <td class="text-break"><code>${esc(f.name)}</code>${f.own ? '' : ` <span class="badge bg-secondary">${esc(t('autobackup.otherInstallation'))}</span>`}</td>
            <td class="text-nowrap">${esc((f.size / 1024 / 1024).toFixed(1))} MB</td>
            <td class="table-actions text-end">
                <a class="btn btn-sm btn-outline-secondary" href="${esc(url)}" download aria-label="${esc(t('action.download'))}" title="${esc(t('action.download'))}"><i class="bi bi-download" aria-hidden="true"></i></a>
                <button type="button" class="btn btn-sm btn-outline-danger" data-ab-delete="${esc(f.name)}" aria-label="${esc(t('action.delete'))}" title="${esc(t('action.delete'))}"><i class="bi bi-trash" aria-hidden="true"></i></button>
            </td>
        </tr>`;
    }).join('') || `<tr><td class="text-muted">${esc(t('autobackup.noFiles'))}</td></tr>`;
}

export async function load() {
    try {
        const [p, files] = await Promise.all([api('/api/system/auto-backup'), api('/api/system/auto-backup/files')]);
        fill(p);
        renderFiles(files);
        if (p.running) pollWhileRunning();
    } catch (err) {
        toast(err.message, 'danger');
    }
}

function pollWhileRunning() {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(async () => {
        try {
            const p = await api('/api/system/auto-backup');
            policy = { ...policy, ...p };
            renderStatus();
            if (p.running) { pollWhileRunning(); return; }
            renderFiles(await api('/api/system/auto-backup/files'));
            toast(p.last_status === 'ok' ? t('autobackup.done') : errorText(p.last_error_code, p.last_error),
                p.last_status === 'ok' ? 'success' : 'danger');
        } catch (err) { /* transient: the next load() shows the state */ }
    }, POLL_MS);
}

function payload() {
    const port = $('ab-port').value;
    return {
        enabled: $('ab-enabled').checked,
        frequency: $('ab-frequency').value,
        weekday: Number($('ab-weekday').value),
        time_of_day: `${$('ab-time').value}:00`,
        include_media: $('ab-media').checked,
        keep_local: Number($('ab-keep-local').value),
        keep_remote: Number($('ab-keep-remote').value),
        bundle_password: $('ab-bundle-password').value || null,
        bundle_password_confirmed: $('ab-bundle-confirm').checked,
        destination: $('ab-destination').value,
        host: $('ab-host').value.trim(),
        port: port ? Number(port) : null,
        share: $('ab-share').value.trim(),
        remote_dir: $('ab-remote-dir').value.trim(),
        username: $('ab-username').value,
        password: $('ab-password').value || null,
        smb_encrypt: $('ab-smb-encrypt').checked,
        allow_insecure_ftp: $('ab-insecure').checked,
        tls_fingerprint: $('ab-fingerprint').value.trim(),
    };
}

function showTest(result) {
    const box = $('ab-test-result');
    box.className = `alert py-2 mt-3 ${result.ok ? 'alert-success' : 'alert-danger'}`;
    box.replaceChildren(document.createTextNode(result.ok ? t('autobackup.testOk') : errorText(result.code, result.detail)));
    if (!result.ok && result.fingerprint) {
        const info = document.createElement('div');
        info.className = 'small mt-2';
        info.textContent = t('autobackup.fingerprintSeen', { fingerprint: result.fingerprint });
        const trust = document.createElement('button');
        trust.type = 'button';
        trust.className = 'btn btn-sm btn-outline-dark mt-2';
        trust.textContent = t('autobackup.trustCertificate');
        trust.addEventListener('click', () => {
            $('ab-fingerprint').value = result.fingerprint;
            box.classList.add('d-none');
        });
        box.append(info, trust);
    }
}

export function init() {
    $('ab-destination').addEventListener('change', toggleFields);
    $('ab-frequency').addEventListener('change', toggleFields);
    $('autobackup-form').addEventListener('submit', (e) => {
        e.preventDefault();
        run(async () => {
            fill(await api('/api/system/auto-backup', { method: 'PUT', body: payload() }));
        }, { button: e.submitter, success: t('autobackup.saved') });
    });
    $('ab-test').addEventListener('click', (e) => run(async () => {
        $('ab-test-result').classList.add('d-none');
        showTest(await api('/api/system/auto-backup/test', { method: 'POST', body: payload() }));
    }, { button: e.currentTarget, busyLabel: t('action.checking') }));
    $('ab-run').addEventListener('click', (e) => run(async () => {
        await api('/api/system/auto-backup/run', { method: 'POST' });
        policy = { ...policy, running: true };
        renderStatus();
        pollWhileRunning();
    }, { button: e.currentTarget, success: t('autobackup.started') }));
    $('ab-files').addEventListener('click', (e) => {
        const btn = e.target.closest('[data-ab-delete]');
        if (!btn || !confirm(t('autobackup.confirmDelete'))) return;
        run(async () => {
            await api(`/api/system/auto-backup/files/${encodeURIComponent(btn.dataset.abDelete)}`, { method: 'DELETE' });
            renderFiles(await api('/api/system/auto-backup/files'));
        }, { button: btn });
    });
    onTabShown('system', load);
}

export function rerender() {
    renderStatus();
}
