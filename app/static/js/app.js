const state = { speakers: [], zones: [], media: [], schedules: [], users: [], role: null };

function toast(msg, kind = 'success') {
    const el = document.createElement('div');
    el.className = `alert alert-${kind} shadow`;
    el.textContent = msg;
    document.getElementById('toast-container').appendChild(el);
    setTimeout(() => el.remove(), 4000);
}

async function api(path, opts = {}) {
    const res = await fetch(path, {
        headers: { 'Content-Type': 'application/json' },
        ...opts,
    });
    if (res.status === 401) {
        window.location.href = '/login';
        throw new Error('unauthenticated');
    }
    if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const detail = data.detail;
        const message = typeof detail === 'string' ? detail : (detail && detail.message) || `HTTP ${res.status}`;
        const err = new Error(message);
        err.detail = detail; // may be a structured object (e.g. {message, applied, failed})
        throw err;
    }
    if (res.status === 204) return null;
    return res.json();
}

// The API returns naive UTC timestamps (datetime.utcnow(), no offset),
// which `new Date()` would otherwise parse as *local* time — shifting
// every displayed time by the viewer's UTC offset.
function fmtDateTime(iso) {
    if (!iso) return '—';
    const hasOffset = /(Z|[+-]\d{2}:?\d{2})$/i.test(iso);
    return new Date(hasOffset ? iso : `${iso}Z`).toLocaleString();
}

// ---------- Tabs ----------
document.querySelectorAll('#main-tabs .nav-item').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('#main-tabs .nav-item').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        document.querySelectorAll('.tab-pane').forEach(p => p.classList.add('d-none'));
        document.getElementById(`tab-${btn.dataset.tab}`).classList.remove('d-none');
        document.getElementById('page-title').textContent = btn.querySelector('span')?.textContent || btn.dataset.tab;
        if (btn.dataset.tab === 'logs') { loadLogs(); loadLogRetention(); }
        if (btn.dataset.tab === 'backuparchive') loadBackupArchive();
        closeMobileSidebar();
    });
});

// ---------- Mobile sidebar drawer ----------
const sidebarEl = document.querySelector('.sidebar');
const sidebarBackdropEl = document.getElementById('sidebar-backdrop');
function openMobileSidebar() { sidebarEl.classList.add('mobile-open'); sidebarBackdropEl.classList.add('show'); }
function closeMobileSidebar() { sidebarEl.classList.remove('mobile-open'); sidebarBackdropEl.classList.remove('show'); }
document.getElementById('sidebar-toggle-btn').addEventListener('click', () => {
    sidebarEl.classList.contains('mobile-open') ? closeMobileSidebar() : openMobileSidebar();
});
sidebarBackdropEl.addEventListener('click', closeMobileSidebar);

// ---------- Relocate Password/Sicurezza/Esci and language/theme into the sidebar drawer on mobile ----------
// Moves the actual DOM node (not a clone) so ids stay unique and every
// listener already bound to these buttons/selects keeps working
// regardless of which container currently holds them.
function makeRelocator(el, sidebarSlotEl) {
    const topbarParent = el.parentNode;
    const topbarNextSibling = el.nextSibling;
    return function place() {
        if (window.innerWidth < 768) {
            if (el.parentNode !== sidebarSlotEl) sidebarSlotEl.appendChild(el);
        } else if (el.parentNode !== topbarParent) {
            topbarParent.insertBefore(el, topbarNextSibling);
        }
    };
}
const accountActionsEl = document.getElementById('account-actions');
const placeAccountActions = makeRelocator(accountActionsEl, document.getElementById('sidebar-account-slot'));
const topbarPrefsEl = document.getElementById('topbar-prefs');
const placePrefs = makeRelocator(topbarPrefsEl, document.getElementById('sidebar-prefs-slot'));
function placeRelocatables() { placeAccountActions(); placePrefs(); }
placeRelocatables();
let placeRelocatablesTimer;
window.addEventListener('resize', () => {
    clearTimeout(placeRelocatablesTimer);
    placeRelocatablesTimer = setTimeout(placeRelocatables, 150);
});
accountActionsEl.querySelectorAll('button').forEach(btn => btn.addEventListener('click', closeMobileSidebar));

async function loadVersion() {
    const link = document.getElementById('version-link');
    if (!link) return;
    try {
        const v = await api('/api/system/version');
        link.textContent = `v${v.version}`;
        document.getElementById('changelog-body').innerHTML = v.changelog.map(entry => `
            <div class="mb-3">
                <div class="fw-semibold">v${entry.version} <span class="text-muted small fw-normal">${entry.date}</span></div>
                <ul class="small mb-0">${entry.changes.map(c => `<li>${c}</li>`).join('')}</ul>
            </div>`).join('');
    } catch (e) { link.textContent = '?'; }
}

// Attention-grabbing alerts (failed schedules, low disk) shown once per
// browser session — sessionStorage so they resurface on the next real
// login rather than nagging on every tab switch within the same visit.
async function loadStartupAlerts() {
    const container = document.getElementById('startup-alerts');
    if (!container) return;
    let alreadyShown = false;
    try { alreadyShown = sessionStorage.getItem('zc_alerts_shown') === '1'; } catch (e) { /* private mode — just show them */ }
    if (alreadyShown) return;
    try {
        const items = await api('/api/system/alerts');
        if (!items.length) return;
        container.innerHTML = items.map(a => `
            <div class="alert alert-${a.severity} alert-dismissible fade show" role="alert">
                <i class="bi bi-exclamation-triangle-fill me-2"></i>${a.message}
                <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
            </div>`).join('');
        try { sessionStorage.setItem('zc_alerts_shown', '1'); } catch (e) { /* non-fatal */ }
    } catch (e) { /* non-fatal — don't block the dashboard over this */ }
}

document.getElementById('logout-btn').addEventListener('click', async () => {
    await api('/api/auth/logout', { method: 'POST' });
    window.location.href = '/login';
});

// ---------- Load / render ----------
async function loadAll() {
    const me = await api('/api/auth/me');
    state.role = me.role;
    document.getElementById('current-user').textContent = `${me.full_name || me.username} (${me.role})`;
    if (me.role !== 'admin') {
        document.getElementById('nav-admin-divider')?.remove();
        document.querySelector('[data-tab="users"]')?.remove();
        document.querySelector('[data-tab="system"]')?.remove();
        document.querySelector('[data-tab="logs"]')?.remove();
        document.querySelector('[data-tab="backuparchive"]')?.remove();
    } else {
        await loadSystemTime();
        loadHostResources();
        loadVersion();
        loadStartupAlerts();
    }

    [state.speakers, state.zones, state.media, state.schedules] = await Promise.all([
        api('/api/speakers'), api('/api/zones'), api('/api/media'), api('/api/schedules'),
    ]);
    if (me.role === 'admin') state.users = await api('/api/auth/users');

    renderSpeakers(); renderZones(); renderMedia(); renderSchedules(); renderUsers();
    populateMediaSelects(); populateZoneSelects(); populateTargetPickers();
    await loadHistory();
}

// Tables and labels built here go through t() at render time, so a
// language switch must redraw them — from the data already in `state`,
// without touching the play/schedule pickers the user may have filled.
document.addEventListener('zc:languagechange', () => {
    renderSpeakers(); renderZones(); renderMedia(); renderSchedules(); renderUsers();
    populateZoneSelects();
    loadHistory();
    const activeTab = document.querySelector('#main-tabs .nav-item.active');
    if (activeTab) document.getElementById('page-title').textContent = activeTab.querySelector('span')?.textContent || activeTab.dataset.tab;
    if (state.role === 'admin') {
        // Not loadSystemTime(): that also refills the network form, which
        // would discard an admin's unsaved edits there.
        if (lastTimeStatus) renderTimeStatusLabels(lastTimeStatus);
        if (!document.getElementById('tab-logs').classList.contains('d-none')) loadLogs();
        if (!document.getElementById('tab-backuparchive').classList.contains('d-none')) loadBackupArchive();
    }
});

