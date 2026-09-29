// Schedules: table and the create/edit dialog.
import { api } from '../lib/api.js';
import { $, actionButton, esc, fillSelect, modal, onAction, run, toast } from '../lib/dom.js';
import { fmtWallClock } from '../lib/format.js';
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

const calendarName = (id) => state.calendars.find(c => c.id === id)?.name || `#${id}`;

function datesLabel(s) {
    const names = (mode) => s.calendars.filter(r => r.mode === mode).map(r => calendarName(r.calendar_id)).join(', ');
    const parts = [];
    if (names('exclude')) parts.push(t('schedules.datesSkip', { names: names('exclude') }));
    if (names('only')) parts.push(t('schedules.datesOnly', { names: names('only') }));
    return parts.join(' · ');
}

// Schedules that already play on the same speakers at the same time
// (saved before the check, or made so by zone members / custom dates).
function overlapIcon(s) {
    const others = state.overlaps
        .filter(p => p.a_id === s.id || p.b_id === s.id)
        .map(p => (p.a_id === s.id ? p.b_name : p.a_name));
    if (!others.length) return '';
    const text = esc(t('schedules.overlapIcon', { names: others.join(', ') }));
    return ` <i class="bi bi-exclamation-triangle-fill text-warning" title="${text}" aria-hidden="true"></i><span class="visually-hidden">${text}</span>`;
}

function enabledCell(s) {
    const label = esc(s.enabled ? t('common.active') : t('schedules.paused'));
    return `<span aria-hidden="true" title="${label}">${s.enabled ? '✅' : '⏸️'}</span><span class="visually-hidden">${label}</span>`;
}

