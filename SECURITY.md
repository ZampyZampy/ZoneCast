# Security Policy

## Supported versions

Only the latest version on the `main` branch is supported. There's no
long-term-support branch — check System > Information in a running
instance (or `app/version.py`) for the installed version, and update
by pulling the latest code and redeploying.

## Reporting a vulnerability

Please **do not** open a public issue for a security vulnerability.
Instead, use GitHub's private reporting:

**Security tab > Report a vulnerability** on this repository (uses
[GitHub Private Vulnerability Reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)).

Please include: the affected version/commit, steps to reproduce, and
what you'd expect to happen instead. This is a small side project
maintained in spare time — there's no fixed SLA, but genuine
vulnerabilities will be prioritized over feature work.

## Security-relevant design notes

A few things worth knowing before deploying this on a network you
care about:

- **Device credentials at rest**: each speaker's web-admin password is
  encrypted at rest (`app/services/crypto.py`, Fernet symmetric
  encryption) with a key generated on first startup
  (`data/secret.key`). That key file is as sensitive as the database
  itself — anyone with both can decrypt every stored device password.
  Back it up together with the database (the built-in export bundle
  already does this), and never commit it or the `data/` directory to
  version control (see `.gitignore`).
- **`SECRET_KEY` in `.env`**: signs session cookies. Treat it like a
  password — generate a long random value per installation (the
  native installer does this automatically), never reuse the
  `.env.example` placeholder, and rotating it invalidates every active
  session.
- **`sudo` escalation for System > Network/Time**: the native systemd
  deployment (`deploy/zonecast.service`) deliberately runs the app
  process *without* `NoNewPrivileges`/`ProtectSystem=strict`/a
  narrowed `CapabilityBoundingSet`, because those settings break the
  app's `sudo -n` escalation path to `deploy/zonecast-netctl.sh` (see
  the extensive comment in that service file for exactly why). That
  helper script is the only thing the app user can run as root
  (enforced via a dedicated `/etc/sudoers.d/zonecast-netctl` entry,
  not broad `sudo` access), and it only accepts a fixed set of
  subcommands (network apply/confirm, NTP servers, timezone, manual
  clock set) — review it before trusting it on a system where the
  `zonecast` user's integrity matters to you.
- **Fanvil CGI auto-provisioning is best-effort**: the exact
  parameter-write syntax (`app/services/drivers/fanvil_http.py`) isn't
  publicly documented in a fully reliable way and varies by firmware
  family. See section 2 of the README for how to safely validate it
  against your own devices before relying on it for a whole fleet.
- **Default credentials**: a fresh install ships with
  `admin`/`admin` (or whatever `DEFAULT_ADMIN_USERNAME` /
  `DEFAULT_ADMIN_PASSWORD` are set to in `.env`) — change this
  immediately after first login, on every installation.
