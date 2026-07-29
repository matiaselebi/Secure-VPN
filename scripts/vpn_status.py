#!/usr/bin/env python3
"""Estado del túnel en texto plano, para el menú .bat y para humanos."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

from securevpn.config_loader import load_config  # noqa: E402
from securevpn.killswitch import KillSwitch  # noqa: E402
from securevpn.ops import secureproxy_is_alive  # noqa: E402
from securevpn.status import get_status, humanize_bytes, humanize_handshake  # noqa: E402


def securedns_alive(cfg) -> bool:
    try:
        with urllib.request.urlopen(cfg.dns.securedns_probe_url, timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def main() -> int:
    cfg = load_config()
    status = get_status(cfg.tunnel.interface_name)

    killswitch_state = cfg.resolve_path("data") / "killswitch_state.json"
    ks = KillSwitch(
        cfg.tunnel.interface_name,
        cfg.server.endpoint_host,
        cfg.server.endpoint_port,
        str(killswitch_state),
        dry_run=True,
    )

    print(f"Endpoint configurado : {cfg.server.endpoint_host}:{cfg.server.endpoint_port}")
    print(f"Modo de túnel        : {cfg.tunnel.mode}")
    print(f"Kill switch          : {'ACTIVO' if ks.is_active() else 'inactivo'}")
    print(f"SecureDNS (hermano)  : {'activo' if securedns_alive(cfg) else 'apagado'}")
    print(f"SecureProxy (hermano): {'activo' if secureproxy_is_alive(cfg) else 'apagado'}")

    if not status.get("up"):
        extra = status.get("error", "")
        print(f"Túnel                : DESCONECTADO {f'({extra})' if extra else ''}")
        return 1

    print("Túnel                : interfaz levantada")
    for peer in status.get("peers", []):
        print(f"  - handshake        : {humanize_handshake(peer['latest_handshake'])}")
        print(f"    recibido/enviado : {humanize_bytes(peer['rx_bytes'])} / {humanize_bytes(peer['tx_bytes'])}")
        print(f"    endpoint         : {peer['endpoint']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
