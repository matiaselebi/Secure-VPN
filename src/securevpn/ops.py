"""Operaciones largas compartidas por el panel .bat y el dashboard.

Aprovisionar el servidor y levantar/bajar el laboratorio se pueden disparar
desde dos lugares (el menú .bat y los botones del dashboard), así que la
lógica vive acá una sola vez y ambos la llaman. Cada operación devuelve
(ok, texto) y deja su evento en el log de SQLite, que es donde el dashboard
muestra el progreso.
"""

import hashlib
import platform
import shutil
import socket
import subprocess
import time
from pathlib import Path

from .config_loader import PROJECT_ROOT, Config
from .docker_desktop import CREATE_NO_WINDOW, ensure_engine
from .logger_db import LoggerDB
from .provision import build_ssh_command, provision_server, render_provision_script


def ssh_usable(cfg: Config, timeout: float = 20.0) -> bool:
    """¿Se puede ENTRAR por SSH ahora mismo, no solo abrir el puerto?

    El puerto TCP abre apenas arranca el contenedor, pero sshd puede tardar
    otro momento en aceptar la sesión. Aprovisionar en ese hueco fallaba con
    errores raros ("Connection closed by remote host"). Este chequeo abre una
    sesión de verdad y corre `true`: si eso sale bien, aprovisionar va a
    andar."""
    ssh_key = cfg.resolve_path(cfg.server.ssh_key_path)
    comando = build_ssh_command(
        cfg.server.ssh_host, cfg.server.ssh_port, cfg.server.ssh_user,
        str(ssh_key) if ssh_key.exists() else None,
    )
    comando = comando[:-1] + ["true"]  # reemplaza el "bash -s" por un no-op
    try:
        proc = subprocess.run(
            comando, capture_output=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def wait_ssh_usable(cfg: Config, espera_maxima: float = 60.0) -> bool:
    """Espera hasta que el SSH del servidor acepte sesiones de verdad."""
    limite = time.time() + espera_maxima
    while True:
        if ssh_usable(cfg):
            return True
        if time.time() >= limite:
            return False
        time.sleep(2)


def do_provision(manager, logger_db: LoggerDB) -> tuple[bool, str]:
    """Aprovisiona el servidor configurado (laboratorio o VPS real) por SSH
    con el estado actual de peers. Tarda 1-3 minutos la primera vez."""
    cfg: Config = manager.cfg
    manager.ensure_initialized()
    logger_db.log_event(
        "provision_start",
        f"{cfg.server.ssh_user}@{cfg.server.ssh_host}:{cfg.server.ssh_port} (tarda 1-3 min)",
    )
    ssh_key = cfg.resolve_path(cfg.server.ssh_key_path)
    script = render_provision_script(
        server_conf=manager.render_server_conf(),
        interface_name=cfg.tunnel.interface_name,
        listen_port=cfg.server.endpoint_port,
        public_interface=cfg.server.public_interface,
        ssh_port=cfg.server.ssh_port,
    )
    ok, output = provision_server(
        script,
        cfg.server.ssh_host,
        cfg.server.ssh_port,
        cfg.server.ssh_user,
        str(ssh_key) if ssh_key.exists() else None,
    )
    logger_db.log_event(
        "provision",
        "servidor listo: ya podés conectar" if ok else f"falló: {output[-300:]}",
        ok=ok,
    )
    return ok, output


def compose_command() -> list[str] | None:
    if shutil.which("docker"):
        probe = subprocess.run(
            ["docker", "compose", "version"], capture_output=True, text=True,
            creationflags=CREATE_NO_WINDOW,
        )
        if probe.returncode == 0:
            return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    return None


def ensure_lab_ssh_key(key_path: Path) -> None:
    """Genera (una sola vez) el par de claves SSH del laboratorio."""
    if key_path.exists():
        return
    key_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "securevpn-lab", "-f", str(key_path)],
        check=True, capture_output=True, text=True,
    )


LAB_STAMP_PATH = PROJECT_ROOT / "data" / "lab_build.stamp"


