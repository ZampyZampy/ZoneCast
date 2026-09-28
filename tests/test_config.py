import pytest

from app import config


@pytest.mark.parametrize("placeholder", ["change-me-in-production", "replace-with-a-long-random-string", ""])
def test_placeholder_secret_key_is_replaced_and_persisted(monkeypatch, tmp_path, placeholder):
    monkeypatch.setattr(config.settings, "secret_key", placeholder)
    monkeypatch.setattr(config.settings, "data_dir", tmp_path)

    first = config.session_secret()
    assert first != placeholder and len(first) == 64
    assert (tmp_path / "session.key").read_text().strip() == first
    assert config.session_secret() == first  # stable across restarts


def test_real_secret_key_is_used_as_is(monkeypatch, tmp_path):
    monkeypatch.setattr(config.settings, "secret_key", "a-real-deployment-specific-secret")
    monkeypatch.setattr(config.settings, "data_dir", tmp_path)

    assert config.session_secret() == "a-real-deployment-specific-secret"
    assert not (tmp_path / "session.key").exists()


@pytest.mark.parametrize("content", ["", "   \n", "short"])
def test_empty_or_truncated_session_key_is_regenerated(monkeypatch, tmp_path, content):
    monkeypatch.setattr(config.settings, "secret_key", "change-me-in-production")
    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    (tmp_path / "session.key").write_text(content)

    key = config.session_secret()
    assert len(key) == 64
    assert (tmp_path / "session.key").read_text().strip() == key
