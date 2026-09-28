// Light/dark mode: a per-viewer preference (localStorage, same pattern
// as the language choice in i18n.js), not a server setting. "Auto"
// follows real sunrise/sunset where the installation is — the server
// puts its timezone's coordinates on <html data-sun-lat/lon> (see
// services/sun_position.py): the browser's geolocation isn't available
// on plain-HTTP LAN deployments and is disabled by Permissions-Policy.
//
// Loaded synchronously in <head>, so the theme is set before the first
// paint (no light flash at night); Bootstrap 5.3's data-bs-theme does the
// actual restyling.
(function () {
    const STORAGE_KEY = 'zc_theme_pref';           // 'light' | 'dark' | 'auto'
    const AUTO_RECHECK_MS = 10 * 60 * 1000;        // day/night can flip while a tab stays open
    const FALLBACK = { lat: 41.9, lon: 12.5 };     // Rome, the project's default TIMEZONE

    function getPref() {
        try {
            const v = localStorage.getItem(STORAGE_KEY);
            if (v === 'light' || v === 'dark' || v === 'auto') return v;
        } catch (e) { /* private mode / blocked storage */ }
        return 'auto';
    }

    function setPref(pref) {
        try { localStorage.setItem(STORAGE_KEY, pref); } catch (e) { /* non-fatal */ }
        apply();
    }

    function siteCoords() {
        const d = document.documentElement.dataset;
        const lat = parseFloat(d.sunLat), lon = parseFloat(d.sunLon);
        return Number.isFinite(lat) && Number.isFinite(lon) ? { lat, lon } : FALLBACK;
    }

    // Classic NOAA "sunrise equation" (zenith 90.833°: refraction plus the
    // sun's radius) — within minutes, plenty for day vs night. Returns UTC
    // decimal hours, or null for polar day/night.
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

    function isNightNow() {
        const { lat, lon } = siteCoords();
        const now = new Date();
        const { sunrise, sunset } = sunTimesUTC(lat, lon, now);
        if (sunrise === null || sunset === null) return false; // polar edge case — default to day
        const nowUTC = now.getUTCHours() + now.getUTCMinutes() / 60;
        const isDay = sunrise <= sunset
            ? nowUTC >= sunrise && nowUTC < sunset
            : nowUTC >= sunrise || nowUTC < sunset; // daylight spans 00:00 UTC
        return !isDay;
    }

    function apply() {
        const pref = getPref();
        const effective = pref === 'auto' ? (isNightNow() ? 'dark' : 'light') : pref;
        document.documentElement.setAttribute('data-bs-theme', effective);
        const sel = document.getElementById('theme-select');
        if (sel) sel.value = pref;
    }

    apply();
    document.addEventListener('DOMContentLoaded', () => {
        apply();
        const sel = document.getElementById('theme-select');
        if (sel) sel.addEventListener('change', (e) => setPref(e.target.value));
    });
    setInterval(() => { if (getPref() === 'auto') apply(); }, AUTO_RECHECK_MS);
})();