function statusDot(status) {
    return `<span class="status-dot status-${status}" title="${status}"></span>`;
}
function renderSpeakers() {
    const zoneName = (id) => state.zones.find(z => z.id === id)?.name || '—';
    document.getElementById('speakers-body').innerHTML = state.speakers.map(s => `
        <tr>
            <td>${statusDot(s.status)}<span class="status-dot-label">${s.status}</span></td>
            <td>${s.name}</td>
            <td>${s.ip_address}</td>
            <td class="col-secondary">${zoneName(s.zone_id)}</td>
            <td class="col-secondary"><code>${s.own_multicast_address}:${s.own_multicast_port}</code></td>
            <td class="col-secondary">${[s.brand, s.model].filter(Boolean).join(' ') || '—'}</td>
            <td class="table-actions text-end">
                <button class="btn btn-sm btn-outline-secondary" onclick="pingSpeaker(${s.id}, this)"><i class="bi bi-broadcast"></i><span class="btn-label"> ${t('action.ping')}</span></button>
                <button class="btn btn-sm btn-outline-secondary" onclick="previewMulticast(${s.id})" title="${t('speakers.previewTitle')}"><i class="bi bi-eye"></i><span class="btn-label"> ${t('action.preview')}</span></button>
                ${state.role === 'admin' && s.supports_auto_config
                    ? `<button class="btn btn-sm btn-outline-secondary" onclick="pushMulticast(${s.id}, this)" title="${t('speakers.applyNowTitle')}"><i class="bi bi-cloud-upload"></i><span class="btn-label"> ${t('action.applyNow')}</span></button>`
                    : (state.role === 'admin' ? `<span class="text-muted small fst-italic me-2" title="${t('speakers.manualConfigTitle')}">${t('action.manualConfig')}</span>` : ``)
                }
                ${state.role === 'admin' && s.supports_config_backup
                    ? `<button class="btn btn-sm btn-outline-secondary" onclick="openBackups(${s.id})" title="${t('speakers.backupTitle')}"><i class="bi bi-archive"></i><span class="btn-label"> ${t('action.backup')}</span></button>`
                    : ``
                }
                <button class="btn btn-sm btn-outline-primary" onclick="editSpeaker(${s.id})"><i class="bi bi-pencil"></i><span class="btn-label"> ${t('action.edit')}</span></button>
                <button class="btn btn-sm btn-outline-danger" onclick="deleteSpeaker(${s.id})"><i class="bi bi-trash"></i><span class="btn-label"> ${t('action.delete')}</span></button>
            </td>
        </tr>`).join('') || `<tr><td colspan="7" class="text-muted">${t('empty.speakers')}</td></tr>`;
}

function renderZones() {
    document.getElementById('zones-body').innerHTML = state.zones.map(z => `
        <tr>
            <td>${z.name}</td>
            <td class="col-secondary">${z.description || ''}</td>
            <td class="col-secondary"><code>${z.multicast_address}:${z.multicast_port}</code></td>
            <td>${state.speakers.filter(s => s.zone_id === z.id).length}</td>
            <td class="table-actions text-end">
                <button class="btn btn-sm btn-outline-primary" onclick="editZone(${z.id})"><i class="bi bi-pencil"></i><span class="btn-label"> ${t('action.edit')}</span></button>
                <button class="btn btn-sm btn-outline-danger" onclick="deleteZone(${z.id})"><i class="bi bi-trash"></i><span class="btn-label"> ${t('action.delete')}</span></button>
            </td>
        </tr>`).join('') || `<tr><td colspan="5" class="text-muted">${t('empty.zones')}</td></tr>`;
}

// Built client-side (not from the server's frequency_note/headroom_note
// strings, which are plain Italian text) so both the table tooltip and
// the upload toast follow the selected UI language. Thresholds match
// models.py's Media properties: >=50% bass is flagged red (may sound
// weak on a PA horn), 30-49% amber, below that green.
function mediaFrequencyNote(m) {
    if (m.band_low_pct === null || m.band_low_pct === undefined) return '';
    if (m.low_freq_warning) return t('media.freqDominant', { pct: m.band_low_pct.toFixed(0) });
    if (m.band_low_pct >= 30) return t('media.freqSignificant', { pct: m.band_low_pct.toFixed(0) });
    return t('media.freqGood', { low: m.band_low_pct.toFixed(0), mid: m.band_mid_pct.toFixed(0), high: m.band_high_pct.toFixed(0) });
}
function mediaHeadroomNote(m) {
    if (m.peak_db === null || m.peak_db === undefined) return '';
    if (m.suggested_gain_db) return t('media.headroomCanAmplify', { peak: m.peak_db.toFixed(1), gain: m.suggested_gain_db.toFixed(1) });
    return t('media.headroomNearMax', { peak: m.peak_db.toFixed(1) });
}
function mediaAnalysisCell(m) {
    if (m.band_low_pct === null || m.band_low_pct === undefined) {
        return '<span class="text-muted small">—</span>';
    }
    const freqBadgeClass = m.low_freq_warning ? 'bg-danger' : (m.band_low_pct >= 30 ? 'bg-warning text-dark' : 'bg-success');
    const parts = [`<span class="badge ${freqBadgeClass}" title="${mediaFrequencyNote(m)}" data-bs-toggle="tooltip">${t('media.bandLow')} ${m.band_low_pct.toFixed(0)}% · ${t('media.bandMid')} ${m.band_mid_pct.toFixed(0)}% · ${t('media.bandHigh')} ${m.band_high_pct.toFixed(0)}%</span>`];
    if (m.suggested_gain_db) {
        parts.push(`<button class="btn btn-sm btn-outline-primary ms-1" title="${mediaHeadroomNote(m)}" onclick="normalizeMedia(${m.id})">${t('media.amplify', { gain: m.suggested_gain_db.toFixed(1) })}</button>`);
    } else if (m.normalized) {
        parts.push(`<span class="badge bg-secondary ms-1" title="${mediaHeadroomNote(m)}" data-bs-toggle="tooltip">${t('media.amplified')}</span>`);
    }
    return `<div class="small">${parts.join(' ')}</div>`;
}

function renderMedia() {
    document.getElementById('media-body').innerHTML = state.media.map(m => `
        <tr>
            <td>${m.original_filename}</td>
            <td>${m.duration_seconds.toFixed(1)}s</td>
            <td class="col-secondary">${(m.size_bytes / 1024 / 1024).toFixed(2)} MB</td>
            <td class="col-secondary">${fmtDateTime(m.uploaded_at)}</td>
            <td>${mediaAnalysisCell(m)}</td>
            <td class="text-end table-actions">
                <a class="btn btn-sm btn-outline-secondary" href="/api/media/${m.id}/download"><i class="bi bi-download"></i><span class="btn-label"> ${t('action.download')}</span></a>
                <button class="btn btn-sm btn-outline-danger" onclick="deleteMedia(${m.id})"><i class="bi bi-trash"></i><span class="btn-label"> ${t('action.delete')}</span></button>
            </td>
        </tr>`).join('') || `<tr><td colspan="6" class="text-muted">${t('empty.media')}</td></tr>`;
    document.querySelectorAll('#media-body [data-bs-toggle="tooltip"]').forEach(el => new bootstrap.Tooltip(el));
}

async function normalizeMedia(id) {
    try {
        const updated = await api(`/api/media/${id}/normalize`, { method: 'POST' });
        const idx = state.media.findIndex(m => m.id === id);
        if (idx !== -1) state.media[idx] = updated;
        renderMedia();
        toast(t('toast.fileAmplified'));
    } catch (e) {
        toast(e.message, 'danger');
    }
}

