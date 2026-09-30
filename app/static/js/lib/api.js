// HTTP to the ZoneCast API. Errors come back as ApiError with the
// message already in the viewer's language (formatApiError, i18n.js) and
// the raw `detail` kept for callers that need its code or params.

export class ApiError extends Error {
    constructor(message, status, detail) {
        super(message);
        this.status = status;
        this.detail = detail;
    }
}

async function send(path, init) {
    let res;
    try {
        res = await fetch(path, init);
    } catch (err) {
        throw new ApiError(t('app.loadError'), 0, null);
    }
    if (res.status === 401 && !path.startsWith('/api/auth/login')) {
        window.location.href = '/login';
        throw new ApiError(t('error.auth.not_authenticated'), 401, null);
    }
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new ApiError(formatApiError(body.detail, res.status), res.status, body.detail);
    }
    return res;
}

// Requests the dashboard makes on its own (pollers) are flagged so they
// don't keep the session alive: it expires after a period without real
// use (see SlidingSession in app/main.py).
let backgroundDepth = 0;

export function inBackground(fn) {
    backgroundDepth++;
    try { return fn(); } finally { backgroundDepth--; }
}

export async function api(path, { method = 'GET', body } = {}) {
    const init = { method, headers: {} };
    if (backgroundDepth) init.headers['X-ZoneCast-Background'] = '1';
    if (body !== undefined) {
        init.headers['Content-Type'] = 'application/json';
        init.body = JSON.stringify(body);
    }
    const res = await send(path, init);
    if (res.status === 204) return null;
    return res.json();
}

export async function upload(path, formData) {
    const res = await send(path, { method: 'POST', body: formData });
    return res.json();
}

// POSTs JSON and saves the response body as a file (full export).
export async function download(path, body, fallbackName) {
    const res = await send(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const blob = await res.blob();
    const match = (res.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = match ? match[1] : fallbackName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}
