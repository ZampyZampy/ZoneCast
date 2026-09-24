#!/usr/bin/env bash
# Native (non-Docker) install of ZoneCast on Ubuntu Server 24.04+/26.04
# LTS — installs the app directly at the OS level as a systemd service,
# instead of inside a container. Run as root (or with sudo) from inside
# a checkout of this repository:
#
#   sudo ./deploy/install_ubuntu.sh
#
# What this buys over the Docker deployment (see docker-compose.yml):
#   - Real multicast networking with no NAT/bridge workarounds — the
#     app just binds to the host's interfaces directly.
#   - NTP status/manual clock-set work without the AppArmor/D-Bus
#     limitations noted in docker-compose.yml, since there's no
#     container boundary at all.
#   - One less moving part (no Docker/containerd) on a machine whose
#     only job is running this app.
#
# What you lose: easy rollback to a previous image, and the isolation
# a container gives you against the app depending on system Python
# packages. Both setups run the app as an unprivileged, non-root user.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Esegui questo script come root (sudo $0)" >&2
    exit 1
fi

INSTALL_DIR="/opt/zonecast"
SERVICE_USER="zonecast"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== Pacchetti di sistema =="
apt-get update
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip ffmpeg rsync git sqlite3 curl

echo "== Utente di servizio =="
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --create-home --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

echo "== Copia applicazione in $INSTALL_DIR =="
mkdir -p "$INSTALL_DIR"
rsync -a --delete \
    --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
    --exclude 'data' --exclude 'media' --exclude 'backups' --exclude 'venv' \
    "$REPO_DIR"/app "$REPO_DIR"/requirements.txt "$REPO_DIR"/deploy "$INSTALL_DIR/"

mkdir -p "$INSTALL_DIR"/data "$INSTALL_DIR"/media "$INSTALL_DIR"/backups

if [[ ! -f "$INSTALL_DIR/.env" ]]; then
    cp "$REPO_DIR/.env.example" "$INSTALL_DIR/.env"
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$SECRET/" "$INSTALL_DIR/.env"
    echo "Creato $INSTALL_DIR/.env con una nuova SECRET_KEY generata — rivedilo (password admin, DATABASE_URL, ecc.) prima del primo avvio."
fi

echo "== Ambiente virtuale Python =="
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"

echo "== Helper privilegiato (NTP/fuso orario/rete dal pannello Sistema) =="
chmod 750 "$INSTALL_DIR/deploy/zonecast-netctl.sh"
chown root:root "$INSTALL_DIR/deploy/zonecast-netctl.sh"
cat > /etc/sudoers.d/zonecast-netctl <<EOF
# Consente SOLO a $SERVICE_USER di eseguire, senza password, questo
# specifico script (mai comandi arbitrari) — vedi deploy/zonecast-netctl.sh.
$SERVICE_USER ALL=(root) NOPASSWD: $INSTALL_DIR/deploy/zonecast-netctl.sh
EOF
chmod 440 /etc/sudoers.d/zonecast-netctl
visudo -c -f /etc/sudoers.d/zonecast-netctl

echo "== Servizio systemd =="
cp "$INSTALL_DIR/deploy/zonecast.service" /etc/systemd/system/zonecast.service
systemctl daemon-reload
systemctl enable zonecast
systemctl restart zonecast

echo
echo "Fatto. Stato del servizio:"
systemctl --no-pager status zonecast || true
echo
echo "Log:      journalctl -u zonecast -f"
echo "Config:   $INSTALL_DIR/.env"
echo "Dati:     $INSTALL_DIR/{data,media,backups}"
echo
echo "Per ripristinare un export completo da un'altra installazione (vedi Impostazioni > Esporta configurazione),"
echo "fallo PRIMA di 'systemctl start zonecast', con il servizio fermo:"
echo "  sudo systemctl stop zonecast"
echo "  sudo -u $SERVICE_USER $INSTALL_DIR/venv/bin/python -m app.tools.import_bundle /percorso/export.zcbundle"
echo "  (eseguito da $INSTALL_DIR)"
echo "  sudo systemctl start zonecast"