function dayLabel(code) { return t(`schedules.${code}`); }
function targetLabel(target) {
    if (target.target_type === 'all') return `<span class="badge bg-primary badge-target">${t('common.allSpeakers')}</span>`;
    if (target.target_type === 'zone') return `<span class="badge bg-info badge-target">${t('common.zone')}: ${state.zones.find(z => z.id === target.target_id)?.name || target.target_id}</span>`;
    return `<span class="badge bg-secondary badge-target">${state.speakers.find(s => s.id === target.target_id)?.name || target.target_id}</span>`;
}
function holidayLabel(s) {
    if (!s.holidays_only && !s.exclude_holidays) return t('schedules.holidayNone');
    const mode = s.holidays_only ? t('schedules.holidayOnly') : t('schedules.holidayExclude');
    return `${mode} (${s.holiday_country || 'IT'})`;
}

function renderSchedules() {
    document.getElementById('schedules-body').innerHTML = state.schedules.map(s => `
        <tr>
            <td>${s.name}</td>
            <td class="col-secondary">${state.media.find(m => m.id === s.media_id)?.original_filename || s.media_id}</td>
            <td>${targetLabel(s)}</td>
            <td>${s.time_of_day.slice(0, 5)}</td>
            <td class="col-secondary">${s.days_of_week.split(',').map(dayLabel).join(' ')}</td>
            <td class="col-secondary">${holidayLabel(s)}</td>
            <td>${s.enabled ? '✅' : '⏸️'}</td>
            <td class="table-actions text-end">
                <button class="btn btn-sm btn-outline-primary" onclick="editSchedule(${s.id})"><i class="bi bi-pencil"></i><span class="btn-label"> ${t('action.edit')}</span></button>
                <button class="btn btn-sm btn-outline-danger" onclick="deleteSchedule(${s.id})"><i class="bi bi-trash"></i><span class="btn-label"> ${t('action.delete')}</span></button>
            </td>
        </tr>`).join('') || `<tr><td colspan="8" class="text-muted">${t('empty.schedules')}</td></tr>`;
}

function renderUsers() {
    document.getElementById('users-body').innerHTML = state.users.map(u => `
        <tr>
            <td>${u.username}${u.is_protected ? ' <span class="badge bg-secondary">default</span>' : ''}</td>
            <td>${u.full_name || ''}</td><td>${u.role}</td>
            <td class="text-end table-actions">
                <button class="btn btn-sm btn-outline-primary" onclick="editUser(${u.id})"><i class="bi bi-pencil"></i><span class="btn-label"> ${t('action.edit')}</span></button>
                ${u.is_protected ? '' : `<button class="btn btn-sm btn-outline-danger" onclick="deleteUser(${u.id})"><i class="bi bi-trash"></i><span class="btn-label"> ${t('action.delete')}</span></button>`}
            </td>
        </tr>`).join('');
}

// ---------- Column sorting (click a <th data-sort> to sort A-Z / Z-A) ----------
const sortState = {};
const SORT_KEY_FNS = {
    speakers: {
        status: s => s.status,
        name: s => s.name,
        ip: s => s.ip_address.split('.').map(n => n.padStart(3, '0')).join('.'),
        zone: s => state.zones.find(z => z.id === s.zone_id)?.name || '',
        mcast: s => s.own_multicast_address,
        model: s => [s.brand, s.model].filter(Boolean).join(' '),
    },
    zones: {
        name: z => z.name,
        description: z => z.description || '',
        mcast: z => z.multicast_address,
        count: z => state.speakers.filter(s => s.zone_id === z.id).length,
    },
    media: {
        name: m => m.original_filename,
        duration: m => m.duration_seconds,
        size: m => m.size_bytes,
        date: m => m.uploaded_at,
    },
    schedules: {
        name: s => s.name,
        media: s => state.media.find(m => m.id === s.media_id)?.original_filename || '',
        time: s => s.time_of_day,
        enabled: s => s.enabled ? 1 : 0,
    },
};
const SORT_RENDER_FNS = { speakers: renderSpeakers, zones: renderZones, media: renderMedia, schedules: renderSchedules };

function sortRows(arr, keyFn, dir) {
    arr.sort((a, b) => {
        const av = keyFn(a), bv = keyFn(b);
        if (typeof av === 'number' && typeof bv === 'number') return dir * (av - bv);
        return dir * String(av).localeCompare(String(bv), 'it', { numeric: true, sensitivity: 'base' });
    });
}
function initSortableTables() {
    Object.entries(SORT_KEY_FNS).forEach(([table, keyFns]) => {
        document.querySelectorAll(`#tab-${table === 'schedules' ? 'schedules' : table} thead th[data-sort]`).forEach(th => {
            th.addEventListener('click', () => {
                const field = th.dataset.sort;
                const keyFn = keyFns[field];
                if (!keyFn) return;
                const current = sortState[table] || {};
                const dir = (current.field === field && current.dir === 1) ? -1 : 1;
                sortState[table] = { field, dir };
                sortRows(state[table], keyFn, dir);
                SORT_RENDER_FNS[table]();
                document.querySelectorAll(`#tab-${table} thead th[data-sort]`).forEach(h => {
                    h.querySelector('.sort-indicator')?.remove();
                    if (h === th) h.insertAdjacentHTML('beforeend', ` <span class="sort-indicator">${dir === 1 ? '▲' : '▼'}</span>`);
                });
            });
        });
    });
}
initSortableTables();

function populateMediaSelects() {
    const opts = state.media.map(m => `<option value="${m.id}">${m.original_filename}</option>`).join('');
    document.getElementById('play-media').innerHTML = opts;
    document.getElementById('schedule-media').innerHTML = opts;
}
function populateZoneSelects() {
    const sel = document.getElementById('speaker-zone');
    const current = sel.value;
    sel.innerHTML = `<option value="">${t('common.none')}</option>` +
        state.zones.map(z => `<option value="${z.id}">${z.name}</option>`).join('');
    if ([...sel.options].some(o => o.value === current)) sel.value = current;
}
function populateTargetPickers() {
    for (const prefix of ['play', 'schedule']) {
        const typeSel = document.getElementById(`${prefix}-target-type`);
        typeSel.onchange = () => refreshTargetIdOptions(prefix);
        refreshTargetIdOptions(prefix);
    }
}
function refreshTargetIdOptions(prefix) {
    const type = document.getElementById(`${prefix}-target-type`).value;
    const wrap = document.getElementById(`${prefix}-target-id-wrap`);
    const sel = document.getElementById(`${prefix}-target-id`);
    if (type === 'all') { wrap.classList.add('d-none'); return; }
    wrap.classList.remove('d-none');
    const items = type === 'zone' ? state.zones : state.speakers;
    sel.innerHTML = items.map(i => `<option value="${i.id}">${i.name}</option>`).join('');
}

// ---------- Playback ----------
document.getElementById('play-btn').addEventListener('click', async () => {
    const target_type = document.getElementById('play-target-type').value;
    const target_id = target_type === 'all' ? null : Number(document.getElementById('play-target-id').value);
    try {
        await api('/api/playback/play', {
            method: 'POST',
            body: JSON.stringify({ media_id: Number(document.getElementById('play-media').value), target_type, target_id }),
        });
        toast(t('toast.playbackStarted'));
        await loadHistory();
    } catch (e) { toast(e.message, 'danger'); }
});

function playTargetLabel(r) {
    if (r.target_type === 'all') return t('common.allSpeakers');
    if (r.target_type === 'zone') return `${t('common.zone')}: ${r.target_label}`;
    return r.target_label;
}

