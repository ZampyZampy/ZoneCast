// Users (admin only): list, create, edit, delete.
import { api } from '../lib/api.js';
import { $, actionButton, esc, modal, onAction, run } from '../lib/dom.js';
import { onDataChange, refresh, state } from '../state.js';

export function render() {
    $('users-body').innerHTML = state.users.map(u => `
        <tr>
            <td>${esc(u.username)}${u.is_protected ? ` <span class="badge bg-secondary">${esc(t('users.defaultBadge'))}</span>` : ''}</td>
            <td>${esc(u.full_name)}</td>
            <td>${esc(t(`users.${u.role}`))}</td>
            <td class="text-end table-actions">
                ${actionButton('edit', u.id, 'bi-pencil', 'action.edit', 'btn-outline-primary')}
                ${u.is_protected ? '' : actionButton('delete', u.id, 'bi-trash', 'action.delete', 'btn-outline-danger')}
            </td>
        </tr>`).join('');
}

function edit(id) {
    const u = state.users.find(x => x.id === id);
    if (!u) return;
    $('user-edit-id').value = u.id;
    $('user-edit-username').value = u.username;
    $('user-edit-fullname').value = u.full_name || '';
    $('user-edit-role').value = u.role;
    // An already-demoted default admin (possible before 1.6.1) stays
    // editable, so it can be made an administrator again.
    const roleLocked = u.is_protected && u.role === 'admin';
    $('user-edit-role').disabled = roleLocked;
    $('user-edit-role-locked').classList.toggle('d-none', !roleLocked);
    $('user-edit-password').value = '';
    modal('user-edit-modal').show();
}

export function init() {
    onDataChange((changed) => { if (changed.has('users')) render(); });

    $('user-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const payload = {
            username: $('user-username').value,
            full_name: $('user-fullname').value,
            password: $('user-password').value,
            role: $('user-role').value,
        };
        run(async () => {
            await api('/api/auth/users', { method: 'POST', body: payload });
            modal('user-modal').hide();
            $('user-form').reset();
            await refresh('users');
        }, { button: e.submitter, success: t('toast.userCreated') });
    });

    $('user-edit-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const payload = { full_name: $('user-edit-fullname').value, role: $('user-edit-role').value };
        const newPassword = $('user-edit-password').value;
        if (newPassword) payload.new_password = newPassword;
        run(async () => {
            await api(`/api/auth/users/${$('user-edit-id').value}`, { method: 'PUT', body: payload });
            modal('user-edit-modal').hide();
            await refresh('users');
        }, { button: e.submitter, success: t('toast.userUpdated') });
    });

    onAction($('users-body'), {
        edit: (id) => edit(id),
        delete: (id, btn) => {
            if (!confirm(t('confirm.deleteUser'))) return;
            run(async () => {
                await api(`/api/auth/users/${id}`, { method: 'DELETE' });
                await refresh('users');
            }, { button: btn });
        },
    });
}
