"""Where each device sits on the house network, for the signed-in Devices page.

Read-only and best effort: every figure is one the Pi already holds. Nothing is
scanned and nothing is sent to a device. Vendor device IDs never leave this module.
"""
import ipaddress
import struct

from ewelink import private_json

JUWAI_CONFIG = "/var/lib/modpi/private/juwai-local.json"
EPEVER_CONFIG = "/var/lib/modpi/private/epever.json"
ZIGBEE_PREFIX = "a4"  # eWeLink gives Zigbee sub-devices IDs starting a4; they have no address of their own


def _lan(value):
    """A private IPv4 address as text, or None."""
    try:
        ip = ipaddress.ip_address(value)
    except (ValueError, TypeError):
        return None
    return str(ip) if ip.version == 4 and ip.is_private and not ip.is_loopback else None


def connected_peer(port, table="/proc/net/tcp"):
    """The far end of an established connection to our listening port (the charger's dongle)."""
    try:
        with open(table) as stream:
            lines = stream.read().splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        fields = line.split()
        try:
            local, remote, state = fields[1], fields[2], fields[3]
            if state != "01" or int(local.rsplit(":", 1)[1], 16) != port:
                continue
            address, _ = remote.rsplit(":", 1)
            peer = _lan(struct.unpack("<I", bytes.fromhex(address))[0])
        except (IndexError, ValueError, struct.error):
            continue
        if peer:
            return peer
    return None


def solar_hosts(path=JUWAI_CONFIG):
    try:
        devices = private_json(path).get("devices")
    except (OSError, RuntimeError, ValueError):
        return {}
    if not isinstance(devices, dict):
        return {}
    return {alias: _lan(entry.get("host")) for alias, entry in devices.items() if isinstance(entry, dict)}


def epever_host(path=EPEVER_CONFIG, table="/proc/net/tcp"):
    try:
        port = private_json(path).get("port")
    except (OSError, RuntimeError, ValueError):
        return None
    return connected_peer(port, table) if type(port) is int else None


def addresses(ewelink=None, seen=None, juwai_config=JUWAI_CONFIG, epever_config=EPEVER_CONFIG, table="/proc/net/tcp"):
    """{"solar": {alias: ip}, "epever": ip, "plugs": {slug: ip}, "home": {slug: {"ip", "zigbee"}}, "bridge": ip}

    seen is EwelinkLocal.addresses(): {device id: (ip, announced type)} heard on the LAN lately."""
    seen = seen or {}
    out = {"solar": solar_hosts(juwai_config), "epever": epever_host(epever_config, table),
           "plugs": {}, "home": {}, "bridge": None}
    for ip, kind in seen.values():
        if kind == "zigbee_gateway":
            out["bridge"] = _lan(ip)
    if ewelink is None:
        return out
    try:
        mapping = ewelink.config().get("devices", {})
    except (OSError, RuntimeError, ValueError):
        mapping = {}
    for slug, value in mapping.items():
        device = value.get("id") if isinstance(value, dict) else value
        out["plugs"][slug] = _lan(seen.get(device, (None, None))[0])
    try:
        home = ewelink.home_mapping() or {}
    except (OSError, RuntimeError, ValueError):
        home = {}
    for slug, entry in home.items():
        out["home"][slug] = {"ip": _lan(seen.get(entry["id"], (None, None))[0]),
                             "zigbee": entry["id"].startswith(ZIGBEE_PREFIX)}
    return out