async function loadHistory() {
    const rows = await api('/api/playback/history?limit=30');
    const statusBadge = (s) => ({
        running: 'bg-primary', completed: 'bg-success', failed: 'bg-danger', stopped: 'bg-secondary',
    }[s] || 'bg-secondary');
    document.getElementById('history-body').innerHTML = rows.map(r => `
        <tr>
            <td>${fmtDateTime(r.started_at)}</td>
            <td>${state.media.find(m => m.id === r.media_id)?.original_filename || r.media_id}</td>
            <td>${playTargetLabel(r)}</td>
            <td class="col-secondary">${r.source === 'schedule' ? t('play.sourceScheduled') : `${t('play.sourceManual')}${r.triggered_by_name ? ' — ' + r.triggered_by_name : ''}`}</td>
            <td><span class="badge ${statusBadge(r.status)}">${r.status}</span></td>
            <td>${r.status === 'running' ? `<button class="btn btn-sm btn-outline-danger" onclick="stopPlayback(${r.id})"><i class="bi bi-stop-circle"></i><span class="btn-label"> ${t('action.stop')}</span></button>` : ''}</td>
        </tr>`).join('') || `<tr><td colspan="6" class="text-muted">${t('empty.playbacks')}</td></tr>`;
}
document.getElementById('refresh-history').addEventListener('click', loadHistory);
async function stopPlayback(id) {
    try { await api(`/api/playback/stop/${id}`, { method: 'POST' }); toast(t('toast.playbackStopped')); await loadHistory(); }
    catch (e) { toast(e.message, 'danger'); }
}
async function pingSpeaker(id, btn) {
    // innerHTML (not textContent) so the icon markup survives the
    // restore — textContent would flatten it to plain text, which on
    // mobile (icon-only buttons, label hidden via CSS) made the button
    // visibly "turn into" a text button on its own once the action finished.
    const originalHtml = btn ? btn.innerHTML : null;
    if (btn) { btn.disabled = true; btn.innerHTML = `<span class="btn-label">${t('action.checking')}</span>`; }
    try {
        const s = await api(`/api/speakers/${id}/ping`, { method: 'POST' });
        toast(
            s.status === 'online' ? `${s.name}: raggiungibile ✅` : `${s.name}: non raggiungibile ❌`,
            s.status === 'online' ? 'success' : 'danger',
        );
        await loadAll();
    } catch (e) {
        toast(e.message, 'danger');
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = originalHtml; }
    }
}
async function previewMulticast(id) {
    try {
        const entries = await api(`/api/speakers/${id}/multicast-preview`);
        const lines = entries.map(e => `#${e.index}  ${e.address}:${e.port}  "${e.label}"  (priorità ${e.priority})`).join('\n');
        alert(`Lista multicast paging che verrà scritta sul dispositivo (solo parametri paging.*, nulla di SIP/rete):\n\n${lines}`);
    } catch (e) { toast(e.message, 'danger'); }
}
async function pushMulticast(id, btn) {
    // innerHTML (not textContent) — see pingSpeaker's comment above,
    // same bug: textContent restore was dropping the button's icon
    // markup, leaving a plain-text "Applica ora" behind on mobile.
    const originalHtml = btn ? btn.innerHTML : null;
    if (btn) { btn.disabled = true; btn.innerHTML = `<span class="btn-label">${t('action.applying')}</span>`; }
    try {
        const res = await api(`/api/speakers/${id}/push-config`, { method: 'POST' });
        toast(`✅ ${t('toast.configApplied')} (${res.applied.length})`);
    } catch (e) {
        if (e.detail && e.detail.unsupported_brand) {
            toast(e.detail.message, 'warning');
        } else if (e.detail && e.detail.failed) {
            const okCount = e.detail.applied.length;
            const failCount = e.detail.failed.length;
            toast(`⚠️ ${t('toast.configPartial')}: ${okCount}/${okCount + failCount}`, 'warning');
            alert(`${t('toast.configPartial')}: ${okCount} ${t('toast.confirmed')}, ${failCount} ${t('toast.failed')}.\n\n${t('toast.failedList')}: ${e.detail.failed.join(', ')}\n\n${t('toast.retryHint')}`);
        } else {
            toast(`❌ ${e.message}`, 'danger');
        }
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = originalHtml; }
    }
}

// ---------- Speaker config backups ----------
async function openBackups(id) {
    const s = state.speakers.find(x => x.id === id);
    document.getElementById('backups-speaker-id').value = id;
    document.getElementById('backups-speaker-name').textContent = s ? s.name : '';
    new bootstrap.Modal(document.getElementById('backups-modal')).show();
    await refreshBackups(id);
}
async function refreshBackups(id) {
    try {
        const backups = await api(`/api/speakers/${id}/backups`);
        document.getElementById('backups-body').innerHTML = backups.map(b => `
            <tr>
                <td>${fmtDateTime(b.created_at)}</td>
                <td>${b.format}</td>
                <td class="col-secondary">${(b.size_bytes / 1024).toFixed(1)} KB</td>
                <td class="col-secondary">${b.created_by_name || '—'}</td>
                <td class="text-end table-actions">
                    <a class="btn btn-sm btn-outline-secondary" href="/api/backups/${b.id}/download"><i class="bi bi-download"></i><span class="btn-label"> ${t('action.download')}</span></a>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteBackup(${b.id}, ${id})"><i class="bi bi-trash"></i><span class="btn-label"> ${t('action.discard')}</span></button>
                </td>
            </tr>`).join('') || `<tr><td colspan="5" class="text-muted">${t('empty.backups')}</td></tr>`;
    } catch (e) { toast(e.message, 'danger'); }
}
document.getElementById('backups-create-btn').addEventListener('click', async () => {
    const id = document.getElementById('backups-speaker-id').value;
    try {
        await api(`/api/speakers/${id}/backups`, { method: 'POST' });
        toast(t('toast.backupCreated'));
        await refreshBackups(id);
    } catch (e) { toast(e.message, 'danger'); }
});
async function deleteBackup(backupId, speakerIdForRefresh) {
    if (!confirm(t('confirm.deleteBackup'))) return;
    try {
        await api(`/api/backups/${backupId}`, { method: 'DELETE' });
        if (speakerIdForRefresh) await refreshBackups(speakerIdForRefresh);
        else await loadBackupArchive();
    } catch (e) { toast(e.message, 'danger'); }
}

// ---------- Media upload ----------
document.getElementById('upload-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const fileInput = document.getElementById('upload-file');
    const status = document.getElementById('upload-status');
    if (!fileInput.files.length) return;
    const fd = new FormData();
    fd.append('file', fileInput.files[0]);
    status.textContent = t('toast.uploading');
    try {
        const res = await fetch('/api/media/upload', { method: 'POST', body: fd });
        if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || t('toast.uploadError')); }
        const uploaded = await res.json();
        status.textContent = '';
        fileInput.value = '';
        const notes = [mediaFrequencyNote(uploaded), mediaHeadroomNote(uploaded)].filter(Boolean).join(' ');
        toast(notes ? `${t('toast.fileUploaded')}. ${notes}` : t('toast.fileUploaded'), uploaded.low_freq_warning ? 'warning' : 'success');
        state.media = await api('/api/media');
        renderMedia(); populateMediaSelects();
    } catch (e) { status.textContent = ''; toast(e.message, 'danger'); }
});
async function deleteMedia(id) {
    if (!confirm(t('confirm.deleteMedia'))) return;
    await api(`/api/media/${id}`, { method: 'DELETE' });
    state.media = await api('/api/media'); renderMedia(); populateMediaSelects();
}

// ---------- Speakers CRUD ----------
// Brands whose driver supports per-slot paging volume (see
// services/drivers/*.py's supports_paging_volume) — kept in sync by
// hand since the list is short; mirrors the server-side registry.
const PAGING_VOLUME_BRANDS = ['fanvil'];
// Fixed dropdown values (see the <select> in dashboard.html) — anything
// else typed into the "Other" free-text field is stored as-is.
const KNOWN_SPEAKER_BRANDS = ['fanvil', 'algo', 'cyberdata', 'grandstream', 'axis', 'tiptel', 'akuvox'];

