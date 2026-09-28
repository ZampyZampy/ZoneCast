"""The one way to get "now" for storage: naive UTC, matching every
DateTime column in models.py (datetime.utcnow() is deprecated since
Python 3.12 but produced exactly this). The API returns these values
as-is and the dashboard reads them as UTC (lib/format.js)."""
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
