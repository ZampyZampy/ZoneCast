// DOM helpers shared by every feature module. Globals used: t (i18n.js),
// bootstrap (vendor bundle).

export const $ = (id) => document.getElementById(id);

// Every server-provided value that goes into an innerHTML template must
// pass through esc(): names, filenames and descriptions are free text,
// and log messages even quote usernames typed at the (unauthenticated)
// login form.
export function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
    ));
}

// Success/info toasts fade after 4 s; errors stay until dismissed — a
// failure message that vanishes before it's read is a failure unreported.
export function toast(message, kind = 'success') {
    const el = document.createElement('div');
    const sticky = kind === 'danger';
    el.className = `alert alert-${kind} shadow${sticky ? ' alert-dismissible' : ''}`;
    el.setAttribute('role', sticky ? 'alert' : 'status');
    el.textContent = message;
    if (sticky) {
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'btn-close';
        close.setAttribute('aria-label', t('common.close'));
        close.addEventListener('click', () => el.remove());
        el.appendChild(close);
    }
    $('toast-container').appendChild(el);
    if (!sticky) setTimeout(() => el.remove(), 4000);
}

// Runs `fn` with `button` disabled, so a double-click (or a second click
// while a slow request is pending) can't start the action twice. With
// `busyLabel` the button also says what it's doing ("Applying…") — some
// device writes and exports take several seconds.
export async function withBusy(button, fn, { busyLabel } = {}) {
    if (button && button.disabled) return undefined;
    let saved;
    if (button) {
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        if (busyLabel) {
            saved = button.innerHTML;
            const label = button.querySelector('.btn-label');
            if (label) label.textContent = ` ${busyLabel}`;
            else button.textContent = busyLabel;
        }
    }
    try {
        return await fn();
    } finally {
        if (button) {
            if (saved !== undefined) button.innerHTML = saved;
            button.disabled = false;
            button.removeAttribute('aria-busy');
        }
    }
}

// One helper for "do it, report failure": every action in the dashboard
// goes through here instead of ad-hoc try/catch blocks.
export async function run(fn, { button, success, busyLabel } = {}) {
    try {
        const result = await withBusy(button, fn, { busyLabel });
        if (success) toast(typeof success === 'function' ? success(result) : success);
        return result;
    } catch (err) {
        toast(err.message, 'danger');
        return undefined;
    }
}

export const modal = (id) => bootstrap.Modal.getOrCreateInstance($(id));

// Replaces a <select>'s options, keeping the current choice when it still
// exists — re-rendering must never silently change what "Play now" does.
export function fillSelect(select, items, { value = i => i.id, label, empty } = {}) {
    const current = select.value;
    const options = items.map(i => `<option value="${esc(value(i))}">${esc(label(i))}</option>`);
    if (empty) options.unshift(`<option value="">${esc(empty)}</option>`);
    select.innerHTML = options.join('');
    if ([...select.options].some(o => o.value === current)) select.value = current;
}

// Icon+label action button; the label doubles as the accessible name,
// since on phones the text part is hidden and only the icon shows.
// `titleKey` gives the tooltip a longer explanation than the label.
export function actionButton(action, id, icon, labelKey, style = 'btn-outline-secondary', titleKey = null) {
    const label = esc(t(labelKey));
    const title = titleKey ? esc(t(titleKey)) : label;
    return `<button type="button" class="btn btn-sm ${style}" data-action="${action}" data-id="${esc(id)}" aria-label="${label}" title="${title}">`
        + `<i class="bi ${icon}" aria-hidden="true"></i><span class="btn-label"> ${label}</span></button>`;
}

// One delegated click listener per container, dispatching on
// data-action — no inline onclick handlers (the CSP forbids them).
export function onAction(container, handlers) {
    container.addEventListener('click', (e) => {
        const target = e.target.closest('[data-action]');
        if (!target || !container.contains(target)) return;
        const handler = handlers[target.dataset.action];
        if (handler) handler(Number(target.dataset.id), target, e);
    });
}

// Asks for the account password in a proper password field (a
// window.prompt would show it in clear text). Resolves null on cancel.
export function askPassword(message) {
    return new Promise((resolve) => {
        const form = $('password-prompt-form');
        const input = $('password-prompt-input');
        const dlg = modal('password-prompt-modal');
        let answered = false;
        $('password-prompt-text').textContent = message;
        input.value = '';
        const onSubmit = (e) => {
            e.preventDefault();
            answered = true;
            dlg.hide();
            resolve(input.value);
        };
        const onHidden = () => {
            form.removeEventListener('submit', onSubmit);
            $('password-prompt-modal').removeEventListener('hidden.bs.modal', onHidden);
            input.value = '';
            if (!answered) resolve(null);
        };
        form.addEventListener('submit', onSubmit);
        $('password-prompt-modal').addEventListener('hidden.bs.modal', onHidden);
        $('password-prompt-modal').addEventListener('shown.bs.modal', () => input.focus(), { once: true });
        dlg.show();
    });
}

export function isVisible(el) {
    return !!el && !el.classList.contains('d-none') && !document.hidden;
}