function effectiveSpeakerBrand() {
    const select = document.getElementById('speaker-brand').value;
    return select === 'other' ? document.getElementById('speaker-brand-other').value.trim() : select;
}
function toggleSpeakerVolumeField() {
    document.getElementById('speaker-brand-other').classList.toggle('d-none', document.getElementById('speaker-brand').value !== 'other');
    const brand = effectiveSpeakerBrand().toLowerCase();
    document.getElementById('speaker-volume-wrap').classList.toggle('d-none', !PAGING_VOLUME_BRANDS.includes(brand));
}
document.getElementById('speaker-brand').addEventListener('change', toggleSpeakerVolumeField);
document.getElementById('speaker-brand-other').addEventListener('input', toggleSpeakerVolumeField);

document.getElementById('new-speaker-btn').addEventListener('click', () => resetSpeakerForm());
function resetSpeakerForm() {
    document.getElementById('speaker-form').reset();
    document.getElementById('speaker-id').value = '';
    document.getElementById('speaker-mcast-port').value = 5004;
    toggleSpeakerVolumeField();
}
function editSpeaker(id) {
    const s = state.speakers.find(x => x.id === id);
    document.getElementById('speaker-id').value = s.id;
    document.getElementById('speaker-name').value = s.name;
    document.getElementById('speaker-ip').value = s.ip_address;
    document.getElementById('speaker-zone').value = s.zone_id || '';
    document.getElementById('speaker-mcast-addr').value = s.own_multicast_address;
    document.getElementById('speaker-mcast-port').value = s.own_multicast_port;
    const brand = (s.brand || '').toLowerCase();
    if (brand && !KNOWN_SPEAKER_BRANDS.includes(brand)) {
        document.getElementById('speaker-brand').value = 'other';
        document.getElementById('speaker-brand-other').value = s.brand;
    } else {
        document.getElementById('speaker-brand').value = brand;
        document.getElementById('speaker-brand-other').value = '';
    }
    document.getElementById('speaker-model').value = s.model || '';
    document.getElementById('speaker-location').value = s.location;
    document.getElementById('speaker-http-user').value = s.http_username;
    document.getElementById('speaker-paging-volume').value = s.paging_volume || '';
    toggleSpeakerVolumeField();
    new bootstrap.Modal(document.getElementById('speaker-modal')).show();
}
document.getElementById('speaker-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = document.getElementById('speaker-id').value;
    const payload = {
        name: document.getElementById('speaker-name').value,
        ip_address: document.getElementById('speaker-ip').value,
        zone_id: document.getElementById('speaker-zone').value ? Number(document.getElementById('speaker-zone').value) : null,
        own_multicast_address: document.getElementById('speaker-mcast-addr').value,
        own_multicast_port: Number(document.getElementById('speaker-mcast-port').value),
        brand: effectiveSpeakerBrand(),
        model: document.getElementById('speaker-model').value,
        location: document.getElementById('speaker-location').value,
        http_username: document.getElementById('speaker-http-user').value,
        paging_volume: document.getElementById('speaker-paging-volume').value || null,
    };
    const pass = document.getElementById('speaker-http-pass').value;
    // On edit, leave the stored password untouched unless a new one is
    // typed (the field is always shown blank — the API never returns
    // the stored password back to the browser).
    if (!id || pass) payload.http_password = pass;
    try {
        if (id) await api(`/api/speakers/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
        else await api('/api/speakers', { method: 'POST', body: JSON.stringify(payload) });
        bootstrap.Modal.getInstance(document.getElementById('speaker-modal')).hide();
        await loadAll();
        toast(t('toast.speakerSaved'));
    } catch (e2) { toast(e2.message, 'danger'); }
});
async function deleteSpeaker(id) {
    if (!confirm(t('confirm.deleteSpeaker'))) return;
    await api(`/api/speakers/${id}`, { method: 'DELETE' }); await loadAll();
}

// ---------- Zones CRUD ----------
document.getElementById('new-zone-btn').addEventListener('click', () => {
    document.getElementById('zone-form').reset();
    document.getElementById('zone-id').value = '';
    document.getElementById('zone-mcast-port').value = 5004;
});
function editZone(id) {
    const z = state.zones.find(x => x.id === id);
    document.getElementById('zone-id').value = z.id;
    document.getElementById('zone-name').value = z.name;
    document.getElementById('zone-description').value = z.description || '';
    document.getElementById('zone-mcast-addr').value = z.multicast_address;
    document.getElementById('zone-mcast-port').value = z.multicast_port;
    new bootstrap.Modal(document.getElementById('zone-modal')).show();
}
document.getElementById('zone-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = document.getElementById('zone-id').value;
    const payload = {
        name: document.getElementById('zone-name').value,
        description: document.getElementById('zone-description').value,
        multicast_address: document.getElementById('zone-mcast-addr').value,
        multicast_port: Number(document.getElementById('zone-mcast-port').value),
    };
    try {
        if (id) await api(`/api/zones/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
        else await api('/api/zones', { method: 'POST', body: JSON.stringify(payload) });
        bootstrap.Modal.getInstance(document.getElementById('zone-modal')).hide();
        await loadAll();
        toast(t('toast.zoneSaved'));
    } catch (e2) { toast(e2.message, 'danger'); }
});
async function deleteZone(id) {
    if (!confirm(t('confirm.deleteZone'))) return;
    await api(`/api/zones/${id}`, { method: 'DELETE' }); await loadAll();
}

