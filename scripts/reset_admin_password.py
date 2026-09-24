"""
Utility to reset (or create) the admin user's password directly against
the database, for when web login access is lost.

Usage (from the project root, with the venv active or inside the
container):
    python scripts/reset_admin_password.py <username> <new_password>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, Base, engine  # noqa: E402
from app.models import User, UserRole  # noqa: E402
from app.security import hash_password  # noqa: E402


def main():
    if len(sys.argv) != 3:
        print("Uso: python scripts/reset_admin_password.py <username> <nuova_password>")
        sys.exit(1)

    username, new_password = sys.argv[1], sys.argv[2]
    Base.metadata.create_all(bind=engine)
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
