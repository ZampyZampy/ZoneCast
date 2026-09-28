// Custom dates: named lists of days (closures, exam days...) schedules
// can skip or be limited to. Editing one can make schedules overlap —
// the save goes through and the overlaps are reported.
import { api } from '../lib/api.js';
import { $, actionButton, esc, modal, onAction, run, toast } from '../lib/dom.js';
import { fmtDay } from '../lib/format.js';
import { onDataChange, refresh, state } from '../state.js';

const PREVIEW = 3;  // dates listed in the table before "+n"
let rowSerial = 0;

function entryLabel(d) {
    const range = d.end_date && d.end_date !== d.start_date
        ? `${fmtDay(d.start_date, d)} – ${fmtDay(d.end_date, d)}`
        : fmtDay(d.start_date, d);
    return `${range}${d.yearly ? ' ↻' : ''}${d.label ? ` (${d.label})` : ''}`;
}

export function render() {
    $('calendars-body').innerHTML = state.calendars.map(c => {
        const shown = c.dates.slice(0, PREVIEW).map(entryLabel).join(', ');
        const more = c.dates.length > PREVIEW ? ` +${c.dates.length - PREVIEW}` : '';
        return `<tr>
            <td>${esc(c.name)}${c.description ? `<div class="small text-muted">${esc(c.description)}</div>` : ''}</td>
            <td>${esc(t('calendars.count', { n: c.dates.length }))}${c.dates.length ? `<div class="small text-muted">${esc(shown)}${esc(more)}</div>` : ''}</td>
            <td class="col-secondary">${esc(t('calendars.usedByCount', { n: c.schedule_count }))}</td>
            <td class="table-actions text-end">
                ${actionButton('edit', c.id, 'bi-pencil', 'action.edit', 'btn-outline-primary')}
                ${actionButton('delete', c.id, 'bi-trash', 'action.delete', 'btn-outline-danger')}
            </td>
        </tr>`;
    }).join('') || `<tr><td colspan="4" class="text-muted">${esc(t('empty.calendars'))}</td></tr>`;
}

// One editable row per date or range. Built with DOM calls: the label is
// free text and goes into .value, never into HTML.
function addRow(entry = {}) {
    const n = ++rowSerial;
    const row = document.createElement('div');
    row.className = 'calendar-entry';
    const input = (field, type, labelKey) => {
        const el = document.createElement('input');
        el.type = type;
        el.className = 'form-control form-control-sm';
        el.dataset.field = field;
        el.setAttribute('aria-label', t(labelKey));
        return el;
    };
    const start = input('start', 'date', 'calendars.from');
    start.value = entry.start_date || '';
    const end = input('end', 'date', 'calendars.to');
    end.value = entry.end_date && entry.end_date !== entry.start_date ? entry.end_date : '';
    const label = input('label', 'text', 'calendars.label');
    label.placeholder = t('calendars.label');
    label.maxLength = 128;
    label.value = entry.label || '';
    const yearlyWrap = document.createElement('div');
    yearlyWrap.className = 'form-check m-0';
    const yearly = document.createElement('input');
    yearly.type = 'checkbox';
    yearly.className = 'form-check-input';
    yearly.id = `calendar-yearly-${n}`;
    yearly.dataset.field = 'yearly';
    yearly.checked = !!entry.yearly;
    const yearlyLabel = document.createElement('label');
    yearlyLabel.className = 'form-check-label small';
    yearlyLabel.htmlFor = yearly.id;
    yearlyLabel.textContent = t('calendars.yearly');
    yearlyWrap.append(yearly, yearlyLabel);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'btn btn-sm btn-outline-danger';
    remove.dataset.remove = '';
    remove.setAttribute('aria-label', t('calendars.remove'));
    remove.title = t('calendars.remove');
    remove.innerHTML = '<i class="bi bi-x-lg" aria-hidden="true"></i>';
    row.append(start, end, label, yearlyWrap, remove);
    $('calendar-entries').append(row);
    updateCount();
    return row;
}

function updateCount() {
    $('calendar-entry-count').textContent = $('calendar-entries').children.length;
}