// ---------- Schedules CRUD ----------
document.getElementById('new-schedule-btn').addEventListener('click', () => resetScheduleForm());
function resetScheduleForm() {
    document.getElementById('schedule-form').reset();
    document.getElementById('schedule-id').value = '';
    document.querySelectorAll('#schedule-days input').forEach(c => c.checked = ['mon','tue','wed','thu','fri'].includes(c.value));
    document.getElementById('schedule-holiday-mode').value = 'none';
    document.getElementById('schedule-holiday-country').value = 'IT';
    refreshTargetIdOptions('schedule');
}
function editSchedule(id) {
    const s = state.schedules.find(x => x.id === id);
    document.getElementById('schedule-id').value = s.id;
    document.getElementById('schedule-name').value = s.name;
    document.getElementById('schedule-media').value = s.media_id;
    document.getElementById('schedule-target-type').value = s.target_type;
    refreshTargetIdOptions('schedule');
    if (s.target_id) document.getElementById('schedule-target-id').value = s.target_id;
    document.getElementById('schedule-time').value = s.time_of_day.slice(0, 5);
    document.getElementById('schedule-start-date').value = s.start_date || '';
    document.getElementById('schedule-end-date').value = s.end_date || '';
    const days = s.days_of_week.split(',');
    document.querySelectorAll('#schedule-days input').forEach(c => c.checked = days.includes(c.value));
    document.getElementById('schedule-holiday-mode').value = s.holidays_only ? 'only' : (s.exclude_holidays ? 'exclude' : 'none');
    document.getElementById('schedule-holiday-country').value = s.holiday_country || 'IT';
    document.getElementById('schedule-enabled').checked = s.enabled;
    new bootstrap.Modal(document.getElementById('schedule-modal')).show();
}
document.getElementById('schedule-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = document.getElementById('schedule-id').value;
    const target_type = document.getElementById('schedule-target-type').value;
    const days = Array.from(document.querySelectorAll('#schedule-days input:checked')).map(c => c.value);
    if (!days.length) { toast(t('toast.selectDay'), 'danger'); return; }
    const holidayMode = document.getElementById('schedule-holiday-mode').value;
    const payload = {
        name: document.getElementById('schedule-name').value,
        media_id: Number(document.getElementById('schedule-media').value),
        target_type,
        target_id: target_type === 'all' ? null : Number(document.getElementById('schedule-target-id').value),
        time_of_day: document.getElementById('schedule-time').value + ':00',
        days_of_week: days.join(','),
        start_date: document.getElementById('schedule-start-date').value || null,
        end_date: document.getElementById('schedule-end-date').value || null,
        exclude_holidays: holidayMode === 'exclude',
        holidays_only: holidayMode === 'only',
        holiday_country: document.getElementById('schedule-holiday-country').value,
        enabled: document.getElementById('schedule-enabled').checked,
    };
    try {
        if (id) await api(`/api/schedules/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
        else await api('/api/schedules', { method: 'POST', body: JSON.stringify(payload) });
        bootstrap.Modal.getInstance(document.getElementById('schedule-modal')).hide();
        await loadAll();
        toast(t('toast.scheduleSaved'));
    } catch (e2) { toast(e2.message, 'danger'); }
});
async function deleteSchedule(id) {
    if (!confirm(t('confirm.deleteSchedule'))) return;
    await api(`/api/schedules/${id}`, { method: 'DELETE' }); await loadAll();
}

// ---------- Users CRUD ----------
document.getElementById('user-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {
        username: document.getElementById('user-username').value,
        full_name: document.getElementById('user-fullname').value,
        password: document.getElementById('user-password').value,
        role: document.getElementById('user-role').value,
    };
    try {
        await api('/api/auth/users', { method: 'POST', body: JSON.stringify(payload) });
        bootstrap.Modal.getInstance(document.getElementById('user-modal')).hide();
        document.getElementById('user-form').reset();
        state.users = await api('/api/auth/users'); renderUsers();
        toast(t('toast.userCreated'));
    } catch (e2) { toast(e2.message, 'danger'); }
});
async function deleteUser(id) {
    if (!confirm(t('confirm.deleteUser'))) return;
    try { await api(`/api/auth/users/${id}`, { method: 'DELETE' }); state.users = await api('/api/auth/users'); renderUsers(); }
    catch (e) { toast(e.message, 'danger'); }
}

function editUser(id) {
    const u = state.users.find(x => x.id === id);
    document.getElementById('user-edit-id').value = u.id;
    document.getElementById('user-edit-username').value = u.username;
    document.getElementById('user-edit-fullname').value = u.full_name || '';
    document.getElementById('user-edit-role').value = u.role;
    document.getElementById('user-edit-password').value = '';
    new bootstrap.Modal(document.getElementById('user-edit-modal')).show();
}
document.getElementById('user-edit-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = document.getElementById('user-edit-id').value;
    const payload = {
        full_name: document.getElementById('user-edit-fullname').value,
        role: document.getElementById('user-edit-role').value,
    };
    const newPw = document.getElementById('user-edit-password').value;
    if (newPw) payload.new_password = newPw;
    try {
        await api(`/api/auth/users/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
        bootstrap.Modal.getInstance(document.getElementById('user-edit-modal')).hide();
        state.users = await api('/api/auth/users'); renderUsers();
        toast(t('toast.userUpdated'));
    } catch (e2) { toast(e2.message, 'danger'); }
});

// ---------- Self password change ----------
document.getElementById('password-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errBox = document.getElementById('pw-error');
    errBox.classList.add('d-none');
    const newPw = document.getElementById('pw-new').value;
    const confirmPw = document.getElementById('pw-confirm').value;
    if (newPw !== confirmPw) {
        errBox.textContent = t('toast.passwordMismatch');
        errBox.classList.remove('d-none');
        return;
    }
    try {
        await api('/api/auth/me/password', {
            method: 'POST',
            body: JSON.stringify({ current_password: document.getElementById('pw-current').value, new_password: newPw }),
        });
        bootstrap.Modal.getInstance(document.getElementById('password-modal')).hide();
        document.getElementById('password-form').reset();
        toast(t('toast.passwordUpdated'));
    } catch (e2) {
        errBox.textContent = e2.message;
        errBox.classList.remove('d-none');
    }
});

// ---------- Two-factor auth (self-service) ----------
const tfaStatusView = document.getElementById('tfa-status-view');
const tfaSetupView = document.getElementById('tfa-setup-view');
const tfaCodesView = document.getElementById('tfa-codes-view');
const tfaStatusFooter = document.getElementById('tfa-status-footer');
const tfaSetupFooter = document.getElementById('tfa-setup-footer');
const tfaCodesFooter = document.getElementById('tfa-codes-footer');

function tfaShowView(view) {
    const pairs = [[tfaStatusView, tfaStatusFooter], [tfaSetupView, tfaSetupFooter], [tfaCodesView, tfaCodesFooter]];
    pairs.forEach(([v, f]) => { v.classList.add('d-none'); f.classList.add('d-none'); });
    const match = pairs.find(([v]) => v === view);
    view.classList.remove('d-none');
    if (match) match[1].classList.remove('d-none');
}

async function loadTfaStatus() {
    try {
        const s = await api('/api/auth/me/2fa');
        document.getElementById('tfa-enable-btn').classList.toggle('d-none', s.enabled);
        document.getElementById('tfa-disable-btn').classList.toggle('d-none', !s.enabled);
        document.getElementById('tfa-regen-btn').classList.toggle('d-none', !s.enabled);
        document.getElementById('tfa-status-text').textContent = s.enabled
            ? t('security.statusEnabled', { n: s.remaining_recovery_codes })
            : t('security.statusDisabled');
    } catch (e) { /* modal not open yet at first load, ignore */ }
    tfaShowView(tfaStatusView);
}
document.getElementById('security-modal').addEventListener('show.bs.modal', loadTfaStatus);

document.getElementById('tfa-enable-btn').addEventListener('click', async () => {
    try {
        const setup = await api('/api/auth/me/2fa/setup', { method: 'POST' });
        document.getElementById('tfa-secret').value = setup.secret;
        document.getElementById('tfa-qr-container').innerHTML = setup.qr_svg;
        document.getElementById('tfa-confirm-code').value = '';
        document.getElementById('tfa-setup-error').classList.add('d-none');
        tfaShowView(tfaSetupView);
    } catch (e) { toast(e.message, 'danger'); }
});

document.getElementById('tfa-cancel-btn').addEventListener('click', () => tfaShowView(tfaStatusView));

document.getElementById('tfa-confirm-btn').addEventListener('click', async () => {
    const errBox = document.getElementById('tfa-setup-error');
    errBox.classList.add('d-none');
    try {
        const result = await api('/api/auth/me/2fa/confirm', {
            method: 'POST',
            body: JSON.stringify({ code: document.getElementById('tfa-confirm-code').value }),
        });
        document.getElementById('tfa-codes-list').textContent = result.recovery_codes.join('\n');
        tfaShowView(tfaCodesView);
    } catch (e) {
        errBox.textContent = e.message;
        errBox.classList.remove('d-none');
    }
});

document.getElementById('tfa-codes-done-btn').addEventListener('click', loadTfaStatus);

document.getElementById('tfa-disable-btn').addEventListener('click', async () => {
    const password = prompt(t('security.promptDisable'));
    if (!password) return;
    try {
        await api('/api/auth/me/2fa/disable', { method: 'POST', body: JSON.stringify({ password }) });
        toast(t('toast.tfaDisabled'));
        loadTfaStatus();
    } catch (e) { toast(e.message, 'danger'); }
});

document.getElementById('tfa-regen-btn').addEventListener('click', async () => {
    const password = prompt(t('security.promptRegen'));
    if (!password) return;
    try {
        const result = await api('/api/auth/me/2fa/recovery-codes/regenerate', { method: 'POST', body: JSON.stringify({ password }) });
        document.getElementById('tfa-codes-list').textContent = result.recovery_codes.join('\n');
        tfaShowView(tfaCodesView);
    } catch (e) { toast(e.message, 'danger'); }
});

