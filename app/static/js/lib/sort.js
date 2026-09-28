// Click (or Enter/Space) on a <th data-sort> to sort A→Z, again for Z→A.
// Rows are sorted into a copy at render time, so a data refresh keeps the
// chosen order instead of silently reverting to the server's.
import { compareValues } from './format.js';

const sortState = {};

export function sortedRows(table, rows, keyFns) {
    const s = sortState[table];
    const keyFn = s && keyFns[s.field];
    if (!keyFn) return rows;
    return [...rows].sort((a, b) => s.dir * compareValues(keyFn(a), keyFn(b)));
}

export function makeSortable(table, keyFns, render) {
    const headers = document.querySelectorAll(`#tab-${table} thead th[data-sort]`);
    const update = () => headers.forEach(h => {
        const s = sortState[table];
        h.setAttribute('aria-sort', s && s.field === h.dataset.sort ? (s.dir === 1 ? 'ascending' : 'descending') : 'none');
        h.querySelector('.sort-indicator')?.remove();
        if (s && s.field === h.dataset.sort) {
            h.insertAdjacentHTML('beforeend', ` <span class="sort-indicator" aria-hidden="true">${s.dir === 1 ? '▲' : '▼'}</span>`);
        }
    });
    // applyI18n() rewrites the header text on a language switch, which
    // drops the arrow: put it back.
    document.addEventListener('zc:languagechange', update);
    headers.forEach(th => {
        if (!keyFns[th.dataset.sort]) return;
        th.tabIndex = 0;
        const toggle = () => {
            const current = sortState[table];
            const dir = current && current.field === th.dataset.sort && current.dir === 1 ? -1 : 1;
            sortState[table] = { field: th.dataset.sort, dir };
            update();
            render();
        };
        th.addEventListener('click', toggle);
        th.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
        });
    });
    update();
}
