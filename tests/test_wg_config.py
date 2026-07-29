import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from securevpn.peers import PeerRegistry  # noqa: E402
from securevpn.wg_config import (  # noqa: E402
    choose_dns_servers,
    client_allowed_ips,
    render_client_config,
    render_server_config,
)

NET4 = "10.66.66.0/24"
NET6 = "fd42:4242:4242::/64"


@pytest.fixture
def peer(tmp_path):
    registry = PeerRegistry(str(tmp_path / "peers.json"), NET4, NET6)
    return registry.add_peer("pc")


def test_server_config_contains_interface_and_peer(peer, tmp_path):
    conf = render_server_config("PRIVKEY", 51820, NET4, NET6, [peer])
    assert "[Interface]" in conf
    assert "Address = 10.66.66.1/24, fd42:4242:4242::1/64" in conf
    assert "ListenPort = 51820" in conf
    assert "# peer: pc" in conf
    assert f"PublicKey = {peer['public_key']}" in conf
    assert f"PresharedKey = {peer['preshared_key']}" in conf
    # Control de acceso: el peer solo puede usar SU IP exacta.
    assert "AllowedIPs = 10.66.66.2/32, fd42:4242:4242::2/128" in conf


def test_full_tunnel_covers_ipv4_and_ipv6():
    """Si faltara ::/0, el tráfico IPv6 saldría fuera del túnel sin cifrar
    (IPv6 leak). Este test existe para que nadie lo rompa sin enterarse."""
    allowed = client_allowed_ips("full", [])
    assert "0.0.0.0/0" in allowed
    assert "::/0" in allowed


def test_split_mode_uses_configured_networks():
    allowed = client_allowed_ips("split", ["10.66.66.0/24", "192.168.50.0/24"])
    assert allowed == "10.66.66.0/24, 192.168.50.0/24"
    assert "0.0.0.0/0" not in allowed


def test_split_mode_without_networks_is_an_error():
    with pytest.raises(ValueError):
        client_allowed_ips("split", [])


def test_invalid_mode_is_an_error():
    with pytest.raises(ValueError):
        client_allowed_ips("fulll", [])


def test_client_config_full_tunnel(peer):
    conf = render_client_config(
        peer,
        server_public_key="SERVERPUB",
        endpoint_host="203.0.113.7",
        endpoint_port=51820,
        mode="full",
        split_allowed_ips=[],
        dns_servers=["127.0.0.1"],
        keepalive_seconds=25,
    )
    assert f"PrivateKey = {peer['private_key']}" in conf
    assert "Address = 10.66.66.2/32, fd42:4242:4242::2/128" in conf
    assert "DNS = 127.0.0.1" in conf
    assert "PublicKey = SERVERPUB" in conf
    assert "Endpoint = 203.0.113.7:51820" in conf
    assert "AllowedIPs = 0.0.0.0/0, ::/0" in conf
    assert "PersistentKeepalive = 25" in conf


def test_ipv4_only_configs_when_v6_disabled(tmp_path):
    """Con network_v6 vacío (server o red sin IPv6), las configs salen
    limpias solo-IPv4, sin pedazos de v6 colgando."""
    registry = PeerRegistry(str(tmp_path / "peers.json"), NET4, "")
    peer = registry.add_peer("pc")
    assert peer["ip_v6"] == ""

    server_conf = render_server_config("PRIV", 51820, NET4, "", [peer])
    assert "Address = 10.66.66.1/24\n" in server_conf
    assert "AllowedIPs = 10.66.66.2/32\n" in server_conf
    assert "fd42" not in server_conf

    client_conf = render_client_config(
        peer, "SRVPUB", "127.0.0.1", 51820, "split",
        ["10.66.66.0/24"], [], 25,
    )
    assert "Address = 10.66.66.2/32\n" in client_conf
    assert "fd42" not in client_conf


def test_choose_dns_prefers_securedns_when_alive():
    assert choose_dns_servers(True, "127.0.0.1", ["9.9.9.9"], securedns_alive=True) == [
        "127.0.0.1"
    ]


def test_choose_dns_falls_back_when_securedns_down():
    """La regla de la integración: conectar la VPN jamás debe dejarte sin
    resolución de nombres porque SecureDNS estaba apagado."""
    assert choose_dns_servers(True, "127.0.0.1", ["9.9.9.9", "1.1.1.1"], False) == [
        "9.9.9.9",
        "1.1.1.1",
    ]


def test_choose_dns_ignores_securedns_when_disabled():
    assert choose_dns_servers(False, "127.0.0.1", ["9.9.9.9"], securedns_alive=True) == [
        "9.9.9.9"
    ]