def lab_fingerprint(cfg: Config) -> str:
    """Huella de la "receta" del laboratorio (compose + Dockerfile).

    Sirve para detectar que la receta cambió desde la última vez que se
    construyó el contenedor. Docker no lo detecta solo: `docker compose up -d`
    sobre un contenedor que ya existe lo deja tal cual, aunque el compose diga
    otra cosa. Ese fue exactamente el problema con el DNS fijo del laboratorio:
    el archivo estaba bien, pero el contenedor viejo seguía sin DNS.
    """
    partes: list[bytes] = []
    compose_file = cfg.resolve_path(cfg.lab.compose_file)
    for archivo in (compose_file, compose_file.parent / "Dockerfile.lab"):
        try:
            partes.append(Path(archivo).read_bytes())
        except OSError:
            partes.append(b"")
    return hashlib.sha256(b"||".join(partes)).hexdigest()[:16]


def _leer_stamp() -> str:
    try:
        return LAB_STAMP_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _escribir_stamp(huella: str) -> None:
    try:
        LAB_STAMP_PATH.parent.mkdir(parents=True, exist_ok=True)
        LAB_STAMP_PATH.write_text(huella, encoding="utf-8")
    except OSError:
        pass


SENALES_DE_REGISTRO = (
    "failed to resolve source metadata",
    "registry-1.docker.io",
    "failed to do request",
    "proxyconnect",
    "connection refused",
    "i/o timeout",
)


def _con_pista_de_proxy(error: str) -> str:
    """Le agrega al error de Docker la causa real cuando pinta a "no pudo
    salir a internet" y el proxy del sistema está encendido.

    Construir la imagen del laboratorio es lo único que necesita internet, y
    ahí Docker se lleva una sorpresa: hereda el proxy del sistema, pero corre
    dentro de una máquina virtual donde `127.0.0.1` es la VM y no tu PC, así
    que SecureProxy le queda inalcanzable. El error que muestra Docker no
    dice nada de eso.
    """
    if not any(senal in error for senal in SENALES_DE_REGISTRO):
        return error
    if not system_proxy_enabled():
        return error
    return (
        f"{error}\n\n"
        "Pista: tenés el proxy del sistema encendido (SecureProxy) y Docker "
        "lo hereda, pero desde su máquina virtual 127.0.0.1 no es tu PC, así "
        "que no puede descargar la imagen base. Se arregla de una de estas "
        "dos formas: (1) en Docker Desktop -> Settings -> Resources -> "
        "Proxies, desactivá 'Use system proxy'; o (2) apagá el núcleo un "
        "minuto, encendé la VPN (que construye la imagen) y volvé a prender "
        "el núcleo. Es solo la primera vez: después la imagen queda guardada."
    )


def system_proxy_enabled() -> bool:
    """¿Windows tiene un proxy configurado a nivel sistema ahora mismo?"""
    if platform.system() != "Windows":
        return False
    proc = subprocess.run(
        ["reg", "query",
         r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings",
         "/v", "ProxyEnable"],
        capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
    )
    if proc.returncode != 0:
        return False
    # La salida trae algo como "ProxyEnable    REG_DWORD    0x1"
    return "0x1" in (proc.stdout or "")


