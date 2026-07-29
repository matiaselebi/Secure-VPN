#!/usr/bin/env python3
"""Punto de entrada del dashboard web de SecureVPN (puerto 8891)."""

import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

PID_FILE = PROJECT_ROOT / "data" / "dashboard.pid"

from securevpn.config_loader import load_config  # noqa: E402
from securevpn.dashboard import build_dashboard_server  # noqa: E402
from securevpn.logger_db import LoggerDB  # noqa: E402
from securevpn.tunnel import TunnelManager  # noqa: E402


def main() -> None:
    cfg = load_config()
    logger_db = LoggerDB(str(cfg.resolve_path(cfg.logging.db_path)))
    manager = TunnelManager(cfg, logger_db)
    manager.ensure_initialized()

    server = build_dashboard_server(cfg.dashboard.host, cfg.dashboard.port, manager, logger_db)

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))

    print(f"[SecureVPN] dashboard: http://{cfg.dashboard.host}:{cfg.dashboard.port}/")
    print(f"[SecureVPN] endpoint del servidor: {cfg.server.endpoint_host}:{cfg.server.endpoint_port}")
    print(f"[SecureVPN] PID: {os.getpid()} (guardado en {PID_FILE})")
    print("[SecureVPN] Ctrl+C para detener (o 'python scripts/stop_dashboard.py').")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[SecureVPN] deteniendo dashboard...")
    finally:
        server.shutdown()
        if PID_FILE.exists():
            PID_FILE.unlink()
        time.sleep(0.1)


if __name__ == "__main__":
    main()
