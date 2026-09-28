// Dashboard entry point (an ES module: no build step, no globals of our
// own). Globals it relies on: t / getLanguage / formatApiError / applyI18n
// from i18n.js and `bootstrap` from the vendored Bootstrap bundle, both
// loaded as classic scripts before this one.
import { api } from './lib/api.js';
import { $ } from './lib/dom.js';
import { isAdmin, refresh, state } from './state.js';
import * as account from './features/account.js';
import * as alerts from './features/alerts.js';
import * as backups from './features/backups.js';
import * as calendars from './features/calendars.js';
import * as logs from './features/logs.js';
import * as media from './features/media.js';
import * as nav from './features/nav.js';
import * as playback from './features/playback.js';
import * as schedules from './features/schedules.js';
import * as speakers from './features/speakers.js';
import * as system from './features/system.js';
import * as users from './features/users.js';
import * as zones from './features/zones.js';

function renderUserBadge() {
    const me = state.me;
    $('current-user').textContent = `${me.full_name || me.username} (${t(`users.${me.role}`)})`;
}

// Everything built with t() at render time is redrawn from the data
// already in memory — no refetch, and no form the user is filling in is
// touched.
function onLanguageChange() {
    nav.refreshTitle();
    if (state.me) renderUserBadge();
    speakers.render();
    zones.render();
    media.render();
    schedules.render();
    calendars.render();
    playback.renderHistory();
    if (isAdmin()) {
        users.render();
        logs.render();
        backups.rerender();
        system.renderTimeLabels();
    }
}

async function loadData() {
    try {
        await refresh('speakers', 'zones', 'media', 'schedules', 'calendars', 'overlaps', 'users');
        await playback.loadHistory();
    } catch (err) {
        alerts.showLoadError(err.message, loadData);
    }
}

async function boot() {
    nav.init();
    account.init();
    let me;
    try {
        me = await api('/api/auth/me');
    } catch (err) {
        alerts.showLoadError(err.message, () => window.location.reload());
        return;
    }
    state.me = me;
    state.role = me.role;
    renderUserBadge();

    playback.init();
    media.init();
    speakers.init();
    zones.init();
    schedules.init();
    calendars.init();
    if (isAdmin()) {
        users.init();
        system.init();
        logs.init();
        backups.init();
    }
    document.addEventListener('zc:languagechange', onLanguageChange);

    await loadData();
    if (isAdmin()) {
        alerts.showOnce();
        system.loadVersion();
    }
}

boot();
