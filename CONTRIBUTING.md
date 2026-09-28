# Contributing

This is a small side project, but issues and pull requests are welcome.

## Reporting a bug

Open an issue with: what you expected, what happened instead, and
your setup (native/Docker, OS, speaker brand/model if relevant). Logs
from Sistema > Log (or `journalctl -u zonecast`) are usually the
fastest way to get to the bottom of it.

For a security vulnerability, please see [SECURITY.md](SECURITY.md)
instead of opening a public issue.

## Setting up a dev environment

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # Linux/Mac

pip install -r requirements-dev.txt
copy .env.example .env            # Windows: copy — Linux/Mac: cp
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Requires **ffmpeg** on the PATH for anything that touches media
upload/conversion (not needed to run the test suite — see below).
The database schema is created and upgraded automatically at startup
(Alembic, see `app/migrate.py`).

## Dependencies

`requirements.in` (runtime) and `requirements-dev.in` (tests and
tools) list the direct dependencies; `requirements.txt` and
`requirements-dev.txt` are generated from them, with every transitive
package pinned and hashed, and are what installs and CI use. After
editing a `.in` file, regenerate both:

```bash
uv pip compile requirements.in --universal --python-version 3.11 --generate-hashes -o requirements.txt
uv pip compile requirements-dev.in --universal --python-version 3.11 --generate-hashes -o requirements-dev.txt
```

CI fails if the two files don't match what these commands produce
(a Dependabot PR that only touches `requirements.txt` needs this too).

## Schema changes

Change `app/models.py`, then add a migration under
`migrations/versions/` (`alembic revision --autogenerate -m "..."`,
then review it: SQLite needs `batch_alter_table` for most changes).
`alembic check` must report no differences — CI runs it.

## Running the tests

```bash
ruff check .
pytest --ignore=tests/e2e                  # backend, ~20 s
playwright install chromium                # once
pytest tests/e2e                           # the dashboard in a real browser
```

Tests run against an isolated temp SQLite DB and temp
media/backups directories (see `tests/conftest.py`), so they're safe
to run repeatedly without touching any real installation. They never
talk to real speakers: device checks and pushes are stubbed, and any
HTTP request to a host other than the test server fails the test.
The browser tests start their own ZoneCast process on a random local
port with the same stubs (`tests/e2e/_server.py`).

CI (`.github/workflows/tests.yml`) runs lint, the lock-file check, a
JavaScript syntax check, the migrations check, the backend suite on
Python 3.11–3.14 with coverage, the browser tests, and a Docker build
on every push/PR to `main`.

## Dashboard code

The dashboard is plain ES modules, no build step: `app/static/js/main.js`
wires the feature modules in `features/`, shared helpers live in `lib/`.
Bootstrap and Bootstrap Icons are vendored under `app/static/vendor/`
(no CDN: the dashboard works on a LAN without internet). The Content
Security Policy allows only same-origin scripts, so no inline `<script>`
or `onclick=` — use `data-action` attributes and the delegated handlers
in `lib/dom.js`. Every user-visible string goes through `t()` /
`data-i18n` with a key in all seven languages in `i18n.js`
(`tests/test_i18n.py` checks it); API errors carry a stable `code`
translated as `error.<code>` (`tests/test_errors.py`).

## Pull requests

- Keep PRs focused — one change per PR is easier to review than a
  bundle of unrelated fixes.
- Add/update a test for behavior changes where practical.
- If the change is user-visible, add a line to `CHANGELOG` in
  `app/version.py` and bump `APP_VERSION` — it's the only place that
  needs updating, and it's what shows up in System > Information for
  anyone running the app.
- No strict style guide beyond what's already in the codebase — match
  the surrounding code.
