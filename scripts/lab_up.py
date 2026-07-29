#!/usr/bin/env python3
"""Levanta el laboratorio: un contenedor Docker que hace de "VPS de mentira".

El contenedor arranca siendo un Ubuntu pelado con SSH y nada más - sin
WireGuard, sin firewall, sin fail2ban. Eso es a propósito: el que lo
convierte en servidor VPN es el MISMO script de aprovisionamiento que se
usaría contra un VPS real (scripts/provision_server.py). Así el laboratorio
demuestra el flujo completo, no una maqueta.

Publica en tu PC:
  - 127.0.0.1:2222  -> SSH del contenedor (para el aprovisionamiento)
  - 127.0.0.1:51820 -> puerto UDP de WireGuard
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from securevpn.config_loader import load_config  # noqa: E402
from securevpn.logger_db import LoggerDB  # noqa: E402
from securevpn.ops import do_lab_up  # noqa: E402


def main() -> int:
    cfg = load_config()
    print("[SecureVPN] Levantando el contenedor del laboratorio (la primera vez tarda)...")
    ok, detail = do_lab_up(cfg, LoggerDB(str(cfg.resolve_path(cfg.logging.db_path))))
    print(f"[SecureVPN] {detail}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
