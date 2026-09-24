"""
Restores a ZoneCast export bundle (see services/bundle.py and the
dashboard's Impostazioni > Esporta configurazione) onto a fresh
install — typically right after cloning the repo onto a new machine
and BEFORE starting the app for the first time.

Deliberately a standalone script rather than an API endpoint: hot-
swapping the SQLite file under a running app (open connections, maybe
mid-write) risks corrupting it. Run it once, then start the app
normally (docker compose up, or the systemd service — see deploy/).

Usage:
    python -m app.tools.import_bundle /path/to/zonecast_export_*.zcbundle
    (prompts for the export password)

    python -m app.tools.import_bundle bundle.zcbundle --password 'xxx' --yes
    (non-interactive, e.g. scripted provisioning)
"""
import argparse
import getpass
import sys

from ..config import BASE_DIR, settings
from ..services import bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Ripristina un export ZoneCast (data + media + backup) su questa installazione")
    parser.add_argument("bundle_file", help="File .zcbundle generato da Impostazioni > Esporta configurazione")
    parser.add_argument("--password", help="Password dell'export (se omessa, viene richiesta a terminale)")
    parser.add_argument("--yes", action="store_true", help="Non chiedere conferma prima di sovrascrivere i dati esistenti")
    args = parser.parse_args()

    data = open(args.bundle_file, "rb").read()
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
        extracted = bundle.extract_bundle(data=data, password=password, target_root=BASE_DIR)
    except ValueError as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 1

    print(f"Ripristinati {len(extracted)} file in {BASE_DIR}.")
    print("Ora puoi avviare l'app normalmente (docker compose up -d, oppure il servizio systemd).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
