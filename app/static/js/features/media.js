// Audio library: upload, analysis badges, amplification, download, delete.
import { api, upload } from '../lib/api.js';
import { $, actionButton, esc, onAction, run, toast, withBusy } from '../lib/dom.js';
import { fmtDateTime } from '../lib/format.js';
import { makeSortable, sortedRows } from '../lib/sort.js';
import { onDataChange, refresh, state } from '../state.js';

// Thresholds match models.py's Media properties: >=50% bass is flagged
// red (may sound weak on a PA horn), 30-49% amber, below that green.
function frequencyNote(m) {
    if (m.band_low_pct === null || m.band_low_pct === undefined) return '';
    if (m.low_freq_warning) return t('media.freqDominant', { pct: m.band_low_pct.toFixed(0) });
    if (m.band_low_pct >= 30) return t('media.freqSignificant', { pct: m.band_low_pct.toFixed(0) });
    return t('media.freqGood', { low: m.band_low_pct.toFixed(0), mid: m.band_mid_pct.toFixed(0), high: m.band_high_pct.toFixed(0) });
}
function headroomNote(m) {
    if (m.peak_db === null || m.peak_db === undefined) return '';
    if (m.suggested_gain_db) return t('media.headroomCanAmplify', { peak: m.peak_db.toFixed(1), gain: m.suggested_gain_db.toFixed(1) });
    return t('media.headroomNearMax', { peak: m.peak_db.toFixed(1) });
}

function analysisCell(m) {
    if (m.band_low_pct === null || m.band_low_pct === undefined) return '<span class="text-muted small">—</span>';
    const cls = m.low_freq_warning ? 'bg-danger' : (m.band_low_pct >= 30 ? 'bg-warning text-dark' : 'bg-success');
    const bands = `${t('media.bandLow')} ${m.band_low_pct.toFixed(0)}% · ${t('media.bandMid')} ${m.band_mid_pct.toFixed(0)}% · ${t('media.bandHigh')} ${m.band_high_pct.toFixed(0)}%`;
    const parts = [`<span class="badge ${cls}" title="${esc(frequencyNote(m))}" data-bs-toggle="tooltip">${esc(bands)}</span>`];
    if (m.suggested_gain_db) {
        const label = esc(t('media.amplify', { gain: m.suggested_gain_db.toFixed(1) }));
        parts.push(`<button type="button" class="btn btn-sm btn-outline-primary ms-1" data-action="amplify" data-id="${m.id}" title="${esc(headroomNote(m))}">${label}</button>`);
    } else if (m.normalized) {
        parts.push(`<span class="badge bg-secondary ms-1" title="${esc(headroomNote(m))}" data-bs-toggle="tooltip">${esc(t('media.amplified'))}</span>`);
    }
    return `<div class="small">${parts.join(' ')}</div>`;
}

const SORT = {
    name: m => m.original_filename,
    duration: m => m.duration_seconds,
    size: m => m.size_bytes,
    date: m => m.uploaded_at,
};

export function render() {
    const body = $('media-body');
    body.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => bootstrap.Tooltip.getInstance(el)?.dispose());
    const label = esc(t('action.download'));
    body.innerHTML = sortedRows('media', state.media, SORT).map(m => `
        <tr>
            <td>${esc(m.original_filename)}</td>
            <td>${m.duration_seconds.toFixed(1)}s</td>
            <td class="col-secondary">${(m.size_bytes / 1024 / 1024).toFixed(2)} MB</td>
            <td class="col-secondary">${fmtDateTime(m.uploaded_at)}</td>
            <td>${analysisCell(m)}</td>
            <td class="text-end table-actions">
                <a class="btn btn-sm btn-outline-secondary" href="/api/media/${m.id}/download" aria-label="${label}" title="${label}"><i class="bi bi-download" aria-hidden="true"></i><span class="btn-label"> ${label}</span></a>
                ${actionButton('delete', m.id, 'bi-trash', 'action.delete', 'btn-outline-danger')}
            </td>
        </tr>`).join('') || `<tr><td colspan="6" class="text-muted">${esc(t('empty.media'))}</td></tr>`;
    body.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => new bootstrap.Tooltip(el));
}

export function init() {
    makeSortable('media', SORT, render);
    onDataChange((changed) => { if (changed.has('media')) render(); });

    $('upload-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const input = $('upload-file');
        if (!input.files.length) return;
        const status = $('upload-status');
        const button = e.currentTarget.querySelector('button[type=submit]');
        withBusy(button, async () => {
            const fd = new FormData();
            fd.append('file', input.files[0]);
            status.textContent = t('toast.uploading');
            try {
                const uploaded = await upload('/api/media/upload', fd);
                input.value = '';
                const notes = [frequencyNote(uploaded), headroomNote(uploaded)].filter(Boolean).join(' ');
                toast(notes ? `${t('toast.fileUploaded')}. ${notes}` : t('toast.fileUploaded'), uploaded.low_freq_warning ? 'warning' : 'success');
                await refresh('media');
            } catch (err) {
                toast(err.message, 'danger');
            } finally {
                status.textContent = '';
            }
        });
    });

    onAction($('media-body'), {
        amplify: (id, btn) => run(async () => {
            await api(`/api/media/${id}/normalize`, { method: 'POST' });
            await refresh('media');
        }, { button: btn, success: t('toast.fileAmplified') }),
        delete: (id, btn) => {
            if (!confirm(t('confirm.deleteMedia'))) return;
            run(async () => {
                await api(`/api/media/${id}`, { method: 'DELETE' });
                await refresh('media');
            }, { button: btn });
        },
    });
}
