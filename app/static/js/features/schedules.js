// Schedules: table and the create/edit dialog.
import { api } from '../lib/api.js';
import { $, actionButton, esc, fillSelect, modal, onAction, run, toast } from '../lib/dom.js';
import { makeSortable, sortedRows } from '../lib/sort.js';
import { mediaName, onDataChange, refresh, speakerName, state, zoneName } from '../state.js';
import { refreshTargetOptions } from './playback.js';

const DEFAULT_DAYS = ['mon', 'tue', 'wed', 'thu', 'fri'];

const SORT = {
    name: s => s.name,
    media: s => mediaName(s.media_id) || '',
    time: s => s.time_of_day,
    enabled: s => (s.enabled ? 1 : 0),
};

function targetBadge(s) {
    if (s.target_type === 'all') return `<span class="badge bg-primary badge-target">${esc(t('common.allSpeakers'))}</span>`;
    if (s.target_type === 'zone') return `<span class="badge bg-info badge-target">${esc(t('common.zone'))}: ${esc(zoneName(s.target_id) || s.target_id)}</span>`;
    return `<span class="badge bg-secondary badge-target">${esc(speakerName(s.target_id) || s.target_id)}</span>`;
}

function holidayLabel(s) {
    if (!s.holidays_only && !s.exclude_holidays) return t('schedules.holidayNone');
    const mode = s.holidays_only ? t('schedules.holidayOnly') : t('schedules.holidayExclude');
    return `${mode} (${s.holiday_country || 'IT'})`;
}

function enabledCell(s) {
    const label = esc(s.enabled ? t('common.active') : t('schedules.paused'));
    return `<span aria-hidden="true" title="${label}">${s.enabled ? '✅' : '⏸️'}</span><span class="visually-hidden">${label}</span>`;
}

export function render() {
    $('schedules-body').innerHTML = sortedRows('schedules', state.schedules, SORT).map(s => `
        <tr>
            <td>${esc(s.name)}</td>
            <td class="col-secondary">${esc(mediaName(s.media_id) || s.media_id)}</td>
            <td>${targetBadge(s)}</td>
            <td>${esc(s.time_of_day.slice(0, 5))}</td>
            <td class="col-secondary">${esc(s.days_of_week.split(',').map(d => t(`schedules.${d}`)).join(' '))}</td>
            <td class="col-secondary">${esc(holidayLabel(s))}</td>
            <td>${enabledCell(s)}</td>
            <td class="table-actions text-end">
                ${actionButton('edit', s.id, 'bi-pencil', 'action.edit', 'btn-outline-primary')}
                ${actionButton('delete', s.id, 'bi-trash', 'action.delete', 'btn-outline-danger')}
            </td>
        </tr>`).join('') || `<tr><td colspan="8" class="text-muted">${esc(t('empty.schedules'))}</td></tr>`;
}

function renderMediaSelect() {
    fillSelect($('schedule-media'), state.media, { label: m => m.original_filename });
}

function resetForm() {
    $('schedule-form').reset();
    $('schedule-id').value = '';
    document.querySelectorAll('#schedule-days input').forEach(c => { c.checked = DEFAULT_DAYS.includes(c.value); });
    $('schedule-holiday-mode').value = 'none';
    $('schedule-holiday-country').value = 'IT';
    refreshTargetOptions('schedule');
}

function edit(id) {
    const s = state.schedules.find(x => x.id === id);
    if (!s) return;
    $('schedule-id').value = s.id;
    $('schedule-name').value = s.name;
    $('schedule-media').value = s.media_id;
    $('schedule-target-type').value = s.target_type;
    refreshTargetOptions('schedule');
    if (s.target_id) $('schedule-target-id').value = s.target_id;
    $('schedule-time').value = s.time_of_day.slice(0, 5);
    $('schedule-start-date').value = s.start_date || '';
    $('schedule-end-date').value = s.end_date || '';
    const days = s.days_of_week.split(',');
    document.querySelectorAll('#schedule-days input').forEach(c => { c.checked = days.includes(c.value); });
    $('schedule-holiday-mode').value = s.holidays_only ? 'only' : (s.exclude_holidays ? 'exclude' : 'none');
    $('schedule-holiday-country').value = s.holiday_country || 'IT';
    $('schedule-enabled').checked = s.enabled;
    modal('schedule-modal').show();
}

export function init() {
    makeSortable('schedules', SORT, render);
    onDataChange((changed) => {
        if (['schedules', 'media', 'zones', 'speakers'].some(n => changed.has(n))) render();
        if (changed.has('media')) renderMediaSelect();
        if (changed.has('zones') || changed.has('speakers')) refreshTargetOptions('schedule');
    });
    $('schedule-target-type').addEventListener('change', () => refreshTargetOptions('schedule'));
    $('new-schedule-btn').addEventListener('click', resetForm);

    $('schedule-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const id = $('schedule-id').value;
        const type = $('schedule-target-type').value;
        const days = [...document.querySelectorAll('#schedule-days input:checked')].map(c => c.value);
        if (!days.length) { toast(t('toast.selectDay'), 'danger'); return; }
        const holidayMode = $('schedule-holiday-mode').value;
        const payload = {
            name: $('schedule-name').value,
            media_id: Number($('schedule-media').value),
            target_type: type,
            target_id: type === 'all' ? null : Number($('schedule-target-id').value),
            time_of_day: `${$('schedule-time').value}:00`,
            days_of_week: days.join(','),
            start_date: $('schedule-start-date').value || null,
            end_date: $('schedule-end-date').value || null,
            exclude_holidays: holidayMode === 'exclude',
            holidays_only: holidayMode === 'only',
            holiday_country: $('schedule-holiday-country').value,
            enabled: $('schedule-enabled').checked,
        };
        run(async () => {
            await api(id ? `/api/schedules/${id}` : '/api/schedules', { method: id ? 'PUT' : 'POST', body: payload });
            modal('schedule-modal').hide();
            await refresh('schedules');
        }, { button: e.submitter, success: t('toast.scheduleSaved') });
    });

    onAction($('schedules-body'), {
        edit: (id) => edit(id),
        delete: (id, btn) => {
            if (!confirm(t('confirm.deleteSchedule'))) return;
            run(async () => {
                await api(`/api/schedules/${id}`, { method: 'DELETE' });
                await refresh('schedules');
            }, { button: btn });
        },
    });
}
