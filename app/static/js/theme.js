// Light/dark mode: a per-viewer preference (localStorage, same pattern
// as the language choice in i18n.js), not a server setting. "Auto"
// follows real sunrise/sunset at the browser's location rather than
// the OS's prefers-color-scheme, per how this was asked for — Bootstrap
// 5.3's own data-bs-theme attribute does the actual component
// restyling, so this file only has to decide light vs dark and set it.
const THEME_STORAGE_KEY = 'zc_theme_pref';      // 'light' | 'dark' | 'auto'
const THEME_COORDS_KEY = 'zc_theme_coords';     // cached {lat, lon}, geolocation asked once
const DEFAULT_COORDS = { lat: 41.9028, lon: 12.4964 }; // Rome — matches this deployment's default TIMEZONE when geolocation isn't available/granted
const AUTO_RECHECK_MS = 10 * 60 * 1000; // re-evaluate day/night every 10 min while a tab with "auto" stays open across the boundary

function getThemePref() {
    try {
        const v = localStorage.getItem(THEME_STORAGE_KEY);
        if (v === 'light' || v === 'dark' || v === 'auto') return v;
    } catch (e) { /* private mode / blocked storage */ }
    return 'auto';
}

function setThemePref(pref) {
    try { localStorage.setItem(THEME_STORAGE_KEY, pref); } catch (e) { /* non-fatal */ }
    applyTheme();
}

function getCachedCoords() {
    try {
        const raw = localStorage.getItem(THEME_COORDS_KEY);
        if (raw) return JSON.parse(raw);
    } catch (e) { /* ignore */ }
    return null;
}

function requestCoordsThenApply() {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
        (pos) => {
            const coords = { lat: pos.coords.latitude, lon: pos.coords.longitude };
            try { localStorage.setItem(THEME_COORDS_KEY, JSON.stringify(coords)); } catch (e) { /* non-fatal */ }
            applyTheme();
        },
        () => { /* denied/unavailable — DEFAULT_COORDS keeps auto mode usable */ },
        { timeout: 8000 }
    );
}

// Classic NOAA/"sunrise equation" approximation (solar zenith 90.833°,
// i.e. accounting for atmospheric refraction and the sun's radius) —
// accurate to within a few minutes, which is all "is it day or night"
// needs. Returns UTC decimal hours, or null for polar day/night.
function sunTimesUTC(lat, lon, date) {
    const rad = Math.PI / 180;
    const dayOfYear = Math.floor((Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) - Date.UTC(date.getFullYear(), 0, 0)) / 86400000);
    const lngHour = lon / 15;

    function calc(isSunrise) {
        const t = dayOfYear + ((isSunrise ? 6 : 18) - lngHour) / 24;
        const M = 0.9856 * t - 3.289;
        let L = M + 1.916 * Math.sin(M * rad) + 0.020 * Math.sin(2 * M * rad) + 282.634;
        L = ((L % 360) + 360) % 360;
        let RA = Math.atan(0.91764 * Math.tan(L * rad)) / rad;
        RA = ((RA % 360) + 360) % 360;
        RA += (Math.floor(L / 90) * 90) - (Math.floor(RA / 90) * 90);
        RA /= 15;
        const sinDec = 0.39782 * Math.sin(L * rad);
        const cosDec = Math.cos(Math.asin(sinDec));
        const cosH = (Math.cos(90.833 * rad) - sinDec * Math.sin(lat * rad)) / (cosDec * Math.cos(lat * rad));
        if (cosH > 1 || cosH < -1) return null; // sun never rises/sets today at this latitude
        let H = isSunrise ? 360 - Math.acos(cosH) / rad : Math.acos(cosH) / rad;
        H /= 15;
        const T = H + RA - 0.06571 * t - 6.622;
        return ((T - lngHour) % 24 + 24) % 24;
    }
    return { sunrise: calc(true), sunset: calc(false) };
}

function isNightNow(coords) {
    const now = new Date();
    const { sunrise, sunset } = sunTimesUTC(coords.lat, coords.lon, now);
    if (sunrise === null || sunset === null) return false; // polar edge case — default to day
    const nowUTC = now.getUTCHours() + now.getUTCMinutes() / 60;
    return !(nowUTC >= sunrise && nowUTC < sunset);
}

function resolveEffectiveTheme(pref) {
    if (pref === 'light' || pref === 'dark') return pref;
    const coords = getCachedCoords() || DEFAULT_COORDS;
    if (!getCachedCoords()) requestCoordsThenApply(); // ask once in the background; re-applies with real coords once granted
    return isNightNow(coords) ? 'dark' : 'light';
}

function applyTheme() {
    const pref = getThemePref();
    document.documentElement.setAttribute('data-bs-theme', resolveEffectiveTheme(pref));
    const sel = document.getElementById('theme-select');
    if (sel) sel.value = pref;
}

document.addEventListener('DOMContentLoaded', () => {
    applyTheme();
    const sel = document.getElementById('theme-select');
    if (sel) sel.addEventListener('change', (e) => setThemePref(e.target.value));
});
setInterval(() => { if (getThemePref() === 'auto') applyTheme(); }, AUTO_RECHECK_MS);