def do_lab_up(cfg: Config, logger_db: LoggerDB) -> tuple[bool, str]:
    """Levanta el contenedor del laboratorio y espera a que exponga SSH.

    Antes de nada se asegura de que Docker esté corriendo: si Docker Desktop
    está cerrado, lo abre y espera al motor (ver docker_desktop.py). Si ya
    estaba corriendo, ese paso no cuesta nada.

    Después es idempotente y "barato" a propósito:

    1. Si el laboratorio YA está respondiendo por SSH **y la receta no
       cambió**, no toca nada. Antes siempre corría `up --build`, que
       necesita bajar la imagen base de Docker Hub; si el registro no era
       alcanzable (por ejemplo con el proxy del sistema encendido), fallaba
       aunque el laboratorio estuviera perfectamente levantado.
    2. Si la receta cambió (compose o Dockerfile distintos a los de la última
       construcción), baja el contenedor viejo y lo vuelve a crear. Sin esto,
       arreglos como el DNS fijo del compose no se aplicaban nunca en una PC
       donde el laboratorio ya existía.
    3. Si no está arriba, intenta primero `up -d` SIN `--build`, que reusa la
       imagen ya construida y no necesita internet.
    4. Solo si eso falla, reintenta con `--build` (primera vez real), que sí
       requiere poder descargar la base.
    """
    huella = lab_fingerprint(cfg)
    receta_cambio = _leer_stamp() != huella

    if server_ssh_reachable(cfg, timeout=2.0) and not receta_cambio:
        detail = (
            f"el laboratorio ya estaba levantado (SSH responde en "
            f"{cfg.server.ssh_host}:{cfg.server.ssh_port}); no hace falta reconstruirlo"
        )
        logger_db.log_event("lab_up", detail)
        return True, detail

    motor_ok, motor_detalle = ensure_engine()
    logger_db.log_event("docker_engine", motor_detalle, ok=motor_ok)
    if not motor_ok:
        return False, motor_detalle

    compose = compose_command()
    if compose is None:
        detail = "no encontré Docker: instalá Docker Desktop (docker.com)"
        logger_db.log_event("lab_up", detail, ok=False)
        return False, detail

    logger_db.log_event("lab_up_start", "levantando el contenedor (la primera vez tarda)")
    ensure_lab_ssh_key(cfg.resolve_path(cfg.server.ssh_key_path))
    compose_file = cfg.resolve_path(cfg.lab.compose_file)

    if receta_cambio:
        logger_db.log_event(
            "lab_recrear",
            "la receta del laboratorio cambió: bajo el contenedor viejo y lo rehago",
        )
        subprocess.run(
            compose + ["-f", str(compose_file), "down"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True,
            creationflags=CREATE_NO_WINDOW,
        )
        intentos = (
            (["up", "-d", "--build"], "reconstruyendo la imagen con la receta nueva"),
        )
    else:
        intentos = (
            (["up", "-d"], "reusando la imagen ya construida"),
            (["up", "-d", "--build"], "construyendo la imagen (necesita descargar la base)"),
        )

    ultimo_error = ""
    for args, descripcion in intentos:
        result = subprocess.run(
            compose + ["-f", str(compose_file), *args],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True,
            creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            break
        ultimo_error = (result.stderr or result.stdout or "docker compose falló").strip()[-300:]
        logger_db.log_event("lab_up_intento", f"falló {descripcion}: {ultimo_error[-150:]}", ok=False)
    else:
        ultimo_error = _con_pista_de_proxy(ultimo_error)
        logger_db.log_event("lab_up", ultimo_error, ok=False)
        return False, ultimo_error

    _escribir_stamp(huella)

    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            socket.create_connection(
                (cfg.server.ssh_host, cfg.server.ssh_port), timeout=2
            ).close()
            detail = (
                f"laboratorio listo (SSH en {cfg.server.ssh_host}:{cfg.server.ssh_port}); "
                "siguiente paso: aprovisionar"
            )
            logger_db.log_event("lab_up", detail)
            return True, detail
        except OSError:
            time.sleep(2)
    detail = "el contenedor no expuso SSH en 60s; revisá 'docker compose logs'"
    logger_db.log_event("lab_up", detail, ok=False)
    return False, detail


def do_lab_down(cfg: Config, logger_db: LoggerDB) -> tuple[bool, str]:
    """Baja el contenedor del laboratorio."""
    if not shutil.which("docker"):
        detail = "no encontré Docker"
        logger_db.log_event("lab_down", detail, ok=False)
        return False, detail
    compose_file = cfg.resolve_path(cfg.lab.compose_file)
    result = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "down"],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True,
        creationflags=CREATE_NO_WINDOW,
    )
    ok = result.returncode == 0
    detail = "laboratorio detenido" if ok else (result.stderr or "docker compose down falló").strip()[-300:]
    logger_db.log_event("lab_down", detail, ok=ok)
    return ok, detail


def secureproxy_is_alive(cfg: Config, timeout: float = 1.5) -> bool:
    """¿SecureProxy está escuchando? Chequeo de conexión TCP a su puerto
    (8888 por defecto). No se le manda ninguna petición: con que acepte la
    conexión alcanza para saber que el proceso está vivo."""
    try:
        socket.create_connection(
            (cfg.stack.secureproxy_host, cfg.stack.secureproxy_port), timeout=timeout
        ).close()
        return True
    except OSError:
        return False


def server_ssh_reachable(cfg: Config, timeout: float = 1.5) -> bool:
    """¿El SSH del servidor (lab o VPS) responde ahora mismo? Para mostrar
    en el dashboard si hay "otra punta" a la cual aprovisionar/conectar."""
    try:
        socket.create_connection((cfg.server.ssh_host, cfg.server.ssh_port), timeout=timeout).close()
        return True
    except OSError:
        return False


