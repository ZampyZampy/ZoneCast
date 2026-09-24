"""
Optional per-user TOTP two-factor auth. Self-service: any logged-in
user can enable/disable it for their own account (see
routers/auth.py's /me/2fa/* endpoints) — it's never mandatory.

Recovery codes are the fallback if the authenticator device is lost:
10 single-use codes, shown to the user exactly once at generation
time, stored only as bcrypt hashes (same as the login password) so a
DB leak doesn't hand out working codes.
"""
import io
import json
import secrets

import pyotp
import qrcode
import qrcode.image.svg

from .. import security

ISSUER = "ZoneCast"
RECOVERY_CODE_COUNT = 10


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)


def qr_code_svg(uri: str) -> str:
    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(uri, image_factory=factory, box_size=8)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def verify_code(secret: str, code: str) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code:
        return False
    # valid_window=1 tolerates ~30s of clock drift either side.
    return pyotp.totp.TOTP(secret).verify(code, valid_window=1)


def generate_recovery_codes() -> tuple[list[str], str]:
    """Returns (plaintext codes to show the user once, JSON blob of
    bcrypt hashes to store)."""
    codes = [f"{secrets.token_hex(4)}-{secrets.token_hex(4)}" for _ in range(RECOVERY_CODE_COUNT)]
    hashed = [security.hash_password(c) for c in codes]
    return codes, json.dumps(hashed)


def consume_recovery_code(stored_json: str | None, code: str) -> str | None:
    """If `code` matches one of the stored hashed recovery codes,
    returns the updated JSON blob with that code removed (to persist).
    Returns None if no match."""
    if not stored_json or not code:
        return None
    hashes: list[str] = json.loads(stored_json)
    code = code.strip()
    for h in hashes:
        if security.verify_password(code, h):
            hashes.remove(h)
            return json.dumps(hashes)
    return None


def remaining_recovery_codes(stored_json: str | None) -> int:
    if not stored_json:
        return 0
    return len(json.loads(stored_json))
