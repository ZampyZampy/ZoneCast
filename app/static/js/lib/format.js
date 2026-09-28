// Locale-aware formatting, following the language picked in the
// dashboard rather than the browser's own locale.

// The API returns naive UTC timestamps (datetime.utcnow(), no offset),
// which `new Date()` would otherwise parse as *local* time — shifting
// every displayed time by the viewer's UTC offset.
export function fmtDateTime(iso) {
    if (!iso) return '—';
    const hasOffset = /(Z|[+-]\d{2}:?\d{2})$/i.test(iso);
    return new Date(hasOffset ? iso : `${iso}Z`).toLocaleString(getLanguage());
}

export function compareValues(a, b) {
    if (typeof a === 'number' && typeof b === 'number') return a - b;
    return String(a ?? '').localeCompare(String(b ?? ''), getLanguage(), { numeric: true, sensitivity: 'base' });
}

export const fmtGiB = (bytes) => (bytes / (1024 ** 3)).toFixed(1);

// A calendar day ("2026-12-24") or a wall-clock time on the scheduler's
// clock ("2026-12-24T08:00"): shown as written, never shifted to the
// viewer's timezone.
function wallClock(iso) {
    const [d, time = '00:00'] = iso.split('T');
    const [y, m, day] = d.split('-').map(Number);
    const [hh, mm] = time.split(':').map(Number);
    return new Date(Date.UTC(y, m - 1, day, hh, mm));
}

export function fmtDay(iso, { yearly = false } = {}) {
    if (!iso) return '';
    const opts = { timeZone: 'UTC', day: 'numeric', month: 'short' };
    if (!yearly) opts.year = 'numeric';
    return wallClock(iso).toLocaleDateString(getLanguage(), opts);
}

export function fmtWallClock(iso) {
    if (!iso) return '';
    return wallClock(iso).toLocaleString(getLanguage(), {
        timeZone: 'UTC', weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
    });
}
