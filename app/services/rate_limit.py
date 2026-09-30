"""
Minimal in-memory brute-force guard for the login endpoint. Deliberately
simple (no Redis/DB) since ZoneCast runs as a single process — state
lives for the process's lifetime, which is exactly the window that
matters for slowing down a live password-guessing attempt.
"""
import math
import time

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 300  # 5 minutes

# key -> list of failed-attempt unix timestamps
_failed_attempts: dict[str, list[float]] = {}


def _key(username: str, client_ip: str) -> str:
    # Keyed by username+IP together: slows down both "guess many
    # passwords for one account" and "spray one password across many
    # accounts from one source" without letting an attacker lock out a
    # legitimate user by deliberately failing login from elsewhere.
    return f"{username.lower()}:{client_ip}"


def is_locked_out(username: str, client_ip: str) -> int:
    """Returns remaining lockout seconds (0 if not locked out).

    The lock lifts as soon as fewer than MAX_ATTEMPTS failures are left
    in the sliding window, i.e. when the oldest of the last MAX_ATTEMPTS
    leaves it — not a fixed time after the newest one, which made the
    "try again in N seconds" message overstate the wait by up to the
    whole window. Rounded up, so a locked key never reports 0."""
    now = time.time()
    attempts = [t for t in _failed_attempts.get(_key(username, client_ip), []) if now - t < WINDOW_SECONDS]
    if len(attempts) < MAX_ATTEMPTS:
        return 0
    return max(1, math.ceil(attempts[-MAX_ATTEMPTS] + WINDOW_SECONDS - now))


def record_failure(username: str, client_ip: str) -> None:
    now = time.time()
    key = _key(username, client_ip)
    attempts = [t for t in _failed_attempts.get(key, []) if now - t < WINDOW_SECONDS]
    attempts.append(now)
    _failed_attempts[key] = attempts


def record_success(username: str, client_ip: str) -> None:
    _failed_attempts.pop(_key(username, client_ip), None)
