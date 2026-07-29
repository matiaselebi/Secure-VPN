#!/usr/bin/env python3
"""Botón de emergencia: revierte el firewall (borra las reglas SecureVPN_ y
restablece la política por defecto) y baja el túnel, incondicionalmente.
Para cuando algo quedó a medias y querés tu internet normal YA."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from securevpn.config_loader import load_config  # noqa: E402
from securevpn.logger_db import LoggerDB  # noqa: E402
from securevpn.tunnel import TunnelManager  # noqa: E402


def main() -> int:
    cfg = load_config()
    manager = TunnelManager(cfg, LoggerDB(str(cfg.resolve_path(cfg.logging.db_path))))
    result = manager.restore_internet()
    for message in result["messages"]:
        print(f"[SecureVPN] {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
