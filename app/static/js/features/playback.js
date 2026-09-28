// "Play now" and the playback history.
import { api } from '../lib/api.js';
import { $, actionButton, esc, fillSelect, isVisible, onAction, run } from '../lib/dom.js';
import { fmtDateTime } from '../lib/format.js';
import { poller } from '../lib/poller.js';
import { mediaName, onDataChange, state } from '../state.js';
import { onTabShown } from './nav.js';

const STATUS_BADGE = { running: 'bg-primary', completed: 'bg-success', failed: 'bg-danger', stopped: 'bg-secondary' };
const IDLE_POLL_MS = 15000;
const ACTIVE_POLL_MS = 3000;  // while something is playing, so "completed" shows up promptly
let history = [];
const historyPoller = poller(loadHistory, IDLE_POLL_MS, { when: () => isVisible($('tab-play')) });

function targetLabel(r) {
    if (r.target_type === 'all') return t('common.allSpeakers');
    if (r.target_type === 'zone') return `${t('common.zone')}: ${r.target_label}`;
    return r.target_label;
}

function statusCell(r) {
    const label = esc(t(`playstatus.${r.status}`));
    const reason = r.status === 'failed' && r.error_message ? t('play.reason', { reason: r.error_message }) : '';
    const title = reason ? ` title="${esc(reason)}"` : '';
    const info = reason ? ` <i class="bi bi-info-circle" aria-hidden="true"></i><span class="visually-hidden">${esc(reason)}</span>` : '';
    return `<span class="badge ${STATUS_BADGE[r.status] || 'bg-secondary'}"${title}>${label}${info}</span>`;
}

export function renderHistory() {
    $('history-body').innerHTML = history.map(r => `
        <tr>
            <td>${fmtDateTime(r.started_at)}</td>
            <td>${esc(mediaName(r.media_id) || r.media_id)}</td>
            <td>${esc(targetLabel(r))}</td>
            <td class="col-secondary">${r.source === 'schedule' ? esc(t('play.sourceScheduled')) : `${esc(t('play.sourceManual'))}${r.triggered_by_name ? ' — ' + esc(r.triggered_by_name) : ''}`}</td>
            <td>${statusCell(r)}</td>
            <td>${r.status === 'running' ? actionButton('stop', r.id, 'bi-stop-circle', 'action.stop', 'btn-outline-danger') : ''}</td>
        </tr>`).join('') || `<tr><td colspan="6" class="text-muted">${esc(t('empty.playbacks'))}</td></tr>`;
}

export async function loadHistory() {
    try {
        history = await api('/api/playback/history?limit=30');
    } catch (err) {
        return;  // transient (e.g. a restart): the next poll retries
    }
    renderHistory();
    historyPoller.setInterval(history.some(r => r.status === 'running') ? ACTIVE_POLL_MS : IDLE_POLL_MS);
}

export function refreshTargetOptions(prefix) {
    const type = $(`${prefix}-target-type`).value;
    $(`${prefix}-target-id-wrap`).classList.toggle('d-none', type === 'all');
    if (type === 'all') return;
    fillSelect($(`${prefix}-target-id`), type === 'zone' ? state.zones : state.speakers, { label: i => i.name });
}

export function renderPickers() {
    fillSelect($('play-media'), state.media, { label: m => m.original_filename });
    refreshTargetOptions('play');
}

export function init() {
    $('play-target-type').addEventListener('change', () => refreshTargetOptions('play'));
    $('play-btn').addEventListener('click', (e) => run(async () => {
        const type = $('play-target-type').value;
        await api('/api/playback/play', {
            method: 'POST',
            body: {
                media_id: Number($('play-media').value),
                target_type: type,
                target_id: type === 'all' ? null : Number($('play-target-id').value),
            },
        });
        await loadHistory();
    }, { button: e.currentTarget, success: t('toast.playbackStarted') }));
    $('refresh-history').addEventListener('click', (e) => run(loadHistory, { button: e.currentTarget }));
    onAction($('history-body'), {
        stop: (id, btn) => run(async () => {
            await api(`/api/playback/stop/${id}`, { method: 'POST' });
            await loadHistory();
        }, { button: btn, success: t('toast.playbackStopped') }),
    });
    onDataChange((changed) => {
        if (changed.has('media') || changed.has('zones') || changed.has('speakers')) renderPickers();
        if (changed.has('media')) renderHistory();
    });
    onTabShown('play', loadHistory);
    historyPoller.start();
}
