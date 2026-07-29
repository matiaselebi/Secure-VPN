"""Registro de eventos de la VPN en SQLite.

Misma filosofía que los logs de SecureProxy y SecureDNS: cada cosa que pasa
(conectar, desconectar, kill switch activado/desactivado, peer agregado,
servidor aprovisionado, restauración de emergencia) queda registrada con
fecha en `data/vpn_logs.db`, consultable desde el dashboard.
"""

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


class LoggerDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event TEXT NOT NULL,
                    detail TEXT,
                    ok INTEGER NOT NULL
                )
                """
            )
            conn.commit()

    def log_event(self, event: str, detail: str = "", ok: bool = True) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO events (timestamp, event, detail, ok) VALUES (?, ?, ?, ?)",
                (timestamp, event, detail, int(ok)),
            )
            conn.commit()

    def recent_events(self, limit: int = 25) -> list[tuple]:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "SELECT timestamp, event, detail, ok FROM events ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return cur.fetchall()

    def stats(self) -> dict:
        with self._lock, self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            connects = conn.execute(
                "SELECT COUNT(*) FROM events WHERE event = 'connect' AND ok = 1"
            ).fetchone()[0]
            errors = conn.execute("SELECT COUNT(*) FROM events WHERE ok = 0").fetchone()[0]
        return {"total_events": total, "connects": connects, "errors": errors}
