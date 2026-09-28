// Zones: table and create/edit/delete.
import { api } from '../lib/api.js';
import { $, actionButton, esc, modal, onAction, run } from '../lib/dom.js';
import { makeSortable, sortedRows } from '../lib/sort.js';
import { onDataChange, refresh, state } from '../state.js';

const speakerCount = (zoneId) => state.speakers.filter(s => s.zone_id === zoneId).length;

const SORT = {
    name: z => z.name,
    description: z => z.description || '',
    mcast: z => z.multicast_address,
    count: z => speakerCount(z.id),
};

export function render() {
    $('zones-body').innerHTML = sortedRows('zones', state.zones, SORT).map(z => `
        <tr>
            <td>${esc(z.name)}</td>
            <td class="col-secondary">${esc(z.description)}</td>
            <td class="col-secondary"><code>${esc(z.multicast_address)}:${esc(z.multicast_port)}</code></td>
            <td>${speakerCount(z.id)}</td>
            <td class="table-actions text-end">
                ${actionButton('edit', z.id, 'bi-pencil', 'action.edit', 'btn-outline-primary')}
                ${actionButton('delete', z.id, 'bi-trash', 'action.delete', 'btn-outline-danger')}
            </td>
        </tr>`).join('') || `<tr><td colspan="5" class="text-muted">${esc(t('empty.zones'))}</td></tr>`;
}

function resetForm() {
    $('zone-form').reset();
    $('zone-id').value = '';
    $('zone-mcast-port').value = 5004;
}

function edit(id) {
    const z = state.zones.find(x => x.id === id);
    if (!z) return;
    $('zone-id').value = z.id;
    $('zone-name').value = z.name;
    $('zone-description').value = z.description || '';
    $('zone-mcast-addr').value = z.multicast_address;
    $('zone-mcast-port').value = z.multicast_port;
    modal('zone-modal').show();
}

export function init() {
    makeSortable('zones', SORT, render);
    onDataChange((changed) => { if (changed.has('zones') || changed.has('speakers')) render(); });
    $('new-zone-btn').addEventListener('click', resetForm);

    $('zone-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const id = $('zone-id').value;
        const payload = {
            name: $('zone-name').value,
            description: $('zone-description').value,
            multicast_address: $('zone-mcast-addr').value,
            multicast_port: Number($('zone-mcast-port').value),
        };
        run(async () => {
            await api(id ? `/api/zones/${id}` : '/api/zones', { method: id ? 'PUT' : 'POST', body: payload });
            modal('zone-modal').hide();
            await refresh('zones');
        }, { button: e.submitter, success: t('toast.zoneSaved') });
    });

    onAction($('zones-body'), {
        edit: (id) => edit(id),
        delete: (id, btn) => {
            if (!confirm(t('confirm.deleteZone'))) return;
            run(async () => {
                await api(`/api/zones/${id}`, { method: 'DELETE' });
                await refresh('zones', 'speakers');  // members lose their zone
            }, { button: btn });
        },
    });
}
