// Tabs, the page title, and the mobile sidebar drawer.
import { $ } from '../lib/dom.js';

const showHooks = {};
const hideHooks = {};
let current = 'play';

export const currentTab = () => current;
export const onTabShown = (tab, fn) => { (showHooks[tab] ||= []).push(fn); };
export const onTabHidden = (tab, fn) => { (hideHooks[tab] ||= []).push(fn); };

const sidebar = () => document.querySelector('.sidebar');

function setSidebar(open) {
    sidebar().classList.toggle('mobile-open', open);
    $('sidebar-backdrop').classList.toggle('show', open);
    $('sidebar-toggle-btn').setAttribute('aria-expanded', String(open));
}

export function refreshTitle() {
    const btn = document.querySelector(`#main-tabs .nav-item[data-tab="${current}"]`);
    $('page-title').textContent = btn?.querySelector('span')?.textContent || current;
}

export function showTab(tab) {
    if (!$(`tab-${tab}`)) return;
    const previous = current;
    current = tab;
    document.querySelectorAll('#main-tabs .nav-item').forEach(b => {
        const active = b.dataset.tab === tab;
        b.classList.toggle('active', active);
        if (active) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    });
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.toggle('d-none', p.id !== `tab-${tab}`));
    refreshTitle();
    setSidebar(false);
    if (previous !== tab) (hideHooks[previous] || []).forEach(fn => fn());
    (showHooks[tab] || []).forEach(fn => fn());
}

// On phones the language/theme selectors and the account buttons move
// into the drawer. The DOM nodes themselves move (not clones), so ids
// stay unique and their listeners keep working.
function relocator(el, sidebarSlot) {
    const home = el.parentNode;
    const nextSibling = el.nextSibling;
    return () => {
        if (window.innerWidth < 768) {
            if (el.parentNode !== sidebarSlot) sidebarSlot.appendChild(el);
        } else if (el.parentNode !== home) {
            home.insertBefore(el, nextSibling);
        }
    };
}

export function init() {
    document.querySelectorAll('#main-tabs .nav-item').forEach(btn => {
        btn.addEventListener('click', () => showTab(btn.dataset.tab));
    });
    $('sidebar-toggle-btn').addEventListener('click', () => setSidebar(!sidebar().classList.contains('mobile-open')));
    $('sidebar-backdrop').addEventListener('click', () => setSidebar(false));

    const placers = [
        relocator($('account-actions'), $('sidebar-account-slot')),
        relocator($('topbar-prefs'), $('sidebar-prefs-slot')),
    ];
    const place = () => placers.forEach(p => p());
    place();
    let timer;
    window.addEventListener('resize', () => { clearTimeout(timer); timer = setTimeout(place, 150); });
    $('account-actions').querySelectorAll('button').forEach(b => b.addEventListener('click', () => setSidebar(false)));
}
