"""
App version and changelog shown in Sistema > Informazioni. Bump
APP_VERSION and add a CHANGELOG entry whenever a user-visible change
ships — this is the only place either needs updating.
"""

APP_VERSION = "1.5.2"

# Newest first. Each entry: version, date (YYYY-MM-DD), list of
# one-line change descriptions. English, matching the UI's default
# language — unlike the rest of the dashboard this text isn't run
# through app/static/js/i18n.js, so non-English viewers see it in
# English regardless of their selected language.
CHANGELOG = [
    {
        "version": "1.5.2",
        "date": "2026-09-23",
        "changes": [
            "Operators can now create/edit/delete zones and speakers (including moving a speaker between zones), matching what the dashboard already let them attempt — only device actions (Apply now, Backup) and the admin-only sections stay admin-restricted",
        ],
    },
    {
        "version": "1.5.1",
        "date": "2026-09-23",
        "changes": [
            "Fixed a crash when an operator (non-admin) account saved a schedule, caused by re-removing already-hidden admin-only nav items on every refresh",
        ],
    },
    {
        "version": "1.5.0",
        "date": "2026-09-23",
        "changes": [
            "Fixed NTP server display showing the resolved IP address instead of the configured hostname",
            "Language and theme selectors moved into the mobile sidebar drawer, decluttering the top bar on small screens",
        ],
    },
    {
        "version": "1.4.0",
        "date": "2026-09-23",
        "changes": [
            "ZoneCast logo and favicon, in both the sidebar/login and the browser tab",
            "Light/dark theme with an automatic mode based on real sunrise/sunset — accessible to every user from the top bar, not just admins",
            "Per-schedule holiday calendar (IT/US/GB/FR/DE/ES/JP/CN), not just Italy",
            "Fixed sudo-based system actions (NTP/timezone/network) being silently blocked by systemd hardening incompatible with that design",
            "Fixed NTP server changes on chrony being misapplied when NTP sync was toggled off (backend was detected by \"currently active\", not \"installed\")",
            "Fixed DNS servers always displaying the systemd-resolved stub address instead of the real configured servers",
            "Fixed a changed IP address staying reachable at the old address too, from a leftover installer network config",
            "Fixed the network-change confirmation prompt not appearing when reconnecting at the new address from a fresh page load",
            "Reordered the System panel's cards for a tidier desktop layout",
        ],
    },
    {
        "version": "1.3.0",
        "date": "2026-09-23",
        "changes": [
            "Version number and changelog available from System > Information",
            "Language menu (EN/IT/ZH/JA/DE/ES/FR) with full interface translation",
            "Speaker brand: dropdown of major multicast brands instead of free text",
            "Visual warning when an audio file has a high bass level",
            "First-login alerts for failed schedules or low disk space",
        ],
    },
    {
        "version": "1.2.0",
        "date": "2026-09-22",
        "changes": [
            "System panel: host resource monitoring (disk, RAM, CPU)",
            "Configurable NTP servers from the System panel (supports both systemd-timesyncd and chrony)",
            "Fixed manual time being silently overwritten when NTP was active",
            "Multicast push to Fanvil devices now aligns the whole device, clearing slots ZoneCast doesn't manage",
        ],
    },
    {
        "version": "1.1.0",
        "date": "2026-09-21",
        "changes": [
            "Multicast paging volume configurable per speaker (brands that support it)",
            "Full mobile UI overhaul: collapsible sidebar, adaptive tables and buttons",
        ],
    },
    {
        "version": "1.0.0",
        "date": "2026-09-01",
        "changes": [
            "First release: zones, schedules with Italian holidays, multicast RTP paging",
            "Fanvil config backup/restore, persistent logs, 2FA, full export/import",
        ],
    },
]