// ---------- Full data export ----------
const exportBtn = document.getElementById('export-download-btn');
if (exportBtn) {
    exportBtn.addEventListener('click', async () => {
        const errBox = document.getElementById('export-error');
        errBox.classList.add('d-none');
        const password = document.getElementById('export-password').value;
        exportBtn.disabled = true;
        exportBtn.textContent = t('toast.exporting');
        try {
            const res = await fetch('/api/system/export', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password }),
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.detail || `${t('toast.error')} ${res.status}`);
            }
            const blob = await res.blob();
            const disposition = res.headers.get('Content-Disposition') || '';
            const match = disposition.match(/filename="([^"]+)"/);
            const filename = match ? match[1] : 'zonecast_export.zcbundle';
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url; a.download = filename;
            document.body.appendChild(a); a.click(); a.remove();
            URL.revokeObjectURL(url);
            document.getElementById('export-password').value = '';
            toast(t('toast.exportDownloaded'));
        } catch (e) {
            errBox.textContent = e.message;
            errBox.classList.remove('d-none');
        } finally {
            exportBtn.disabled = false;
            exportBtn.textContent = t('system.exportDownload');
        }
    });
}

// ---------- Full data import ----------
const importBtn = document.getElementById('import-upload-btn');
if (importBtn) {
    importBtn.addEventListener('click', async () => {
        const errBox = document.getElementById('import-error');
        const infoBox = document.getElementById('import-info');
        errBox.classList.add('d-none');
        infoBox.classList.add('d-none');
        const fileInput = document.getElementById('import-file');
        if (!fileInput.files.length) { toast(t('toast.chooseFile'), 'danger'); return; }
        if (!confirm(t('confirm.import'))) return;
        const fd = new FormData();
        fd.append('file', fileInput.files[0]);
        fd.append('password', document.getElementById('import-password').value);
        importBtn.disabled = true;
        importBtn.textContent = t('toast.importing');
        try {
            const res = await fetch('/api/system/import', { method: 'POST', body: fd });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || `${t('toast.error')} ${res.status}`);
            infoBox.textContent = data.message;
            infoBox.classList.remove('d-none');
            toast(t('toast.importStarted'));
        } catch (e) {
            errBox.textContent = e.message;
            errBox.classList.remove('d-none');
        } finally {
            importBtn.disabled = false;
            importBtn.textContent = t('system.importRestart');
        }
    });
}

// ---------- Host resources (disk / RAM / CPU) ----------
function _resBarClass(percent) {
    if (percent >= 90) return 'bg-danger';
    if (percent >= 75) return 'bg-warning';
    return 'bg-success';
}
function _fmtGiB(bytes) { return (bytes / (1024 ** 3)).toFixed(1); }
async function loadHostResources() {
    if (!document.getElementById('sys-resources-card')) return;
    try {
        const r = await api('/api/system/resources');
        document.getElementById('res-error').classList.add('d-none');

        document.getElementById('res-disk-label').textContent = `${_fmtGiB(r.disk_used_bytes)} / ${_fmtGiB(r.disk_total_bytes)} GiB (${r.disk_percent}%)`;
        const diskBar = document.getElementById('res-disk-bar');
        diskBar.style.width = `${r.disk_percent}%`;
        diskBar.className = `progress-bar ${_resBarClass(r.disk_percent)}`;

        document.getElementById('res-mem-label').textContent = `${_fmtGiB(r.mem_used_bytes)} / ${_fmtGiB(r.mem_total_bytes)} GiB (${r.mem_percent}%)`;
        const memBar = document.getElementById('res-mem-bar');
        memBar.style.width = `${r.mem_percent}%`;
        memBar.className = `progress-bar ${_resBarClass(r.mem_percent)}`;

        document.getElementById('res-cpu-count').textContent = r.cpu_count;
        document.getElementById('res-cpu-label').textContent = `${r.cpu_percent}%`;
        const cpuBar = document.getElementById('res-cpu-bar');
        cpuBar.style.width = `${r.cpu_percent}%`;
        cpuBar.className = `progress-bar ${_resBarClass(r.cpu_percent)}`;
    } catch (e) {
        const err = document.getElementById('res-error');
        err.textContent = e.message;
        err.classList.remove('d-none');
    }
}
setInterval(loadHostResources, 15000);

// ---------- System time / NTP (read-only on Docker, full control on native) ----------
let timezonesLoaded = false;
let lastTimeStatus = null;
function renderTimeStatusLabels(status) {
    document.getElementById('sys-sync-status').innerHTML = status.ntp_synchronized
        ? `<span class="badge bg-success">${t('system.synced')}</span>`
        : `<span class="badge bg-warning text-dark">${t('system.notSynced')}</span>`;
    document.getElementById('sys-ntp-servers-display').textContent = status.ntp_servers.length ? status.ntp_servers.join(', ') : t('system.noneConfigured');
}
async function loadSystemTime() {
    try {
        const status = await api('/api/system/time');
        lastTimeStatus = status;
        document.getElementById('sys-local-time').textContent = status.local_time;
        document.getElementById('sys-timezone').textContent = status.timezone;
        renderTimeStatusLabels(status);

        document.getElementById('sys-ntp-controls').classList.toggle('d-none', !status.controllable);
        document.getElementById('sys-time-readonly-note').classList.toggle('d-none', status.controllable);
        document.getElementById('sys-network-card').classList.toggle('d-none', !status.controllable);
        if (status.controllable) {
            document.getElementById('sys-ntp-toggle').checked = !!status.ntp_enabled;
            const ntpServersInput = document.getElementById('sys-ntp-servers-input');
            if (document.activeElement !== ntpServersInput) ntpServersInput.value = status.ntp_servers.join(', ');
            if (!timezonesLoaded) {
                timezonesLoaded = true;
                try {
                    const zones = await api('/api/system/time/timezones');
                    const sel = document.getElementById('sys-timezone-select');
                    sel.innerHTML = zones.map(z => `<option value="${z}">${z}</option>`).join('');
                    sel.value = status.timezone;
                } catch (e) { /* leave select empty, non-fatal */ }
            }
            loadNetworkConfig();
        }
    } catch (e) {
        document.getElementById('sys-local-time').textContent = t('system.notAvailable');
        document.getElementById('sys-sync-status').innerHTML = `<span class="text-danger small">${e.message}</span>`;
    }
}
document.getElementById('sys-manual-time-save').addEventListener('click', async () => {
    const val = document.getElementById('sys-manual-time').value;
    if (!val) { toast(t('toast.selectDateTime'), 'danger'); return; }
    try {
        const res = await api('/api/system/time/manual', { method: 'POST', body: JSON.stringify({ datetime_local: val }) });
        toast(res.ntp_disabled ? t('toast.timeSetNtpOff') : t('toast.timeSet'));
        await loadSystemTime();
    } catch (err) { toast(err.message, 'danger'); }
});
document.getElementById('sys-ntp-servers-save').addEventListener('click', async () => {
    const raw = document.getElementById('sys-ntp-servers-input').value;
    const servers = raw.split(/[,\s]+/).map(s => s.trim()).filter(Boolean);
    try {
        await api('/api/system/time/ntp-servers', { method: 'POST', body: JSON.stringify({ servers }) });
        toast(t('toast.ntpServersUpdated'));
        await loadSystemTime();
    } catch (err) { toast(err.message, 'danger'); }
});
document.getElementById('sys-ntp-toggle').addEventListener('change', async (e) => {
    try {
        await api('/api/system/time/ntp', { method: 'POST', body: JSON.stringify({ enabled: e.target.checked }) });
        toast(e.target.checked ? t('toast.ntpEnabled') : t('toast.ntpDisabled'));
        await loadSystemTime();
    } catch (err) { toast(err.message, 'danger'); e.target.checked = !e.target.checked; }
});
document.getElementById('sys-timezone-save').addEventListener('click', async () => {
    const tz = document.getElementById('sys-timezone-select').value;
    if (!tz) return;
    try {
        await api('/api/system/time/timezone', { method: 'POST', body: JSON.stringify({ timezone: tz }) });
        toast(t('toast.timezoneUpdated'));
        await loadSystemTime();
    } catch (err) { toast(err.message, 'danger'); }
});

