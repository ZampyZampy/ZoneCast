"""
Restores a ZoneCast export bundle (see services/bundle.py and the
dashboard's Impostazioni > Esporta configurazione) onto a fresh
install — typically right after cloning the repo onto a new machine
and BEFORE starting the app for the first time.

Deliberately a standalone script rather than an API endpoint: hot-
swapping the SQLite file under a running app (open connections, maybe
mid-write) risks corrupting it. Run it once, then start the app
normally (docker compose up, or the systemd service — see deploy/).

Goes through the same staging as the dashboard's import (a bundle from a
newer ZoneCast is refused, the current data is copied aside first), then
applies it at once, since the service is stopped.

Usage:
    python -m app.tools.import_bundle /path/to/zonecast_export_*.zcbundle
    (prompts for the export password)

    python -m app.tools.import_bundle bundle.zcbundle --password 'xxx' --yes
    (non-interactive, e.g. scripted provisioning)
"""
import argparse
import getpass
import sys

from ..config import settings
from ..services import pending_import


def main() -> int:
    parser = argparse.ArgumentParser(description="Ripristina un export ZoneCast (data + media + backup) su questa installazione")
    parser.add_argument("bundle_file", help="File .zcbundle generato da Impostazioni > Esporta configurazione")
    parser.add_argument("--password", help="Password dell'export (se omessa, viene richiesta a terminale)")
    parser.add_argument("--yes", action="store_true", help="Non chiedere conferma prima di sovrascrivere i dati esistenti")
    args = parser.parse_args()

    with open(args.bundle_file, "rb") as f:
        data = f.read()
    password = args.password or getpass.getpass("Password dell'export: ")

    existing_db = settings.db_path.exists()
    if existing_db and not args.yes:
        answer = input(
            f"ATTENZIONE: {settings.db_path} esiste già e verrà sovrascritto, insieme a media/ e backups/ "
            f"esistenti con lo stesso nome file. Continuare? [s/N] "
        )
        if answer.strip().lower() not in ("s", "si", "sì", "y", "yes"):
            print("Annullato.")
            return 1

    try:
        pending_import.stage(data, password)
        pending_import.apply_pending_import()
    except (ValueError, OSError) as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 1

    print(f"Ripristino completato in {settings.db_path.parent.parent} (i dati precedenti sono stati copiati a parte, *.pre-import-*).")
    print("Ora puoi avviare l'app normalmente (docker compose up -d, oppure il servizio systemd).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
