"""Every translation key the dashboard uses exists, in every language, with
the same {placeholders} as English."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANGS = ["en", "it", "fr", "de", "es", "ja", "zh"]

# Keys built at runtime (t(`playstatus.${r.status}`) and similar): the
# values each template can take.
DYNAMIC = {
    "playstatus": ["running", "completed", "failed", "stopped"],
    "status": ["online", "offline", "unknown"],
    "users": ["admin", "operator"],
    "schedules": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
}


def _object(body: str) -> dict:
    return json.loads("{" + re.sub(r",\s*$", "", body.strip()) + "}")


def _translations() -> dict[str, dict]:
    text = (ROOT / "app/static/js/i18n.js").read_text(encoding="utf-8")
    table = {lang: {} for lang in LANGS}
    for lang in ("en", "it"):
        table[lang].update(_object(re.search(rf"^{lang}: \{{\n(.*?)^\}},$", text, re.S | re.M).group(1)))
    for lang, body in re.findall(r"^TRANSLATIONS\.(\w+) = Object\.assign\(\{\}, TRANSLATIONS\.en, \{(.*?)\}\);$", text, re.S | re.M):
        table[lang].update(_object(body))
    for lang, body in re.findall(r"^Object\.assign\(TRANSLATIONS\.(\w+), \{(.*?)\}\);$", text, re.S | re.M):
        table[lang].update(_object(body))
    return table


def _keys_used() -> set[str]:
    keys = set()
    for path in (ROOT / "app/templates").glob("*.html"):
        keys.update(re.findall(r'data-i18n="([^"]+)"', path.read_text(encoding="utf-8")))
    for path in (ROOT / "app/static/js").rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        keys.update(re.findall(r"""\bt\(\s*['"]([\w.]+)['"]""", text))
        for prefix in re.findall(r"\bt\(\s*`([\w]+)\.\$\{", text):
            assert prefix in DYNAMIC, f"{path.name}: add the values of t(`{prefix}.${{…}}`) to DYNAMIC"
            keys.update(f"{prefix}.{value}" for value in DYNAMIC[prefix])
    return keys


def test_every_used_key_is_translated_in_every_language():
    used = _keys_used()
    assert len(used) > 250, len(used)  # sanity: the regexes still match the code base
    table = _translations()
    for lang in LANGS:
        missing = sorted(k for k in used if k not in table[lang])
        assert not missing, f"{lang}: {missing}"


def test_translations_keep_the_english_placeholders():
    table = _translations()
    for key, english in table["en"].items():
        expected = set(re.findall(r"\{(\w+)\}", english))
        for lang in LANGS[1:]:
            if key in table[lang]:
                assert set(re.findall(r"\{(\w+)\}", table[lang][key])) == expected, (lang, key)


def test_no_language_has_keys_english_lacks():
    table = _translations()
    for lang in LANGS[1:]:
        extra = sorted(set(table[lang]) - set(table["en"]))
        assert not extra, f"{lang}: {extra}"
