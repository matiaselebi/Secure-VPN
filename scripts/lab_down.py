#!/usr/bin/env python3
"""Baja el contenedor del laboratorio (la VPN local deja de tener servidor).

Tranquilidad: si la VPN estaba conectada con kill switch, la forma correcta
de cortar es primero "Desconectar" (que limpia el firewall) y después bajar
el laboratorio. Si te olvidás del orden, siempre queda "Restaurar internet".
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from securevpn.config_loader import load_config  # noqa: E402
from securevpn.logger_db import LoggerDB  # noqa: E402
from securevpn.ops import do_lab_down  # noqa: E402


def main() -> int:
    cfg = load_config()
    ok, detail = do_lab_down(cfg, LoggerDB(str(cfg.resolve_path(cfg.logging.db_path))))
    print(f"[SecureVPN] {detail}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
