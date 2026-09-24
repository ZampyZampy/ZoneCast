#!/usr/bin/env bash
# Narrow, auditable root helper for ZoneCast's NTP/timezone/network
# settings (Sistema tab, native install only). Invoked by the app via
# `sudo -n /opt/zonecast/deploy/zonecast-netctl.sh <subcommand> ...`,
# authorized by a NOPASSWD sudoers rule scoped to exactly this script
# path (see install_ubuntu.sh) — the app process itself stays non-root.
#
# Network changes never take effect unconfirmed: `net-apply` writes the
# new config, applies it immediately, and forks a detached watchdog
# that reverts to the previous config after TIMEOUT_SECONDS unless
# `net-confirm` runs first. This is deliberately filesystem-marker
# based (not tied to netplan's own `try` command or to this script's
# process lifetime) so it survives the app restarting, and doesn't
# depend on netplan's confirmation prompt working without a real TTY.
set -euo pipefail

NETPLAN_FILE="/etc/netplan/90-zonecast.yaml"
BACKUP_FILE="/etc/netplan/90-zonecast.yaml.backup"
CONFIRM_MARKER="/run/zonecast-net-confirm"
APPLY_TIME_MARKER="/run/zonecast-net-apply-time"
TIMEOUT_SECONDS=120
WATCHDOG_LOG="/var/log/zonecast-netctl-watchdog.log"
TIMESYNCD_CONF="/etc/systemd/timesyncd.conf"
CHRONY_ZONECAST_CONF="/etc/chrony/conf.d/99-zonecast.conf"
CHRONY_DEFAULT_SOURCES="/etc/chrony/sources.d/ubuntu-ntp-pools.sources"
CHRONY_DEFAULT_SOURCES_DISABLED="/etc/chrony/sources.d/ubuntu-ntp-pools.sources.zonecast-disabled"