def _limpiar_intento_anterior(manager) -> tuple[bool, str]:
    """Baja un túnel que quedó a medias de un encendido anterior.

    Devuelve (no_habia_nada_que_limpiar, detalle).

    El caso real: se aprieta Encender VPN, el servidor todavía no estaba
    aprovisionado, el túnel se instala pero nunca hace handshake. Queda una
    interfaz de WireGuard instalada y sin funcionar. Al siguiente intento,
    instalar el túnel falla porque ya existe... y el mensaje de error habla
    de eso, no de la causa real. Un túnel SIN handshake no sirve para nada,
    así que se baja y se empieza de cero.

    Un túnel que SÍ está funcionando no se toca: si ya estaba conectado, el
    encendido es idempotente y no hay nada que arreglar.
    """
    try:
        from .status import get_status

        estado = get_status(manager.cfg.tunnel.interface_name)
        if not estado.get("up"):
            return True, "no había nada de un intento anterior"
        peers = estado.get("peers") or []
        con_handshake = any(p.get("latest_handshake") for p in peers)
        if con_handshake:
            return True, "el túnel ya estaba conectado y funcionando"
        manager.disconnect()
        return False, "había un túnel a medias (sin handshake) de un intento anterior: lo bajé"
    except Exception as exc:  # noqa: BLE001 - la limpieza nunca debe cortar el encendido
        return True, f"no pude revisar el estado previo ({exc}); sigo igual"


def do_start_all(manager, logger_db: LoggerDB, avisar=None) -> tuple[bool, list[str]]:
    """Encendido completo de la VPN en un solo paso, en el orden correcto y
    esperando entre etapas.

    Es "el botón": Docker -> laboratorio -> SSH usable -> aprovisionar ->
    conectar. Antes esto eran tres scripts encadenados por el orquestador,
    uno arrancando apenas terminaba el anterior; entre medio no había ninguna
    espera, y por eso aparecían fallas de carrera (apt corriendo antes de que
    el contenedor tuviera red, ssh antes de que sshd aceptara sesiones).
    Teniendo las etapas acá adentro se puede esperar de verdad a que cada una
    esté lista antes de largar la siguiente.

    Devuelve (ok, líneas). `avisar` es una función opcional que recibe cada
    línea a medida que pasa, para que el .bat pueda imprimir el progreso en
    vivo en vez de mostrar todo junto al final.
    """
    cfg: Config = manager.cfg
    lineas: list[str] = []

    def paso(texto: str, ok: bool = True) -> None:
        lineas.append(texto)
        # Cada etapa queda en el log de SQLite: así el progreso se ve en vivo
        # en la pestaña Eventos del dashboard aunque el encendido lo haya
        # disparado el .bat, y al revés.
        logger_db.log_event("encendido_vpn", texto[:300], ok=ok)
        if avisar is not None:
            avisar(texto)

    # 0) Arranque limpio: si quedó un túnel a medias de un intento anterior
    # -la interfaz instalada pero sin handshake- se baja ANTES de empezar.
    # Sin esto, el intento nuevo se pisa con el viejo: `wireguard.exe` se
    # niega a instalar un túnel que ya existe, el kill switch puede quedar de
    # la corrida anterior, y el error que sale no tiene nada que ver con la
    # causa real.
    limpio, detalle_limpieza = _limpiar_intento_anterior(manager)
    if not limpio:
        paso(f"Limpieza previa: {detalle_limpieza}")

    # 1) Docker corriendo (abre Docker Desktop si hace falta).
    ok, detalle = ensure_engine(avisar=lambda t: paso(f"Docker: {t}"))
    paso(f"Docker: {detalle}", ok)
    if not ok:
        return False, lineas

    # 2) Laboratorio levantado (y recreado si cambió la receta).
    ok, detalle = do_lab_up(cfg, logger_db)
    paso(f"Laboratorio: {detalle}", ok)
    if not ok:
        return False, lineas

    # 3) SSH realmente usable antes de aprovisionar.
    if not wait_ssh_usable(cfg, espera_maxima=60):
        paso(
            "SSH: el servidor no aceptó una sesión en 60s. Probá bajar y volver "
            "a levantar el laboratorio.",
            False,
        )
        return False, lineas
    paso("SSH: el servidor acepta sesiones, sigo con el aprovisionamiento")

    # 4) Aprovisionar.
    ok, salida = do_provision(manager, logger_db)
    if not ok:
        paso(f"Aprovisionamiento: FALLÓ - {salida[-400:]}", False)
        return False, lineas
    paso("Aprovisionamiento: servidor listo")

    # 5) Conectar el túnel (+ kill switch, si está habilitado).
    resultado = manager.connect()
    for mensaje in resultado["messages"]:
        paso(f"Conexión: {mensaje}")
    return bool(resultado["ok"]), lineas
