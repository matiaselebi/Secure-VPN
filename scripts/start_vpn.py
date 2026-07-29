#!/usr/bin/env python3
"""Encendido completo de la VPN en un solo comando.

Hace, en orden y esperando entre etapas:

  1. se asegura de que Docker esté corriendo (si Docker Desktop está
     cerrado, lo abre y espera al motor),
  2. levanta el laboratorio (y lo recrea si cambió su receta),
  3. espera a que el SSH del servidor acepte sesiones de verdad,
  4. aprovisiona el servidor,
  5. conecta el túnel y activa el kill switch si corresponde.

Existe porque encadenar lab_up + provision + connect desde afuera, uno
apenas terminado el otro, generaba fallas de carrera: apt corriendo antes
de que el contenedor tuviera red, ssh antes de que sshd estuviera listo.
Acá las esperas están adentro y cada etapa arranca cuando la anterior está
realmente lista.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from securevpn.config_loader import load_config  # noqa: E402
from securevpn.logger_db import LoggerDB  # noqa: E402
from securevpn.ops import do_start_all  # noqa: E402
from securevpn.tunnel import TunnelManager  # noqa: E402


def main() -> int:
    cfg = load_config()
    logger_db = LoggerDB(str(cfg.resolve_path(cfg.logging.db_path)))
    manager = TunnelManager(cfg, logger_db)

    print("[SecureVPN] Encendido completo: Docker -> laboratorio -> aprovisionar -> conectar")
    print("[SecureVPN] (la primera vez puede tardar varios minutos)")
    print()

    def avisar(linea: str) -> None:
        # flush=True para que el progreso se vea a medida que pasa y no
        # aparezca todo junto al final cuando la salida está redirigida.
        print(f"[SecureVPN] {linea}", flush=True)

    ok, _lineas = do_start_all(manager, logger_db, avisar=avisar)
    print()
    if ok:
        print("[SecureVPN] VPN encendida y conectada.")
        return 0
    print("[SecureVPN] El encendido NO se completó (ver el detalle arriba).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
