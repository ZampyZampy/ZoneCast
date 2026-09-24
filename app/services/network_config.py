"""
Live network configuration (IP/subnet/gateway/DNS) for a native
install — see deploy/zonecast-netctl.sh for the actual privileged
apply/revert mechanism. Deliberately NOT available on Docker: netplan
manages the HOST's interfaces, which a container generally can't
reach or safely mutate.

Safety model: `apply()` writes a netplan config and applies it
immediately, but a detached watchdog (spawned by the wrapper script,
independent of this app's process) automatically reverts to the
previous config after ~120s unless `confirm()` is called first. If a
wrong IP/gateway locks out the dashboard itself, no action is needed —
it self-heals. The caller (the router) is expected to tell the admin,
in the UI, to reconnect at the new address and confirm from there.
"""
import ipaddress
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import privileged

_EXCLUDED_IFACE_PREFIXES = ("lo", "docker", "veth", "br-", "virbr")


class NetworkConfigError(RuntimeError):
    pass


@dataclass
class NetworkStatus:
    interface: str
    address_cidr: str | None
    gateway: str | None
    dns_servers: list[str]
    available_interfaces: list[str]


def _run_json(cmd: list[str]) -> list | dict:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise NetworkConfigError(f"comando non disponibile: {' '.join(cmd)}") from exc
    if result.returncode != 0:
        raise NetworkConfigError(f"{' '.join(cmd)} fallito: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise NetworkConfigError(f"output inatteso da {' '.join(cmd)}") from exc


def list_interfaces() -> list[str]:
    links = _run_json(["ip", "-j", "link", "show"])
    return [
        i["ifname"] for i in links
        if i["ifname"] != "lo" and not i["ifname"].startswith(_EXCLUDED_IFACE_PREFIXES)
    ]


def _read_dns_servers(iface: str) -> list[str]:
    """`/etc/resolv.conf` is useless here on a systemd-resolved system
    (the normal case on 24.04+): it's a symlink to the stub resolver
    file, which always just says `nameserver 127.0.0.53` regardless of
    what's actually configured underneath — so a freshly-applied DNS
    server (e.g. 8.8.8.8) looked like it reverted on the next read, when
    the real config never changed. `resolvectl dns <iface>` reports the
    servers actually in effect for that link."""
    try:
        result = subprocess.run(["resolvectl", "dns", iface], capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        result = None
    if result and result.returncode == 0 and result.stdout.strip():
        _, _, rest = result.stdout.strip().splitlines()[0].partition(":")
        servers = rest.split()
        if servers:
            return servers

    # Fallback for systems without systemd-resolved (resolvectl absent).
    dns_servers: list[str] = []
    try:
        for line in Path("/etc/resolv.conf").read_text().splitlines():
            if line.startswith("nameserver"):
                parts = line.split()
                if len(parts) >= 2:
                    dns_servers.append(parts[1])
    except OSError:
        pass
    return dns_servers


def get_current(interface: str | None = None) -> NetworkStatus:
    interfaces = list_interfaces()
    if not interfaces:
        raise NetworkConfigError("Nessuna interfaccia di rete rilevata")
    iface = interface if interface in interfaces else interfaces[0]

    addr_data = _run_json(["ip", "-j", "-4", "addr", "show", "dev", iface])
    address_cidr = None
    if addr_data and addr_data[0].get("addr_info"):
        addr_infos = addr_data[0]["addr_info"]
        # Prefer the primary address if the interface happens to carry
        # more than one (e.g. a leftover from a netplan file net-apply
        # hasn't disabled yet) — otherwise `ip` lists them in whatever
        # order the kernel assigned them, not necessarily "the current
        # one" from the admin's point of view.
        info = next((a for a in addr_infos if not a.get("secondary")), addr_infos[0])
        address_cidr = f"{info['local']}/{info['prefixlen']}"

    routes = _run_json(["ip", "-j", "route", "show", "default"])
    gateway = next((r.get("gateway") for r in routes if r.get("dev") == iface), None)

    dns_servers = _read_dns_servers(iface)

    return NetworkStatus(
        interface=iface, address_cidr=address_cidr, gateway=gateway,
        dns_servers=dns_servers, available_interfaces=interfaces,
    )


def _build_netplan_yaml(interface: str, address_cidr: str, gateway: str, dns_servers: list[str]) -> str:
    dns_lines = "\n".join(f"        - {d}" for d in dns_servers)
    return (
        "network:\n"
        "  version: 2\n"
        "  ethernets:\n"
        f"    {interface}:\n"
        "      dhcp4: false\n"
        "      addresses:\n"
        f"        - {address_cidr}\n"
        "      routes:\n"
        "        - to: default\n"
        f"          via: {gateway}\n"
        "      nameservers:\n"
        "        addresses:\n"
        f"{dns_lines}\n"
    )


def apply(interface: str, address_cidr: str, gateway: str, dns_servers: list[str]) -> int:
    """Validates and applies immediately, with an auto-revert watchdog.
    Returns the watchdog timeout in seconds (see zonecast-netctl.sh)."""
    if interface not in list_interfaces():
        raise NetworkConfigError(f"Interfaccia '{interface}' non trovata")
    try:
        ipaddress.ip_interface(address_cidr)
        ipaddress.ip_address(gateway)
        for d in dns_servers:
            ipaddress.ip_address(d)
    except ValueError as exc:
        raise NetworkConfigError(f"Parametro di rete non valido: {exc}") from exc
    if not dns_servers:
        raise NetworkConfigError("Specificare almeno un server DNS")

    yaml_content = _build_netplan_yaml(interface, address_cidr, gateway, dns_servers)
    try:
        output = privileged.run_netctl(["net-apply"], stdin_text=yaml_content)
    except privileged.PrivilegedActionError as exc:
        raise NetworkConfigError(str(exc)) from exc
    # output looks like "OK watchdog=120s"
    try:
        return int(output.split("=")[1].rstrip("s"))
    except (IndexError, ValueError):
        return 120


def confirm() -> None:
    try:
        privileged.run_netctl(["net-confirm"])
    except privileged.PrivilegedActionError as exc:
        raise NetworkConfigError(str(exc)) from exc


def pending() -> int | None:
    """Seconds left before an unconfirmed change auto-reverts, or None
    if nothing is pending. Checked on every page load (not just by the
    tab that applied the change) so a fresh session reconnecting at the
    new address still sees the confirm prompt — see
    deploy/zonecast-netctl.sh's net-pending for why this needs the
    remaining time, not just a yes/no."""
    try:
        output = privileged.run_netctl(["net-pending"])
    except privileged.PrivilegedActionError:
        return None
    if not output.startswith("pending"):
        return None
    try:
        return int(output.split(":")[1])
    except (IndexError, ValueError):
        return 120