export function render() {
    $('schedules-body').innerHTML = sortedRows('schedules', state.schedules, SORT).map(s => `
        <tr>
            <td>${esc(s.name)}${overlapIcon(s)}</td>
            <td class="col-secondary">${esc(mediaName(s.media_id) || s.media_id)}</td>
            <td>${targetBadge(s)}</td>
            <td>${esc(s.time_of_day.slice(0, 5))}</td>
            <td class="col-secondary">${esc(s.days_of_week.split(',').map(d => t(`schedules.${d}`)).join(' '))}</td>
            <td class="col-secondary">${esc(holidayLabel(s))}${s.calendars.length ? `<div class="small text-muted">${esc(datesLabel(s))}</div>` : ''}</td>
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

// One select per custom-dates list: not used / skip its days / only its days.
function renderCalendarRules(rules = currentRules()) {
    const box = $('schedule-calendars');
    if (!state.calendars.length) {
        box.innerHTML = `<div class="form-text mt-0">${esc(t('schedules.noCalendars'))}</div>`;
        return;
    }
    const byId = new Map(rules.map(r => [r.calendar_id, r.mode]));
    box.innerHTML = state.calendars.map(c => {
        const id = `schedule-calendar-${Number(c.id)}`;
        const mode = byId.get(c.id) || '';
        const opt = (value, key) => `<option value="${value}"${mode === value ? ' selected' : ''}>${esc(t(key))}</option>`;
        return `<div class="row g-2 align-items-center mb-1">
            <label class="col-6 col-form-label col-form-label-sm text-break" for="${id}">${esc(c.name)}</label>
            <div class="col-6"><select class="form-select form-select-sm" id="${id}" data-calendar="${Number(c.id)}">
                ${opt('', 'schedules.calendarUnused')}${opt('exclude', 'schedules.calendarExclude')}${opt('only', 'schedules.calendarOnly')}
            </select></div>
        </div>`;
    }).join('');
}

function currentRules() {
    return [...document.querySelectorAll('#schedule-calendars select')]
        .filter(sel => sel.value)
        .map(sel => ({ calendar_id: Number(sel.dataset.calendar), mode: sel.value }));
}

function clearConflicts() {
    $('schedule-conflicts').classList.add('d-none');
    $('schedule-conflicts').replaceChildren();
}

// The save was refused: say which schedules it would play over, inside
// the dialog (a toast would vanish while the user fixes the form).
function showConflicts(params) {
    const box = $('schedule-conflicts');
    const node = (tag, className, text) => {
        const el = document.createElement(tag);
        if (className) el.className = className;
        if (text !== undefined) el.textContent = text;
        return el;
    };
    const list = node('ul', 'mb-1 ps-3');
    for (const c of params.conflicts) {
        const item = node('li', 'mb-1');
        const target = c.target_type === 'all' ? t('common.allSpeakers')
            : (c.target_type === 'zone' ? `${t('common.zone')}: ${c.target_label}` : c.target_label);
        const days = c.days.map(d => t(`schedules.${d}`)).join(' ');
        item.append(node('span', 'fw-medium', c.name), document.createTextNode(` — ${target}, ${c.time} (${days}). `),
            node('span', '', t('schedules.overlapFirst', { when: fmtWallClock(c.first_clash) })));
        if (c.shared_count) {
            const more = c.shared_count > c.shared_speakers.length ? ', …' : '';
            item.append(node('div', 'small', t('schedules.overlapShared', { n: c.shared_count, names: c.shared_speakers.join(', ') + more })));
        }
        const open = node('button', 'btn btn-link btn-sm p-0 align-baseline', t('schedules.openSchedule'));
        open.type = 'button';
        open.addEventListener('click', () => {
            if (!confirm(t('confirm.openOtherSchedule'))) return;
            // It may have been created by someone else since this page loaded.
            run(async () => {
                await refresh('schedules', 'calendars', 'overlaps');
                if (!state.schedules.some(x => x.id === c.schedule_id)) throw new Error(t('error.schedules.not_found'));
                edit(c.schedule_id);
            }, { button: open });
        });
        item.append(document.createTextNode(' '), open);
        list.append(item);
    }
    box.replaceChildren(node('div', 'fw-medium mb-1', t('schedules.overlapTitle')), list,
        node('div', 'small', t('schedules.overlapHint')));
    box.classList.remove('d-none');
    box.scrollIntoView({ block: 'nearest' });
}

function resetForm() {
    $('schedule-form').reset();
    $('schedule-id').value = '';
    document.querySelectorAll('#schedule-days input').forEach(c => { c.checked = DEFAULT_DAYS.includes(c.value); });
    $('schedule-holiday-mode').value = 'none';
    $('schedule-holiday-country').value = 'IT';
    refreshTargetOptions('schedule');
    renderCalendarRules([]);
    clearConflicts();
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
    renderCalendarRules(s.calendars);
    clearConflicts();
    modal('schedule-modal').show();
}

export function init() {
    makeSortable('schedules', SORT, render);
    onDataChange((changed) => {
        if (['schedules', 'media', 'zones', 'speakers', 'calendars', 'overlaps'].some(n => changed.has(n))) render();
        if (changed.has('media')) renderMediaSelect();
        if (changed.has('calendars')) renderCalendarRules();
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
            calendars: currentRules(),
        };
        clearConflicts();
        run(async () => {
            try {
                await api(id ? `/api/schedules/${id}` : '/api/schedules', { method: id ? 'PUT' : 'POST', body: payload });
            } catch (err) {
                if (!err.detail || err.detail.code !== 'schedules.overlap') throw err;
                showConflicts(err.detail.params);
                return false;
            }
            modal('schedule-modal').hide();
            await refresh('schedules', 'calendars', 'overlaps');
            return true;
        }, { button: e.submitter, success: (saved) => (saved ? t('toast.scheduleSaved') : null) });
    });

    onAction($('schedules-body'), {
        edit: (id) => edit(id),
        delete: (id, btn) => {
            if (!confirm(t('confirm.deleteSchedule'))) return;
            run(async () => {
                await api(`/api/schedules/${id}`, { method: 'DELETE' });
                await refresh('schedules', 'calendars', 'overlaps');
            }, { button: btn });
        },
    });
}
