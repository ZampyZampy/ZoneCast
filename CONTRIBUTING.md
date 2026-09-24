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

## Running the tests

```bash
pytest
```

Tests run against an isolated temp SQLite DB and temp
media/backups directories (see `tests/conftest.py`), so they're safe
to run repeatedly without touching any real installation. CI
(`.github/workflows/tests.yml`) runs the same suite on Python 3.11 and
3.13 on every push/PR to `main`.

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
