// Data shared across tabs, as last fetched from the API.
import { api } from './lib/api.js';

export const state = {
    me: null,
    role: null,
    speakers: [],
    zones: [],
    media: [],
    schedules: [],
    calendars: [],
    overlaps: [],
    users: [],
};

export const isAdmin = () => state.role === 'admin';

const ENDPOINTS = {
    speakers: '/api/speakers',
    zones: '/api/zones',
    media: '/api/media',
    schedules: '/api/schedules',
    calendars: '/api/calendars',
    overlaps: '/api/schedules/overlaps',
    users: '/api/auth/users',
};

const listeners = new Set();

// Called with the set of collection names that changed.
export function onDataChange(fn) { listeners.add(fn); }

// Re-fetches only the collections a change can affect, then lets every
// view redraw from `state` — instead of reloading everything (and
// resetting every form on the page) after each save.
export async function refresh(...names) {
    const wanted = names.filter(n => n !== 'users' || isAdmin());
    const results = await Promise.all(wanted.map(n => api(ENDPOINTS[n])));
    wanted.forEach((n, i) => { state[n] = results[i]; });
    const changed = new Set(wanted);
    listeners.forEach(fn => fn(changed));
}

export const zoneName = (id) => state.zones.find(z => z.id === id)?.name;
export const speakerName = (id) => state.speakers.find(s => s.id === id)?.name;
export const mediaName = (id) => state.media.find(m => m.id === id)?.original_filename;
