#!/usr/bin/env python3
"""Agrega un peer (otro dispositivo tuyo, típicamente el celular) y muestra
el QR para escanearlo con la app oficial de WireGuard.

Uso: python scripts/add_peer.py celular

IMPORTANTE: después de agregar un peer hay que RE-APROVISIONAR el servidor
(opción del panel o scripts/provision_server.py) para que lo conozca - el
servidor solo acepta peers que figuran en su configuración.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import qrcode  # noqa: E402

from securevpn.config_loader import load_config  # noqa: E402
from securevpn.logger_db import LoggerDB  # noqa: E402
from securevpn.tunnel import TunnelManager  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("Uso: python scripts/add_peer.py <nombre>   (ej: celular)")
        return 1
    name = sys.argv[1].strip()

    cfg = load_config()
    logger_db = LoggerDB(str(cfg.resolve_path(cfg.logging.db_path)))
    manager = TunnelManager(cfg, logger_db)
    manager.ensure_initialized()

    if manager.registry.get(name) is None:
        manager.registry.add_peer(name)
        logger_db.log_event("peer_added", f"peer '{name}'")
        print(f"[SecureVPN] Peer '{name}' creado.")
    else:
        print(f"[SecureVPN] El peer '{name}' ya existía; muestro su config actual.")

    conf_path = manager.write_client_conf(name)
    conf_text = conf_path.read_text(encoding="utf-8")

    print(f"[SecureVPN] Configuración guardada en: {conf_path}")
    print("[SecureVPN] Escaneá este QR con la app de WireGuard del celular")
    print("[SecureVPN] (Agregar túnel -> Escanear código QR):")
    print()
    qr = qrcode.QRCode(border=2)
    qr.add_data(conf_text)
    # invert=True: en consolas con fondo negro (cmd) el QR se ve al derecho.
    qr.print_ascii(invert=True)
    print()
    print("[SecureVPN] El QR contiene la clave privada del peer: no lo compartas.")
    print("[SecureVPN] También está disponible en el dashboard (pestaña Peers).")
    print()
    print("[SecureVPN] RECORDÁ: re-aprovisionar el servidor para que acepte")
    print("[SecureVPN] este peer (opción del panel o scripts/provision_server.py).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
