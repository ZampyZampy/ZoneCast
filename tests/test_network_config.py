import pytest

from app.services import network_config, privileged


def test_apply_refused_while_a_change_awaits_confirmation(monkeypatch):
    calls = []

    def fake_netctl(args, stdin_text=None, timeout=15.0):
        calls.append(args[0])
        return "pending:80" if args[0] == "net-pending" else "OK watchdog=120s"

    monkeypatch.setattr(network_config, "list_interfaces", lambda: ["eth0"])
    monkeypatch.setattr(privileged, "run_netctl", fake_netctl)
    with pytest.raises(network_config.NetworkConfigError):
        network_config.apply("eth0", "192.168.1.10/24", "192.168.1.1", ["1.1.1.1"])
    assert "net-apply" not in calls

    monkeypatch.setattr(privileged, "run_netctl", lambda args, **kw: "none" if args[0] == "net-pending" else "OK watchdog=120s")
    assert network_config.apply("eth0", "192.168.1.10/24", "192.168.1.1", ["1.1.1.1"]) == 120


def test_address_without_prefix_is_refused_before_netctl(monkeypatch):
    calls = []
    monkeypatch.setattr(network_config, "list_interfaces", lambda: ["eth0"])
    monkeypatch.setattr(privileged, "run_netctl", lambda args, **kw: calls.append(args[0]) or "none")
    with pytest.raises(network_config.NetworkConfigError):
        network_config.apply("eth0", "192.168.1.10", "192.168.1.1", ["1.1.1.1"])
    assert calls == []
