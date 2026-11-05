"""
Utility to reset (or create) the admin user's password directly against
the database, for when web login access is lost.

Usage, from the install folder (/opt/zonecast, or /app in the container)
as the service user, so the database stays owned by it:
    python -m app.tools.reset_admin_password <username> <new_password>
"""
import sys

from ..database import SessionLocal
from ..migrate import run_migrations
from ..models import User, UserRole
from ..security import hash_password


def main():
    if len(sys.argv) != 3:
        print("Uso: python -m app.tools.reset_admin_password <username> <nuova_password>")
        sys.exit(1)

    username, new_password = sys.argv[1], sys.argv[2]
    run_migrations()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user:
            user.password_hash = hash_password(new_password)
            print(f"Password aggiornata per l'utente '{username}'.")
        else:
            user = User(
                username=username,
                password_hash=hash_password(new_password),
                full_name="Amministratore",
                role=UserRole.admin,
            )
            db.add(user)
            print(f"Creato nuovo utente admin '{username}'.")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