// ---------- Network configuration (native install only) ----------
let netCountdownTimer = null;
function showNetTrialBox(remainingSeconds) {
    const box = document.getElementById('net-trial-box');
    box.classList.remove('d-none');
    let remaining = remainingSeconds;
    document.getElementById('net-countdown').textContent = Math.max(remaining, 0);
    clearInterval(netCountdownTimer);
    netCountdownTimer = setInterval(() => {
        remaining -= 1;
        document.getElementById('net-countdown').textContent = Math.max(remaining, 0);
        if (remaining <= 0) { clearInterval(netCountdownTimer); box.classList.add('d-none'); }
    }, 1000);
}
async function loadNetworkConfig() {
    try {
        const n = await api('/api/system/network');
        const sel = document.getElementById('net-interface');
        sel.innerHTML = n.available_interfaces.map(i => `<option value="${i}">${i}</option>`).join('');
        sel.value = n.interface;
        document.getElementById('net-address').value = n.address_cidr || '';
        document.getElementById('net-gateway').value = n.gateway || '';
        document.getElementById('net-dns').value = (n.dns_servers || []).join(', ');
    } catch (e) { /* network card already hidden if not controllable; non-fatal otherwise */ }
    // A change applied from ANY session (not just this tab) shows up
    // here too — otherwise reconnecting at the new address in a fresh
    // page load (the normal way to verify it) never surfaces a Confirm
    // button, and the trial silently expires with nothing on screen.
    try {
        const p = await api('/api/system/network/pending');
        if (p.pending_seconds !== null && p.pending_seconds !== undefined) showNetTrialBox(p.pending_seconds);
        else document.getElementById('net-trial-box').classList.add('d-none');
    } catch (e) { /* non-fatal */ }
}
document.getElementById('net-apply-btn').addEventListener('click', async () => {
    const errBox = document.getElementById('net-error');
    errBox.classList.add('d-none');
    const dns = document.getElementById('net-dns').value.split(',').map(s => s.trim()).filter(Boolean);
    const payload = {
        interface: document.getElementById('net-interface').value,
        address_cidr: document.getElementById('net-address').value.trim(),
        gateway: document.getElementById('net-gateway').value.trim(),
        dns_servers: dns,
    };
    if (!confirm(`${t('confirm.netApply')} "${payload.interface}"? ${t('confirm.netApplyEnd')}`)) return;
    try {
        const result = await api('/api/system/network/apply', { method: 'POST', body: JSON.stringify(payload) });
        showNetTrialBox(result.watchdog_seconds);
    } catch (err) {
        errBox.textContent = err.message;
        errBox.classList.remove('d-none');
    }
});
document.getElementById('net-confirm-btn').addEventListener('click', async () => {
    try {
        await api('/api/system/network/confirm', { method: 'POST' });
        clearInterval(netCountdownTimer);
        document.getElementById('net-trial-box').classList.add('d-none');
        toast(t('toast.netConfirmed'));
    } catch (err) { toast(err.message, 'danger'); }
});

// ---------- Event log ----------
const LEVEL_BADGE = { INFO: 'bg-secondary', WARNING: 'bg-warning text-dark', ERROR: 'bg-danger', CRITICAL: 'bg-danger' };
function currentLogFilterParams() {
    const level = document.getElementById('logs-level-filter').value;
    const q = document.getElementById('logs-search').value.trim();
    const params = new URLSearchParams();
    if (level) params.set('level', level);
    if (q) params.set('q', q);
    return params;
}
function syncLogExportLinks() {
    const params = currentLogFilterParams();
    const csvParams = new URLSearchParams(params); csvParams.set('format', 'csv');
    const jsonParams = new URLSearchParams(params); jsonParams.set('format', 'json');
    document.getElementById('logs-export-csv').href = `/api/logs/export?${csvParams.toString()}`;
    document.getElementById('logs-export-json').href = `/api/logs/export?${jsonParams.toString()}`;
}
async function loadLogs() {
    const params = currentLogFilterParams();
    params.set('limit', '300');
    syncLogExportLinks();
    try {
        const rows = await api(`/api/logs?${params.toString()}`);
        document.getElementById('logs-body').innerHTML = rows.map(r => `
            <tr>
                <td class="text-nowrap small">${fmtDateTime(r.created_at)}</td>
                <td><span class="badge ${LEVEL_BADGE[r.level] || 'bg-secondary'}">${r.level}</span></td>
                <td class="small text-muted col-secondary">${r.logger_name}</td>
                <td class="small">${r.message}</td>
            </tr>`).join('') || `<tr><td colspan="4" class="text-muted">${t('empty.events')}</td></tr>`;
    } catch (e) { toast(e.message, 'danger'); }
}
document.getElementById('logs-refresh').addEventListener('click', loadLogs);
document.getElementById('logs-level-filter').addEventListener('change', loadLogs);
let logsSearchTimer;
document.getElementById('logs-search').addEventListener('input', () => {
    clearTimeout(logsSearchTimer);
    logsSearchTimer = setTimeout(loadLogs, 400);
});

document.getElementById('logs-clear-btn').addEventListener('click', async () => {
    if (!confirm('Eliminare definitivamente tutto lo storico dei log? Non è recuperabile.')) return;
    try {
        const res = await api('/api/logs', { method: 'DELETE' });
        toast(`Log puliti (${res.deleted} righe rimosse)`);
        await loadLogs();
    } catch (e) { toast(e.message, 'danger'); }
});

async function loadLogRetention() {
    try {
        const s = await api('/api/logs/settings');
        document.getElementById('logs-retention-never').checked = s.retention_days === null;
        document.getElementById('logs-retention-days').value = s.retention_days ?? '';
        document.getElementById('logs-retention-days').disabled = s.retention_days === null;
    } catch (e) { /* non-fatal */ }
}
document.getElementById('logs-retention-never').addEventListener('change', (e) => {
    document.getElementById('logs-retention-days').disabled = e.target.checked;
});
document.getElementById('logs-retention-save').addEventListener('click', async () => {
    const never = document.getElementById('logs-retention-never').checked;
    const daysVal = document.getElementById('logs-retention-days').value;
    if (!never && !daysVal) { toast('Indica i giorni oppure spunta "Mai"', 'danger'); return; }
    try {
        await api('/api/logs/settings', {
            method: 'PUT',
            body: JSON.stringify({ retention_days: never ? null : parseInt(daysVal, 10) }),
        });
        toast('Impostazione di conservazione salvata');
    } catch (e) { toast(e.message, 'danger'); }
});

// ---------- Backup archive ----------
async function loadBackupArchive() {
    try {
        const rows = await api('/api/backups');
        document.getElementById('backup-archive-body').innerHTML = rows.map(b => `
            <tr>
                <td>${fmtDateTime(b.created_at)}</td>
                <td>${b.speaker_name || '—'}${b.speaker_exists ? '' : ` <span class="badge bg-secondary" title="${t('backups.speakerDeleted')}">${t('backups.archived')}</span>`}</td>
                <td class="col-secondary">${b.speaker_ip || '—'}</td>
                <td>${b.format}</td>
                <td class="col-secondary">${(b.size_bytes / 1024).toFixed(1)} KB</td>
                <td class="col-secondary">${b.created_by_name || '—'}</td>
                <td class="text-end table-actions">
                    <a class="btn btn-sm btn-outline-secondary" href="/api/backups/${b.id}/download"><i class="bi bi-download"></i><span class="btn-label"> ${t('action.download')}</span></a>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteBackup(${b.id})"><i class="bi bi-trash"></i><span class="btn-label"> ${t('action.discard')}</span></button>
                </td>
            </tr>`).join('') || `<tr><td colspan="7" class="text-muted">${t('empty.backups')}</td></tr>`;
    } catch (e) { toast(e.message, 'danger'); }
}

loadAll().catch(err => console.error(err));
setInterval(loadHistory, 15000);
