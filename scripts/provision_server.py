#!/usr/bin/env python3
"""Aprovisiona el servidor WireGuard por SSH (VPS real o laboratorio local,
mismo camino de código). Idempotente: correrlo de nuevo re-aplica todo,
que es exactamente lo que hace falta tras agregar o quitar peers."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from securevpn.config_loader import load_config  # noqa: E402
from securevpn.logger_db import LoggerDB  # noqa: E402
from securevpn.ops import do_provision  # noqa: E402
from securevpn.tunnel import TunnelManager  # noqa: E402


def main() -> int:
    cfg = load_config()
    logger_db = LoggerDB(str(cfg.resolve_path(cfg.logging.db_path)))
    manager = TunnelManager(cfg, logger_db)

    print(f"[SecureVPN] Aprovisionando {cfg.server.ssh_user}@{cfg.server.ssh_host}:{cfg.server.ssh_port} por SSH...")
    print("[SecureVPN] (instala WireGuard + nftables + fail2ban, escribe la config,")
    print("[SecureVPN]  activa el ruteo y levanta el túnel; tarda 1-3 minutos)")

    ok, output = do_provision(manager, logger_db)
    print(output)
    if ok:
        print("[SecureVPN] Servidor aprovisionado y túnel levantado. Ya podés conectar.")
        return 0
    print("[SecureVPN] El aprovisionamiento FALLÓ (ver salida arriba).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
