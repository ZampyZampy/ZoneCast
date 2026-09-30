"""
App version and changelog shown in Sistema > Informazioni. Bump
APP_VERSION and add a CHANGELOG entry whenever a user-visible change
ships — this is the only place either needs updating.
"""

APP_VERSION = "1.6.2"

# Newest first. Each entry: version, date (YYYY-MM-DD), list of
# one-line change descriptions. English, matching the UI's default
# language — unlike the rest of the dashboard this text isn't run
# through app/static/js/i18n.js, so non-English viewers see it in
# English regardless of their selected language.
CHANGELOG = [
    {
        "version": "1.6.2",
        "date": "2026-09-30",
        "changes": [
            "Deleting a speaker also empties its device's multicast list (Fanvil), so it stops playing announcements to its old zones and to all speakers; if the device can't be reached, a message says it has to be reconfigured by hand",
            "A session now expires after 12 hours without use instead of 12 hours after sign-in; the dashboard's own automatic refreshes don't count as use",
        ],
    },
    {
        "version": "1.6.1",
        "date": "2026-09-30",
        "changes": [
            "The default admin account can no longer be changed to Operator, so there is always an administrator who can sign in and reach Users and System",
            "After too many failed sign-ins, \"try again in N seconds\" now shows the real remaining wait instead of up to 5 minutes more than needed",
        ],
    },
    {
        "version": "1.6.0",
        "date": "2026-09-28",
        "changes": [
            "A speaker can belong to several zones (up to 18 on a Fanvil). Zone members are now chosen from the Zones tab, with a searchable list of speakers; the speaker form shows its zones read-only",
            "Each speaker shows whether its device accepted the latest zone setup (\"Not in sync\" badge and a login alert); a device that was offline gets it again as soon as it answers",
            "Starting a live announcement on speakers that are already playing (another announcement or a schedule) now asks first: stop them and play, play alongside, or cancel",
            "A bell that falls during a live announcement on some of the same speakers now waits for it to end (up to 60 s) instead of cutting in",
            "Custom dates: lists of days (closures, exam days, events, yearly dates such as Dec 24 - Jan 6) that a schedule can skip or be limited to",
            "A schedule that would play on the same speakers at the same time as another one is refused when saved, naming the schedule in the way; overlaps created later by zone members or custom dates are reported, flagged in the list and shown as a login alert",
            "Automatic backup (System tab): the full configuration on a daily or weekly schedule, kept on the server and optionally copied to an FTPS/FTP server or an SMB share, with retention, a connection test, \"back up now\" and alerts when a backup fails",
            "The database is copied next to itself before any schema upgrade, and a backup exported by a newer version is refused instead of breaking the next start",
            "The dashboard no longer loads anything from the internet, runs under a strict Content Security Policy, and every API error is shown in the viewer's language",
            "Deleting a zone, speaker or audio file used by a schedule is refused with the list of schedules to fix first",
        ],
    },
    {
        "version": "1.5.8",
        "date": "2026-09-28",
        "changes": [
            "Security: names, file names and log messages are now always shown as text — before, a crafted value (even a username typed at the login form, which ends up in the Log tab) could run script in an admin's browser",
            "Security (native install): the app's code and the sudo-allowed network/time helper are no longer writable by the service user; the helper moved to /usr/local/sbin (re-run deploy/install_ubuntu.sh to apply). The Docker image likewise keeps its code root-owned",
            "Schedules with invalid days of the week, a zone/speaker destination without a target, or a start date after the end date are now rejected; a schedule that can't be loaded no longer stops the service from starting",
            "Configuration import: bundles containing links or paths outside the staging folder are refused; restoring no longer risks corrupting the imported database with leftover SQLite WAL files, and the pre-import safety copy now includes not-yet-checkpointed changes",
            "If SECRET_KEY is left at its example value, a random session key is generated and kept in data/session.key instead of signing session cookies with a publicly known key",
        ],
    },
    {
        "version": "1.5.7",
        "date": "2026-09-25",
        "changes": [
            "Fixed times in the playback history, log, media and backup lists being shifted by the viewer's UTC offset (e.g. 2 hours behind in Italy during summer time) — the server stores UTC, but the browser was reading it as local time",
            "Switching the interface language now also redraws tables and lists immediately, instead of leaving them in the previous language until the next page reload",
            "Fixed the \"(none)\" zone option in the speaker form always showing in Italian",
            "Dark theme: Edit/Amplify buttons and links are now readable instead of nearly invisible against the dark background",
            "Log exports (CSV/JSON) now mark timestamps explicitly as UTC (+00:00)",
            "Schedules now check their date range and holiday rule against the date in the configured TIMEZONE, not the host clock's zone",
            "Automatic theme: fixed staying dark all day at longitudes where daylight spans midnight UTC",
        ],
    },
    {
        "version": "1.5.6",
        "date": "2026-09-24",
        "changes": [
            "Fixed the playback history's source (Manual/Scheduled) and zone/all-speakers destination labels always showing in Italian regardless of the selected interface language",
        ],
    },
    {
        "version": "1.5.5",
        "date": "2026-09-24",
        "changes": [
            "Fixed the 2FA status text in the Security modal always showing in Italian regardless of the selected interface language",
        ],
    },
    {
        "version": "1.5.4",
        "date": "2026-09-24",
        "changes": [
            "Fixed the playback history table only showing the time of each entry, not the date, making older entries ambiguous",
        ],
    },
    {
        "version": "1.5.3",
        "date": "2026-09-24",
        "changes": [
            "New schedules no longer default to excluding Italian public holidays — holiday filtering is now off by default and fully opt-in per schedule, for any of the supported countries",
        ],
    },
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