case "${1:-}" in
  ntp-on)
    exec timedatectl set-ntp true
    ;;
  ntp-off)
    exec timedatectl set-ntp false
    ;;
  timezone)
    [[ -n "${2:-}" ]] || { echo "manca il fuso orario" >&2; exit 1; }
    exec timedatectl set-timezone "$2"
    ;;
  ntp-servers)
    # New space-separated NTP server list arrives on stdin (already
    # validated by the app). ZoneCast is the sole owner of ITS OWN
    # config (this file / the timesyncd.conf [Time] section), so that
    # part is fully regenerated rather than patched in place.
    #
    # Ubuntu 26.04+ ships chrony instead of systemd-timesyncd (see
    # services/system_time.py) — chrony has no single "NTP=" slot,
    # it merges every configured source. So that a custom server here
    # actually REPLACES the distro defaults (rather than just adding
    # to them), the default Ubuntu pool sources file is disabled while
    # custom servers are set, and restored when cleared — same
    # disable/restore shape as the netplan backup below.
    servers="$(cat | tr -s ' \t\n' ' ' | sed -e 's/^ *//' -e 's/ *$//')"
    # Which backend is INSTALLED, not whether it's currently running —
    # `systemctl is-active` used to gate this, but NTP sync active/off
    # (Sistema > Orario's own toggle, or the auto-disable that manual
    # clock-set does) legitimately stops chrony without uninstalling it,
    # and a disabled-but-installed chrony was being misread as "use
    # timesyncd", writing to timesyncd.conf (not even present on a
    # chrony-only box) and failing to restart a unit that doesn't exist.
    if systemctl list-unit-files chrony.service --no-legend 2>/dev/null | grep -q chrony; then
      if [[ -n "$servers" ]]; then
        [[ -f "$CHRONY_DEFAULT_SOURCES" ]] && mv "$CHRONY_DEFAULT_SOURCES" "$CHRONY_DEFAULT_SOURCES_DISABLED"
        {
          echo "# Managed by ZoneCast (Sistema > Orario) — do not edit by hand."
          for s in $servers; do echo "server $s iburst"; done
        } > "$CHRONY_ZONECAST_CONF"
      else
        rm -f "$CHRONY_ZONECAST_CONF"
        [[ -f "$CHRONY_DEFAULT_SOURCES_DISABLED" ]] && mv "$CHRONY_DEFAULT_SOURCES_DISABLED" "$CHRONY_DEFAULT_SOURCES"
      fi
      # NOT `chronyc reload sources` — that only re-scans sourcedir
      # directories (sources.d), it does not re-read confdir-included
      # files (conf.d, where our custom `server` line lives; those are
      # only parsed at chronyd startup). Using reload here silently
      # dropped the default pool (re-scanned, now empty) *and* never
      # picked up the custom server (not re-scanned at all) — net
      # result zero active sources. A restart re-reads everything.
      systemctl restart chrony
    else
      {
        echo "[Time]"
        [[ -n "$servers" ]] && echo "NTP=$servers"
      } > "$TIMESYNCD_CONF"
      systemctl restart systemd-timesyncd
    fi
    echo "OK"
    ;;
  net-apply)
    # New netplan YAML arrives on stdin (built and validated by the
    # app — see services/network_config.py).
    #
    # netplan MERGES every file in /etc/netplan/ — for the same device
    # key, a later file's `addresses:` is UNIONED with an earlier file's,
    # not replacing it. So the installer's own config (e.g.
    # 00-installer-config.yaml, still declaring the original static IP)
    # stays active side-by-side with ours forever, unless disabled: the
    # old address keeps answering right alongside the new one, and `ip
    # addr show` returns both, making the dashboard's own reading of
    # "the current address" ambiguous. Once ZoneCast applies a config
    # for the first time, it becomes the sole authority for this box's
    # addressing — every OTHER netplan file is disabled (renamed, not
    # deleted, so nothing is lost) rather than merged with.
    # Only on the true first apply (nothing to fall back to yet) — once
    # $NETPLAN_FILE exists, ZoneCast is already the sole authority and
    # any sibling was disabled on a previous run. Tracked in a marker
    # (not just re-globbed) so the watchdog below can restore exactly
    # these files, and only these, if THIS first apply gets reverted —
    # otherwise a bad first IP would revert to no address at all rather
    # than back to the pre-ZoneCast config, defeating the whole point of
    # the watchdog.
    FIRST_APPLY=0
    DISABLED_SIBLINGS_MARKER="/run/zonecast-net-disabled-siblings"
    if [[ ! -f "$NETPLAN_FILE" ]]; then
        FIRST_APPLY=1
        : > "$DISABLED_SIBLINGS_MARKER"
        for f in /etc/netplan/*.yaml; do
            [[ -e "$f" ]] || continue
            mv "$f" "${f}.zonecast-disabled"
            echo "${f}.zonecast-disabled:$f" >> "$DISABLED_SIBLINGS_MARKER"
        done
    fi

    rm -f "$CONFIRM_MARKER"
    if [[ -f "$NETPLAN_FILE" ]]; then
        cp "$NETPLAN_FILE" "$BACKUP_FILE"
    else
        rm -f "$BACKUP_FILE"   # no previous managed config to fall back to
    fi
    cat > "$NETPLAN_FILE"
    chmod 600 "$NETPLAN_FILE"
    date +%s > "$APPLY_TIME_MARKER"   # lets net-pending report accurate remaining time to ANY session, not just the one that clicked Apply
    netplan apply

    setsid nohup bash -c '
      sleep '"$TIMEOUT_SECONDS"'
      if [[ ! -f "'"$CONFIRM_MARKER"'" ]]; then
        if [[ -f "'"$BACKUP_FILE"'" ]]; then
          cp "'"$BACKUP_FILE"'" "'"$NETPLAN_FILE"'"
        elif [[ '"$FIRST_APPLY"' -eq 1 && -f "'"$DISABLED_SIBLINGS_MARKER"'" ]]; then
          rm -f "'"$NETPLAN_FILE"'"
          while IFS=: read -r disabled original; do
            [[ -e "$disabled" ]] && mv "$disabled" "$original"
          done < "'"$DISABLED_SIBLINGS_MARKER"'"
          rm -f "'"$DISABLED_SIBLINGS_MARKER"'"
        else
          rm -f "'"$NETPLAN_FILE"'"
        fi
        netplan apply
        touch "'"$CONFIRM_MARKER"'"   # settled (reverted) — without this, net-pending reports "pending" forever, since NETPLAN_FILE can still exist (restored from backup) with no confirm marker
        rm -f "'"$APPLY_TIME_MARKER"'"
        echo "$(date -Iseconds) reverted (not confirmed in time)" >> "'"$WATCHDOG_LOG"'"
      fi
    ' >>"$WATCHDOG_LOG" 2>&1 < /dev/null &
    disown
    echo "OK watchdog=${TIMEOUT_SECONDS}s"
    ;;
  net-confirm)
    touch "$CONFIRM_MARKER"
    rm -f "/run/zonecast-net-disabled-siblings" "$APPLY_TIME_MARKER"   # confirmed — no revert coming, nothing to restore
    echo "OK"
    ;;
  net-pending)
    # Reports whether a trial config is currently awaiting confirmation,
    # and if so, how many seconds are left — so a session that reconnects
    # at the NEW address in a fresh page load (not the tab that clicked
    # Apply, which is the common case: that tab is stranded at the OLD
    # address) can still show an accurate countdown and Confirm button,
    # instead of the trial silently expiring with nothing on screen to
    # confirm it from.
    if [[ -f "$NETPLAN_FILE" && ! -f "$CONFIRM_MARKER" ]]; then
        applied_at=$(cat "$APPLY_TIME_MARKER" 2>/dev/null || echo 0)
        remaining=$(( TIMEOUT_SECONDS - ($(date +%s) - applied_at) ))
        [[ $remaining -lt 0 ]] && remaining=0
        echo "pending:${remaining}"
    else
        echo "none"
    fi
    ;;
  *)
    echo "uso: $0 {ntp-on|ntp-off|timezone TZ|ntp-servers|net-apply|net-confirm|net-pending}" >&2
    exit 2
    ;;
esac
