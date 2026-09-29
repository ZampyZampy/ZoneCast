// Zones: table, create/edit/delete, and the zone's member speakers — a
// speaker can be in several zones, and membership is managed only here.
import { api } from '../lib/api.js';
import { $, actionButton, esc, modal, onAction, run } from '../lib/dom.js';
import { makeSortable, sortedRows } from '../lib/sort.js';
import { onDataChange, refresh, speakerName, state } from '../state.js';
import { reportOverlaps } from './calendars.js';

const MEMBER_PREVIEW = 3;  // names shown under the count in the table

const SORT = {
    name: z => z.name,
    description: z => z.description || '',
    mcast: z => z.multicast_address,
    count: z => z.speaker_ids.length,
};

// The checklist's own state: survives filtering and re-renders while the
// modal is open, and is only sent to the server on save.
let selected = new Set();
let editingId = null;

function membersCell(z) {
    const names = z.speaker_ids.map(speakerName).filter(Boolean);
    const shown = names.slice(0, MEMBER_PREVIEW).join(', ');
    const more = names.length > MEMBER_PREVIEW ? ` +${names.length - MEMBER_PREVIEW}` : '';
    return `${z.speaker_ids.length}${names.length ? `<div class="small text-muted">${esc(shown)}${esc(more)}</div>` : ''}`;
}

export function render() {
    $('zones-body').innerHTML = sortedRows('zones', state.zones, SORT).map(z => `
        <tr>
            <td>${esc(z.name)}</td>
            <td class="col-secondary">${esc(z.description)}</td>
            <td class="col-secondary"><code>${esc(z.multicast_address)}:${esc(z.multicast_port)}</code></td>
            <td>${membersCell(z)}</td>
            <td class="table-actions text-end">
                ${actionButton('edit', z.id, 'bi-pencil', 'action.edit', 'btn-outline-primary')}
                ${actionButton('delete', z.id, 'bi-trash', 'action.delete', 'btn-outline-danger')}
            </td>
        </tr>`).join('') || `<tr><td colspan="5" class="text-muted">${esc(t('empty.zones'))}</td></tr>`;
    if ($('zone-modal').classList.contains('show')) renderMembers();
}

// Zones a speaker belongs to other than the one being edited.
const otherZones = (s) => s.zone_ids.filter(id => id !== editingId).length;
const atLimit = (s) => s.max_zones != null && otherZones(s) >= s.max_zones;

function memberRow(s) {
    const id = Number(s.id);
    const checked = selected.has(id);
    const disabled = !checked && atLimit(s);
    const others = otherZones(s);
    const detail = [s.ip_address, s.location].filter(Boolean).join(' · ');
    const note = disabled ? t('zones.speakerFull', { max: s.max_zones })
        : (others ? t('zones.inOtherZones', { n: others }) : '');
    const pending = s.paging_sync_ok === false && s.paging_sync_error === 'pending';
    const sync = s.paging_sync_ok === false
        ? ` <span class="badge ${pending ? 'text-bg-info' : 'text-bg-warning'}" title="${esc(pending ? t('speakers.syncPendingTitle') : (s.paging_sync_error || ''))}">${esc(t(pending ? 'speakers.syncPending' : 'speakers.outOfSync'))}</span>` : '';
    return `<label class="list-group-item d-flex align-items-center gap-2${disabled ? ' text-muted' : ''}" for="zone-member-${id}">
            <input class="form-check-input m-0 flex-shrink-0" type="checkbox" id="zone-member-${id}" value="${id}"${checked ? ' checked' : ''}${disabled ? ' disabled' : ''}>
            <span class="status-dot status-${esc(s.status)} flex-shrink-0" aria-hidden="true"></span>
            <span class="flex-grow-1 text-break"><span class="fw-medium">${esc(s.name)}</span> <span class="small text-muted">${esc(detail)}</span></span>
            <span class="small text-muted text-end">${esc(note)}</span>${sync}
        </label>`;
}

function matches(s, needle) {
    if (!needle) return true;
    return [s.name, s.ip_address, s.location].some(v => (v || '').toLowerCase().includes(needle));
}

function visibleSpeakers() {
    const needle = $('zone-members-filter').value.trim().toLowerCase();
    const onlySelected = $('zone-members-selected-only').checked;
    return state.speakers.filter(s => matches(s, needle) && (!onlySelected || selected.has(s.id)));
}

function renderMembers() {
    const list = visibleSpeakers();
    $('zone-members-list').innerHTML = list.map(memberRow).join('')
        || `<div class="list-group-item text-muted small">${esc(t(state.speakers.length ? 'zones.noMatch' : 'zones.noSpeakers'))}</div>`;
    $('zone-members-count').textContent = selected.size;
}

function openForm(zone) {
    $('zone-form').reset();
    editingId = zone ? zone.id : null;
    selected = new Set(zone ? zone.speaker_ids : []);
    $('zone-id').value = zone ? zone.id : '';
    $('zone-mcast-port').value = zone ? zone.multicast_port : 5004;
    if (zone) {
        $('zone-name').value = zone.name;
        $('zone-description').value = zone.description || '';
        $('zone-mcast-addr').value = zone.multicast_address;
    }
    renderMembers();
}

function edit(id) {
    const z = state.zones.find(x => x.id === id);
    if (!z) return;
    openForm(z);
    modal('zone-modal').show();
}

function bindMembers() {
    $('zone-members-list').addEventListener('change', (e) => {
        if (!e.target.matches('input[type=checkbox]')) return;
        const id = Number(e.target.value);
        if (e.target.checked) selected.add(id); else selected.delete(id);
        $('zone-members-count').textContent = selected.size;
    });
    $('zone-members-filter').addEventListener('input', renderMembers);
    // Enter in the filter must not submit (and save) the zone half-edited.
    $('zone-members-filter').addEventListener('keydown', (e) => { if (e.key === 'Enter') e.preventDefault(); });
    $('zone-members-selected-only').addEventListener('change', renderMembers);
    $('zone-members-select-shown').addEventListener('click', () => {
        visibleSpeakers().filter(s => !atLimit(s) || selected.has(s.id)).forEach(s => selected.add(s.id));
        renderMembers();
    });
    $('zone-members-clear').addEventListener('click', () => {
        selected.clear();
        renderMembers();
    });
}

export function init() {
    makeSortable('zones', SORT, render);
    onDataChange((changed) => { if (changed.has('zones') || changed.has('speakers')) render(); });
    $('new-zone-btn').addEventListener('click', () => openForm(null));
    bindMembers();

    $('zone-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const id = $('zone-id').value;
        const payload = {
            name: $('zone-name').value,
            description: $('zone-description').value,
            multicast_address: $('zone-mcast-addr').value,
            multicast_port: Number($('zone-mcast-port').value),
            speaker_ids: [...selected],
        };
        run(async () => {
            const saved = await api(id ? `/api/zones/${id}` : '/api/zones', { method: id ? 'PUT' : 'POST', body: payload });
            modal('zone-modal').hide();
            await refresh('zones', 'speakers', 'overlaps');  // speakers carry their zone_ids
            reportOverlaps(saved.warnings);
        }, { button: e.submitter, success: t('toast.zoneSaved') });
    });

    onAction($('zones-body'), {
        edit: (id) => edit(id),
        delete: (id, btn) => {
            if (!confirm(t('confirm.deleteZone'))) return;
            run(async () => {
                await api(`/api/zones/${id}`, { method: 'DELETE' });
                await refresh('zones', 'speakers');  // members lose the zone
            }, { button: btn });
        },
    });
}