function readRows() {
    return [...$('calendar-entries').children].map(row => {
        const get = (f) => row.querySelector(`[data-field="${f}"]`);
        return {
            start_date: get('start').value,
            end_date: get('end').value || null,
            label: get('label').value.trim(),
            yearly: get('yearly').checked,
        };
    }).filter(e => e.start_date || e.end_date || e.label);
}

// "2026-12-24", "24/12/2026", "2026-12-24 2027-01-06 Christmas": a date or
// a range per line, anything else on the line becomes the label.
const DATE_RE = /(\d{4})-(\d{1,2})-(\d{1,2})|(\d{1,2})[/.](\d{1,2})[/.](\d{4})/g;

function parseLine(line) {
    const dates = [];
    for (const m of line.matchAll(DATE_RE)) {
        const [y, mo, d] = m[1] ? [m[1], m[2], m[3]] : [m[6], m[5], m[4]];
        const iso = `${y}-${mo.padStart(2, '0')}-${d.padStart(2, '0')}`;
        if (Number.isNaN(Date.parse(iso))) return null;
        dates.push(iso);
    }
    if (dates.length < 1 || dates.length > 2) return null;
    const label = line.replace(DATE_RE, '').replace(/^[\s\-–—:.,;]+|[\s\-–—:.,;]+$/g, '').replace(/\s*[-–—]\s*/, ' ').trim();
    return { start_date: dates[0], end_date: dates[1] || null, label: label.slice(0, 128), yearly: false };
}

function pasteDates() {
    const bad = [];
    for (const line of $('calendar-paste').value.split(/\r?\n/).map(l => l.trim()).filter(Boolean)) {
        const entry = parseLine(line);
        if (entry) addRow(entry); else bad.push(line);
    }
    $('calendar-paste').value = bad.join('\n');
    if (bad.length) toast(t('calendars.pasteInvalid', { lines: bad.slice(0, 3).join('; ') }), 'warning');
}

function openForm(cal) {
    $('calendar-form').reset();
    $('calendar-entries').replaceChildren();
    $('calendar-id').value = cal ? cal.id : '';
    if (cal) {
        $('calendar-name').value = cal.name;
        $('calendar-description').value = cal.description || '';
        cal.dates.forEach(addRow);
    } else {
        addRow();
    }
    updateCount();
}

export function reportOverlaps(warnings) {
    if (!warnings || !warnings.length) return;
    const w = warnings[0];
    toast(t('toast.overlapsCreated', { count: warnings.length, first: `${w.a_name} ↔ ${w.b_name}` }), 'warning');
}

export function init() {
    onDataChange((changed) => { if (changed.has('calendars')) render(); });
    $('new-calendar-btn').addEventListener('click', () => openForm(null));
    $('calendar-add-entry').addEventListener('click', () => addRow().querySelector('input').focus());
    $('calendar-paste-add').addEventListener('click', pasteDates);
    $('calendar-entries').addEventListener('click', (e) => {
        const btn = e.target.closest('[data-remove]');
        if (!btn) return;
        btn.closest('.calendar-entry').remove();
        updateCount();
    });

    $('calendar-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const id = $('calendar-id').value;
        const dates = readRows();
        if (dates.some(d => !d.start_date)) { toast(t('calendars.startMissing'), 'danger'); return; }
        const payload = { name: $('calendar-name').value, description: $('calendar-description').value, dates };
        run(async () => {
            const saved = await api(id ? `/api/calendars/${id}` : '/api/calendars', { method: id ? 'PUT' : 'POST', body: payload });
            modal('calendar-modal').hide();
            await refresh('calendars', 'schedules', 'overlaps');
            reportOverlaps(saved.warnings);
        }, { button: e.submitter, success: t('toast.calendarSaved') });
    });

    onAction($('calendars-body'), {
        edit: (id) => {
            const cal = state.calendars.find(c => c.id === id);
            if (!cal) return;
            openForm(cal);
            modal('calendar-modal').show();
        },
        delete: (id, btn) => {
            if (!confirm(t('confirm.deleteCalendar'))) return;
            run(async () => {
                await api(`/api/calendars/${id}`, { method: 'DELETE' });
                await refresh('calendars');
            }, { button: btn });
        },
    });
}
