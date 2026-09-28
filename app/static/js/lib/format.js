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
