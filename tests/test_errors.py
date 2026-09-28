"""The API's single error shape, and that every code it can return is
translated in every dashboard language."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANGS = ["en", "it", "fr", "de", "es", "ja", "zh"]


def _detail(res):
    detail = res.json()["detail"]
    assert set(detail) >= {"code", "message", "params"}, detail
    return detail


def test_domain_error_shape(admin_client):
    res = admin_client.delete("/api/zones/999999")
    assert res.status_code == 404
    assert _detail(res)["code"] == "zones.not_found"


def test_not_authenticated_shape(client):
    res = client.get("/api/zones")
    assert res.status_code == 401
    assert _detail(res)["code"] == "auth.not_authenticated"


def test_custom_validation_code_and_params(admin_client, media_id):
    res = admin_client.post("/api/schedules", json={
        "name": "x", "media_id": media_id, "target_type": "all", "time_of_day": "08:00:00", "days_of_week": "lun,mar",
    })
    assert res.status_code == 422
    detail = _detail(res)
    assert detail["code"] == "validation.days_invalid"
    assert detail["params"]["days"] == "lun, mar"


def test_generic_validation_names_the_fields(admin_client):
    res = admin_client.post("/api/zones", json={"name": "no address"})
    assert res.status_code == 422
    detail = _detail(res)
    assert detail["code"] == "validation.generic" and "multicast_address" in detail["params"]["fields"]


def test_framework_errors_use_the_same_shape(client):
    res = client.get("/api/does-not-exist")
    assert res.status_code == 404
    assert _detail(res)["code"] == "http.404"


def _codes_used_by_the_backend() -> set[str]:
    patterns = [
        r'AppError\(\s*[\w.]+,\s*"([a-z0-9_]+\.[a-z0-9_]+)"',   # AppError(404, "zones.not_found", ...)
        r'invalid\(\s*"([a-z0-9_]+\.[a-z0-9_]+)"',               # invalid("validation.days_empty", ...)
        r'(?:Error|Conflict)\(\s*"([a-z0-9_]+\.[a-z0-9_]+)"',    # CodedError subclasses
        r'refuse_if_used\([^)]*\),\s*"([a-z0-9_]+\.[a-z0-9_]+)"',
    ]
    codes = set()
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            codes.update(re.findall(pattern, text))
    return codes


def _error_translations() -> dict[str, dict]:
    text = (ROOT / "app/static/js/i18n.js").read_text(encoding="utf-8")
    blocks = dict(re.findall(r"Object\.assign\(TRANSLATIONS\.(\w+), (\{.*?\})\);", text))
    return {lang: json.loads(blocks[lang]) for lang in LANGS}


def test_every_error_code_is_translated_in_every_language():
    codes = _codes_used_by_the_backend()
    # the group-busy reason is only stored in history rows, never shown as an API error
    codes.discard("playback.group_busy")
    assert len(codes) > 40, codes  # sanity: the regexes still match the code base
    translations = _error_translations()
    for lang in LANGS:
        missing = sorted(c for c in codes if f"error.{c}" not in translations[lang])
        assert not missing, f"{lang}: {missing}"


def test_error_translation_placeholders_match_english():
    translations = _error_translations()
    for key, english in translations["en"].items():
        expected = set(re.findall(r"\{(\w+)\}", english))
        for lang in LANGS[1:]:
            assert set(re.findall(r"\{(\w+)\}", translations[lang][key])) == expected, (lang, key)
