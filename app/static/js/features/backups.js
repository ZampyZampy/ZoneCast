// Device configuration backups (admin only): per-speaker dialog and the
// archive tab (which also keeps backups of speakers since deleted).
import { api } from '../lib/api.js';
import { $, actionButton, esc, modal, onAction, run } from '../lib/dom.js';
import { fmtDateTime } from '../lib/format.js';
import { state } from '../state.js';
import { onTabShown } from './nav.js';

let archive = [];
let speakerBackups = [];

function downloadLink(id) {
    const label = esc(t('action.download'));
    return `<a class="btn btn-sm btn-outline-secondary" href="/api/backups/${id}/download" aria-label="${label}" title="${label}"><i class="bi bi-download" aria-hidden="true"></i><span class="btn-label"> ${label}</span></a>`;
}

function renderSpeakerBackups() {
    $('backups-body').innerHTML = speakerBackups.map(b => `
        <tr>
            <td>${fmtDateTime(b.created_at)}</td>
            <td>${esc(b.format)}</td>
            <td class="col-secondary">${(b.size_bytes / 1024).toFixed(1)} KB</td>
            <td class="col-secondary">${esc(b.created_by_name || '—')}</td>
            <td class="text-end table-actions">${downloadLink(b.id)}${actionButton('delete', b.id, 'bi-trash', 'action.discard', 'btn-outline-danger')}</td>
        </tr>`).join('') || `<tr><td colspan="5" class="text-muted">${esc(t('empty.backups'))}</td></tr>`;
}

export function renderArchive() {
    $('backup-archive-body').innerHTML = archive.map(b => `
        <tr>
            <td>${fmtDateTime(b.created_at)}</td>
            <td>${esc(b.speaker_name || '—')}${b.speaker_exists ? '' : ` <span class="badge bg-secondary" title="${esc(t('backups.speakerDeleted'))}">${esc(t('backups.archived'))}</span>`}</td>
            <td class="col-secondary">${esc(b.speaker_ip || '—')}</td>
            <td>${esc(b.format)}</td>
            <td class="col-secondary">${(b.size_bytes / 1024).toFixed(1)} KB</td>
            <td class="col-secondary">${esc(b.created_by_name || '—')}</td>
            <td class="text-end table-actions">${downloadLink(b.id)}${actionButton('delete', b.id, 'bi-trash', 'action.discard', 'btn-outline-danger')}</td>
        </tr>`).join('') || `<tr><td colspan="7" class="text-muted">${esc(t('empty.backups'))}</td></tr>`;
}

async function loadSpeakerBackups(speakerId) {
    speakerBackups = await api(`/api/speakers/${speakerId}/backups`);
    renderSpeakerBackups();
}

export async function loadArchive() {
    await run(async () => {
        archive = await api('/api/backups');
        renderArchive();
    });
}

export function openBackups(speakerId) {
    const s = state.speakers.find(x => x.id === speakerId);
    $('backups-speaker-id').value = speakerId;
    $('backups-speaker-name').textContent = s ? s.name : '';
    speakerBackups = [];
    renderSpeakerBackups();
    modal('backups-modal').show();
    run(() => loadSpeakerBackups(speakerId));
}

async function deleteBackup(id, btn, reload) {
    if (!confirm(t('confirm.deleteBackup'))) return;
    await run(async () => {
        await api(`/api/backups/${id}`, { method: 'DELETE' });
        await reload();
    }, { button: btn });
}

export function init() {
    $('backups-create-btn').addEventListener('click', (e) => run(async () => {
        const speakerId = Number($('backups-speaker-id').value);
        await api(`/api/speakers/${speakerId}/backups`, { method: 'POST' });
        await loadSpeakerBackups(speakerId);
    }, { button: e.currentTarget, success: t('toast.backupCreated') }));
    onAction($('backups-body'), {
        delete: (id, btn) => deleteBackup(id, btn, () => loadSpeakerBackups(Number($('backups-speaker-id').value))),
    });
    onAction($('backup-archive-body'), {
        delete: (id, btn) => deleteBackup(id, btn, loadArchive),
    });
    onTabShown('backuparchive', loadArchive);
}

export function rerender() {
    renderArchive();
    if ($('backups-modal').classList.contains('show')) renderSpeakerBackups();
}
