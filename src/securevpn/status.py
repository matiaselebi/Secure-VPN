"""Estado del túnel, leyendo la salida de `wg show <interfaz> dump`.

`wg` (viene con WireGuard, en Windows y Linux) tiene un formato de salida
pensado para scripts: `dump` imprime una línea por peer, separada por tabs,
con handshake en timestamp Unix y bytes transferidos en crudo. Acá se
parsea eso a un dict, y se formatea a algo legible para el dashboard y el
menú .bat ("hace 12 segundos", "3.4 MB").
"""

import platform
import shutil
import subprocess
import time
from pathlib import Path

WINDOWS_WIREGUARD_DIR = Path("C:/Program Files/WireGuard")

# En Windows, cada subprocess abre una ventanita de consola que "parpadea".
# El dashboard consulta `wg show` cada pocos segundos, así que sin esto se ve
# un cmd apareciendo y desapareciendo todo el tiempo. CREATE_NO_WINDOW lo
# ejecuta sin ventana. En Linux/macOS la constante es 0 (sin efecto).
CREATE_NO_WINDOW = 0x08000000 if platform.system() == "Windows" else 0


def find_wg_binary() -> str | None:
    """Encuentra el ejecutable `wg`: en Windows lo instala la app oficial en
    Program Files; en Linux está en el PATH si wireguard-tools está instalado."""
    if platform.system() == "Windows":
        candidate = WINDOWS_WIREGUARD_DIR / "wg.exe"
        if candidate.exists():
            return str(candidate)
    return shutil.which("wg")


def parse_wg_dump(dump_output: str) -> dict:
    """Parsea la salida de `wg show <iface> dump`.

    Primera línea (la interfaz): private_key, public_key, listen_port, fwmark.
    Cada línea siguiente (un peer): public_key, preshared_key, endpoint,
    allowed_ips, latest_handshake, rx_bytes, tx_bytes, keepalive.
    """
    lines = [line for line in dump_output.strip().splitlines() if line.strip()]
    if not lines:
        return {"up": False, "peers": []}

    interface_fields = lines[0].split("\t")
    peers = []
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) < 8:
            continue
        peers.append(
            {
                "public_key": fields[0],
                "endpoint": fields[2],
                "allowed_ips": fields[3],
                "latest_handshake": int(fields[4]),
                "rx_bytes": int(fields[5]),
                "tx_bytes": int(fields[6]),
                "keepalive": fields[7],
            }
        )
    return {
        "up": True,
        "public_key": interface_fields[1] if len(interface_fields) > 1 else "",
        "listen_port": interface_fields[2] if len(interface_fields) > 2 else "",
        "peers": peers,
    }


def get_status(interface_name: str) -> dict:
    """Estado en vivo del túnel. {"up": False} si la interfaz no existe
    (VPN desconectada) o si `wg` no está instalado."""
    wg = find_wg_binary()
    if wg is None:
        return {"up": False, "peers": [], "error": "wg no encontrado (¿WireGuard instalado?)"}
    try:
        result = subprocess.run(
            [wg, "show", interface_name, "dump"],
            capture_output=True, text=True, timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return {"up": False, "peers": [], "error": str(exc)}
    if result.returncode != 0:
        return {"up": False, "peers": []}
    return parse_wg_dump(result.stdout)


def humanize_bytes(count: int) -> str:
    value = float(count)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def humanize_handshake(handshake_timestamp: int, now: float | None = None) -> str:
    """Cuánto hace del último handshake. Con el keepalive de 25s, un túnel
    sano nunca debería mostrar más de ~2-3 minutos acá."""
    if handshake_timestamp == 0:
        return "nunca (todavía sin handshake)"
    seconds = int((now if now is not None else time.time()) - handshake_timestamp)
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"hace {seconds} s"
    if seconds < 3600:
        return f"hace {seconds // 60} min {seconds % 60} s"
    return f"hace {seconds // 3600} h {(seconds % 3600) // 60} min"
