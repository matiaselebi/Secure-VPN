"""Registro de peers (dispositivos que se conectan al túnel).

Cada peer es un dispositivo tuyo: la PC, el celular, una notebook. El
registro vive en `data/peers.json` (fuera de git, como todo `data/`) y
guarda, por peer: nombre, sus claves, su clave presimétrica compartida con
el servidor, y las IPs internas del túnel que le tocaron.

La asignación de IPs es automática: el servidor se queda con la primera IP
de la red del túnel (la .1) y cada peer nuevo recibe la siguiente libre
(.2, .3, ...), tanto en IPv4 como en IPv6.
"""

import ipaddress
import json
from datetime import datetime, timezone
from pathlib import Path

from .keys import derive_public_key, generate_preshared_key, generate_private_key

SERVER_HOST_INDEX = 1
FIRST_PEER_HOST_INDEX = 2


def _host_ip(network: str, index: int) -> str:
    """La IP número `index` dentro de una red (ej: índice 1 de 10.66.66.0/24
    es 10.66.66.1). Funciona igual para IPv4 e IPv6."""
    net = ipaddress.ip_network(network, strict=False)
    return str(net.network_address + index)


def server_ip_v4(network_v4: str) -> str:
    return _host_ip(network_v4, SERVER_HOST_INDEX)


def server_ip_v6(network_v6: str) -> str:
    return _host_ip(network_v6, SERVER_HOST_INDEX)


class PeerRegistry:
    def __init__(self, path: str, network_v4: str, network_v6: str | None = None):
        self.path = Path(path)
        self.network_v4 = network_v4
        # network_v6 vacío/None = túnel solo IPv4 (para redes o servidores
        # sin IPv6; el default del proyecto sigue siendo dual-stack).
        self.network_v6 = network_v6 or ""
        self.peers: list[dict] = []
        if self.path.exists():
            self.peers = json.loads(self.path.read_text(encoding="utf-8"))
        if not self.network_v6:
            # El túnel pasó a IPv4 solo (por ejemplo al usar el laboratorio,
            # donde los contenedores de Docker Desktop no tienen IPv6). Los
            # peers guardados de antes conservan su IP v6 en el archivo, y si
            # se siguiera usando, el .conf del cliente pediría una dirección
            # v6 que del otro lado no existe. Se ignora en memoria, sin tocar
            # el archivo: si mañana se vuelve a habilitar IPv6, las IPs
            # siguen ahí y cada peer conserva la suya.
            for peer in self.peers:
                peer["ip_v6"] = ""

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.peers, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _next_host_index(self) -> int:
        used = {peer["host_index"] for peer in self.peers}
        index = FIRST_PEER_HOST_INDEX
        while index in used:
            index += 1
        max_hosts = ipaddress.ip_network(self.network_v4, strict=False).num_addresses - 2
        if index > max_hosts:
            raise ValueError(f"no quedan IPs libres en {self.network_v4}")
        return index

    def add_peer(self, name: str) -> dict:
        name = name.strip()
        if not name:
            raise ValueError("el peer necesita un nombre (ej: 'pc', 'celular')")
        if any(peer["name"] == name for peer in self.peers):
            raise ValueError(f"ya existe un peer llamado {name!r}")

        private_key = generate_private_key()
        host_index = self._next_host_index()
        peer = {
            "name": name,
            "host_index": host_index,
            "private_key": private_key,
            "public_key": derive_public_key(private_key),
            "preshared_key": generate_preshared_key(),
            "ip_v4": _host_ip(self.network_v4, host_index),
            "ip_v6": _host_ip(self.network_v6, host_index) if self.network_v6 else "",
            "created": datetime.now(timezone.utc).isoformat(),
        }
        self.peers.append(peer)
        self._save()
        return peer

    def remove_peer(self, name: str) -> bool:
        before = len(self.peers)
        self.peers = [peer for peer in self.peers if peer["name"] != name]
        if len(self.peers) != before:
            self._save()
            return True
        return False

    def get(self, name: str) -> dict | None:
        for peer in self.peers:
            if peer["name"] == name:
                return peer
        return None
