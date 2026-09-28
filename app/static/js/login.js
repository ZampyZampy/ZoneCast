// Sign-in page (password, then the 2FA code when the account has it).
// Globals from i18n.js: t, formatApiError.
const loginForm = document.getElementById('login-form');
const twofaForm = document.getElementById('twofa-form');

function showError(box, message) {
    box.textContent = message;
    box.classList.remove('d-none');
}

async function post(url, body) {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    return { res, data };
}

loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const errBox = document.getElementById('login-error');
    const submit = document.getElementById('login-submit');
    errBox.classList.add('d-none');
    submit.disabled = true;
    try {
        const { res, data } = await post('/api/auth/login', {
            username: document.getElementById('username').value,
            password: document.getElementById('password').value,
        });
        if (res.ok && data.requires_2fa) {
            loginForm.classList.add('d-none');
            twofaForm.classList.remove('d-none');
            document.getElementById('twofa-code').focus();
        } else if (res.ok) {
            window.location.href = '/';
        } else {
            showError(errBox, data.detail ? formatApiError(data.detail, res.status) : t('login.error'));
        }
    } catch (err) {
        showError(errBox, t('login.error'));
    } finally {
        submit.disabled = false;
    }
});

twofaForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const errBox = document.getElementById('twofa-error');
    errBox.classList.add('d-none');
    try {
        const { res, data } = await post('/api/auth/login/2fa', { code: document.getElementById('twofa-code').value });
        if (res.ok) {
            window.location.href = '/';
        } else {
            showError(errBox, data.detail ? formatApiError(data.detail, res.status) : t('login.invalidCode'));
        }
    } catch (err) {
        showError(errBox, t('login.invalidCode'));
    }
});

document.getElementById('twofa-back').addEventListener('click', () => {
    twofaForm.classList.add('d-none');
    loginForm.classList.remove('d-none');
    document.getElementById('password').value = '';
});
