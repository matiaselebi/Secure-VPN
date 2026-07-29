#!/usr/bin/env python3
"""Desconecta la VPN: desactiva el kill switch (restaurando el firewall como
estaba) y baja el túnel."""

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
    result = manager.disconnect()
    for message in result["messages"]:
        print(f"[SecureVPN] {message}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
