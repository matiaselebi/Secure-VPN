import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from securevpn.peers import PeerRegistry, server_ip_v4, server_ip_v6  # noqa: E402

NET4 = "10.66.66.0/24"
NET6 = "fd42:4242:4242::/64"


def make_registry(tmp_path):
    return PeerRegistry(str(tmp_path / "peers.json"), NET4, NET6)


def test_server_takes_first_ip():
    assert server_ip_v4(NET4) == "10.66.66.1"
    assert server_ip_v6(NET6) == "fd42:4242:4242::1"


def test_peers_get_sequential_ips(tmp_path):
    registry = make_registry(tmp_path)
    pc = registry.add_peer("pc")
    celular = registry.add_peer("celular")
    assert pc["ip_v4"] == "10.66.66.2"
    assert celular["ip_v4"] == "10.66.66.3"
    assert pc["ip_v6"] == "fd42:4242:4242::2"
    assert celular["ip_v6"] == "fd42:4242:4242::3"


def test_registry_persists_to_disk(tmp_path):
    make_registry(tmp_path).add_peer("pc")
    reloaded = make_registry(tmp_path)
    assert reloaded.get("pc") is not None
    assert reloaded.get("pc")["ip_v4"] == "10.66.66.2"


def test_duplicate_names_rejected(tmp_path):
    registry = make_registry(tmp_path)
    registry.add_peer("pc")
    with pytest.raises(ValueError):
        registry.add_peer("pc")


def test_removed_peer_frees_its_ip(tmp_path):
    registry = make_registry(tmp_path)
    registry.add_peer("pc")
    registry.add_peer("celular")
    assert registry.remove_peer("pc")
    nuevo = registry.add_peer("tablet")
    # Reusa el hueco del peer borrado (la .2), no sigue creciendo.
    assert nuevo["ip_v4"] == "10.66.66.2"


def test_each_peer_has_unique_keys(tmp_path):
    registry = make_registry(tmp_path)
    pc = registry.add_peer("pc")
    celular = registry.add_peer("celular")
    assert pc["private_key"] != celular["private_key"]
    assert pc["public_key"] != celular["public_key"]
    assert pc["preshared_key"] != celular["preshared_key"]


def test_los_peers_viejos_pierden_su_ipv6_si_el_tunel_paso_a_ipv4(tmp_path):
    """Un peer creado cuando el tunel era doble stack guarda su IP v6 en
    peers.json. Si despues el tunel pasa a IPv4 solo (el laboratorio de
    Docker Desktop no tiene IPv6), ese peer NO tiene que seguir pidiendo una
    direccion v6 que del otro lado ya no existe. Se ignora en memoria, sin
    borrarla del archivo: si se vuelve a habilitar IPv6, sigue estando."""
    archivo = tmp_path / "peers.json"
    doble = PeerRegistry(str(archivo), "10.66.66.0/24", "fd42:4242:4242::/64")
    doble.add_peer("celular")
    assert doble.get("celular")["ip_v6"], "con doble stack si tiene v6"

    solo_v4 = PeerRegistry(str(archivo), "10.66.66.0/24", "")
    assert solo_v4.get("celular")["ip_v6"] == ""

    # el archivo quedo intacto: la IP sigue guardada para mas adelante
    guardado = json.loads(archivo.read_text(encoding="utf-8"))
    assert guardado[0]["ip_v6"]
