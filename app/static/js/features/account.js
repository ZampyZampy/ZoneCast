// The signed-in user's own account: log out, password, two-factor auth.
import { api } from '../lib/api.js';
import { $, askPassword, modal, run, toast } from '../lib/dom.js';

const views = () => [
    [$('tfa-status-view'), $('tfa-status-footer')],
    [$('tfa-setup-view'), $('tfa-setup-footer')],
    [$('tfa-codes-view'), $('tfa-codes-footer')],
];

function showView(view) {
    views().forEach(([v, f]) => {
        v.classList.toggle('d-none', v !== view);
        f.classList.toggle('d-none', v !== view);
    });
}

async function loadTfaStatus() {
    try {
        const s = await api('/api/auth/me/2fa');
        $('tfa-enable-btn').classList.toggle('d-none', s.enabled);
        $('tfa-disable-btn').classList.toggle('d-none', !s.enabled);
        $('tfa-regen-btn').classList.toggle('d-none', !s.enabled);
        $('tfa-status-text').textContent = s.enabled
            ? t('security.statusEnabled', { n: s.remaining_recovery_codes })
            : t('security.statusDisabled');
    } catch (err) {
        $('tfa-status-text').textContent = err.message;
    }
    showView($('tfa-status-view'));
}

function showRecoveryCodes(codes) {
    $('tfa-codes-list').textContent = codes.join('\n');
    showView($('tfa-codes-view'));
}

let pendingCodes = null;

// Bootstrap can't stack two dialogs: close the security one first, then
// ask for the password in its own dialog.
async function promptPassword(message) {
    await new Promise((resolve) => {
        $('security-modal').addEventListener('hidden.bs.modal', resolve, { once: true });
        modal('security-modal').hide();
    });
    return askPassword(message);
}

function showError(box, message) {
    box.textContent = message;
    box.classList.remove('d-none');
}

export function init() {
    $('logout-btn').addEventListener('click', async () => {
        try { await api('/api/auth/logout', { method: 'POST' }); } finally { window.location.href = '/login'; }
    });

    $('password-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const errBox = $('pw-error');
        errBox.classList.add('d-none');
        if ($('pw-new').value !== $('pw-confirm').value) {
            showError(errBox, t('toast.passwordMismatch'));
            return;
        }
        const button = e.submitter;
        if (button) button.disabled = true;
        try {
            await api('/api/auth/me/password', {
                method: 'POST',
                body: { current_password: $('pw-current').value, new_password: $('pw-new').value },
            });
            modal('password-modal').hide();
            $('password-form').reset();
            toast(t('toast.passwordUpdated'));
        } catch (err) {
            showError(errBox, err.message);
        } finally {
            if (button) button.disabled = false;
        }
    });

    // Reopening the dialog shows fresh status — or the recovery codes that
    // were just generated while it was closed for the password prompt.
    $('security-modal').addEventListener('show.bs.modal', () => {
        if (pendingCodes) { showRecoveryCodes(pendingCodes); pendingCodes = null; } else loadTfaStatus();
    });

    $('tfa-enable-btn').addEventListener('click', (e) => run(async () => {
        const setup = await api('/api/auth/me/2fa/setup', { method: 'POST' });
        $('tfa-secret').value = setup.secret;
        $('tfa-qr-container').innerHTML = setup.qr_svg;  // generated server-side by the qrcode library
        $('tfa-confirm-code').value = '';
        $('tfa-setup-error').classList.add('d-none');
        showView($('tfa-setup-view'));
    }, { button: e.currentTarget }));

    $('tfa-cancel-btn').addEventListener('click', () => showView($('tfa-status-view')));

    $('tfa-confirm-btn').addEventListener('click', async (e) => {
        const button = e.currentTarget;
        const errBox = $('tfa-setup-error');
        errBox.classList.add('d-none');
        button.disabled = true;
        try {
            const result = await api('/api/auth/me/2fa/confirm', { method: 'POST', body: { code: $('tfa-confirm-code').value } });
            showRecoveryCodes(result.recovery_codes);
        } catch (err) {
            showError(errBox, err.message);
        } finally {
            button.disabled = false;
        }
    });

    $('tfa-codes-done-btn').addEventListener('click', loadTfaStatus);

    $('tfa-disable-btn').addEventListener('click', async () => {
        const password = await promptPassword(t('security.promptDisable'));
        if (password) {
            try {
                await api('/api/auth/me/2fa/disable', { method: 'POST', body: { password } });
                toast(t('toast.tfaDisabled'));
            } catch (err) { toast(err.message, 'danger'); }
        }
        modal('security-modal').show();
    });

    $('tfa-regen-btn').addEventListener('click', async () => {
        const password = await promptPassword(t('security.promptRegen'));
        if (password) {
            try {
                const result = await api('/api/auth/me/2fa/recovery-codes/regenerate', { method: 'POST', body: { password } });
                pendingCodes = result.recovery_codes;
            } catch (err) { toast(err.message, 'danger'); }
        }
        modal('security-modal').show();
    });
}
