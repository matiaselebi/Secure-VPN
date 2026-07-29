"""Encendido automático de Docker Desktop antes de usar el laboratorio.

El laboratorio corre adentro de Docker, así que si Docker no está corriendo
no hay nada que hacer. Sin esto, el encendido de la VPN moría con un error
del estilo "error during connect: ... the system cannot find the file
specified", que no le dice nada a quien solo apretó "Encender VPN".

Este módulo hace lo que haría una persona: mira si el motor de Docker
responde; si no responde, busca Docker Desktop, lo abre y espera a que el
motor levante. Si Docker Desktop ya estaba corriendo (aunque esté
minimizado o en la bandeja del sistema), no lo toca. Y si Docker no está
instalado, lo dice claro y abre la página de descarga, en vez de dejar un
error técnico.

Dos detalles que costaron sangre:

- **"Abierto" se mide por el MOTOR, no por la ventana.** Docker Desktop
  minimizado en la bandeja está perfectamente corriendo; y al revés, la
  ventana puede estar abierta mientras el motor todavía arranca. Por eso lo
  único que se chequea es si `docker info` contesta.
- **Encontrar el ejecutable no es adivinar rutas.** Probar una lista fija de
  carpetas falla apenas alguien lo instaló en otro disco o solo para su
  usuario. Acá se arranca por el camino que SIEMPRE está bien: si `docker`
  responde en la línea de comandos, el ejecutable de Docker Desktop está
  unas carpetas más arriba del `docker.exe` que ya sabemos dónde está.
"""

import ctypes
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

# En Windows, evita que cada subproceso abra una ventanita negra de consola.
CREATE_NO_WINDOW = 0x08000000 if platform.system() == "Windows" else 0

NOMBRE_EJECUTABLE = "Docker Desktop.exe"
URL_DESCARGA = "https://www.docker.com/products/docker-desktop/"

# Rutas típicas, como último recurso: primero se intenta deducir la real.
RUTAS_TIPICAS = (
    r"C:\Program Files\Docker\Docker",
    r"C:\Program Files (x86)\Docker\Docker",
)


def _run(argv: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def engine_ready(timeout: float = 20.0) -> bool:
    """¿El motor de Docker está levantado y aceptando comandos?

    `docker info` es la prueba honesta: el ejecutable `docker` puede existir
    (está instalado) y aun así fallar porque el motor no arrancó.
    """
    if not shutil.which("docker"):
        return False
    proc = _run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=timeout)
    return proc is not None and proc.returncode == 0


def _desde_el_docker_del_path() -> Path | None:
    """Deduce dónde está Docker Desktop a partir del `docker` de la consola.

    Es el camino más confiable de todos: si `docker` funciona, su ejecutable
    vive dentro de la instalación (típicamente en
    `...\\Docker\\Docker\\resources\\bin\\docker.exe`), así que subiendo por
    las carpetas padre aparece el `Docker Desktop.exe` de esa MISMA
    instalación, esté donde esté: otro disco, otra carpeta, o instalado solo
    para este usuario.
    """
    cli = shutil.which("docker")
    if not cli:
        return None
    try:
        actual = Path(cli).resolve()
    except OSError:
        return None
    for padre in actual.parents:
        candidato = padre / NOMBRE_EJECUTABLE
        try:
            if candidato.exists():
                return candidato
        except OSError:
            continue
    return None


def _desde_el_registro() -> Path | None:
    """Docker Desktop deja su carpeta de instalación en el registro."""
    if platform.system() != "Windows":
        return None
    claves = (
        (r"HKLM\SOFTWARE\Docker Inc.\Docker\1.0", "AppPath"),
        (
            r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop",
            "InstallLocation",
        ),
    )
    for clave, valor in claves:
        proc = _run(["reg", "query", clave, "/v", valor], timeout=10)
        if proc is None or proc.returncode != 0:
            continue
        for linea in (proc.stdout or "").splitlines():
            if valor.lower() not in linea.lower():
                continue
            # Formato: "    AppPath    REG_SZ    C:\Program Files\Docker\Docker"
            partes = linea.split("REG_SZ")
            if len(partes) < 2:
                continue
            carpeta = Path(partes[1].strip())
            candidato = carpeta / NOMBRE_EJECUTABLE
            try:
                if candidato.exists():
                    return candidato
            except OSError:
                continue
    return None


def _desde_rutas_tipicas() -> Path | None:
    candidatas = [Path(r) / NOMBRE_EJECUTABLE for r in RUTAS_TIPICAS]
    for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if base:
            candidatas.append(Path(base) / "Docker" / "Docker" / NOMBRE_EJECUTABLE)
            candidatas.append(Path(base) / "Docker" / NOMBRE_EJECUTABLE)
    for candidato in candidatas:
        try:
            if candidato.exists():
                return candidato
        except OSError:
            continue
    return None


def desktop_executable() -> Path | None:
    """Ruta a Docker Desktop.exe, o None si no está instalado.

    Se prueban tres formas, de la más confiable a la menos: deducirla del
    `docker` que ya está en el PATH, leerla del registro, y recién al final
    probar las carpetas donde suele instalarse.
    """
    if platform.system() != "Windows":
        return None
    for buscador in (_desde_el_docker_del_path, _desde_el_registro, _desde_rutas_tipicas):
        encontrado = buscador()
        if encontrado is not None:
            return encontrado
    return None


def docker_instalado() -> bool:
    """¿Docker está instalado en esta PC, de alguna forma?"""
    return bool(shutil.which("docker")) or desktop_executable() is not None


# Procesos que indican que Docker Desktop está levantado o levantándose. El
# de la interfaz es el primero, pero el motor lo levantan los de atrás: en
# algunas versiones la ventana se cierra y el backend sigue, y al revés.
PROCESOS_DOCKER = (
    NOMBRE_EJECUTABLE,
    "com.docker.backend.exe",
    "com.docker.build.exe",
    "dockerd.exe",
)


def desktop_process_running() -> bool:
    """¿Hay algún proceso de Docker Desktop dando vueltas? (aunque el motor
    todavía no esté listo). Sirve para no abrir una segunda instancia
    mientras la primera está terminando de arrancar, y para distinguir
    "está arrancando" de "sigue cerrado" en los mensajes.

    Se mira la lista COMPLETA de procesos en vez de filtrar por nombre: el
    filtro de `tasklist` con un nombre que lleva espacio no es confiable en
    todos los Windows, y dio falsos negativos -decía que Docker no estaba
    cuando sí estaba-. Recorrer la lista es un poco más caro y siempre
    correcto.
    """
    if platform.system() != "Windows":
        return False
    proc = _run(["tasklist", "/NH", "/FO", "CSV"], timeout=20.0)
    if proc is None or proc.returncode != 0:
        return False
    salida = (proc.stdout or "").lower()
    return any(nombre.lower() in salida for nombre in PROCESOS_DOCKER)


def abrir_pagina_de_descarga() -> bool:
    """Abre la página de descarga de Docker Desktop en el navegador.

    Si Docker no está instalado, el encendido no puede continuar de ninguna
    manera: lo más útil que se puede hacer es dejar al usuario a un clic de
    resolverlo, en vez de solo informarle un error.
    """
    try:
        import webbrowser

        return webbrowser.open(URL_DESCARGA)
    except Exception:  # noqa: BLE001 - abrir el navegador nunca es crítico
        return False


def _lanzar_docker_desktop(ejecutable: Path, avisar=None) -> tuple[bool, str]:
    """Abre Docker Desktop, probando varias formas hasta que una funcione.

    Por qué no alcanza con un `Popen` y listo: desde el menú `.bat` andaba y
    desde el dashboard no. La diferencia es el contexto del proceso que
    lanza. El dashboard corre sin ventana (`pythonw.exe`), arrancado por una
    tarea de inicio de sesión o por SecureCenter, que se auto-eleva; y una
    aplicación de interfaz gráfica como Docker Desktop no siempre arranca
    bien cuando la lanza un proceso así: heredaría los permisos de
    administrador (Docker Desktop está pensado para correr como el usuario
    normal) y hereda banderas de consola que para una app gráfica no tienen
    sentido.

    Las tres formas, en orden:

    1. **Through Explorer.** `explorer.exe "<ruta>"` le pide al Explorador de
       Windows que abra el programa. Explorer corre siempre como el usuario
       normal y en su sesión interactiva, así que Docker Desktop arranca como
       si le hubieras hecho doble clic, aunque quien lo pida esté elevado.
       Es el que resuelve el caso del dashboard.
    2. **ShellExecute**, la misma llamada que usa Windows al hacer doble clic.
    3. **Popen** directo, el de toda la vida, como último recurso.

    Después de cada intento se espera un momento y se verifica que el proceso
    haya aparecido de verdad, en vez de asumir que salió bien: lanzar un
    programa "sin error" no significa que haya arrancado.
    """

    def paso(texto: str) -> None:
        if avisar is not None:
            avisar(texto)

    def aparecio(segundos: float = 5.0) -> bool:
        # Chequeo corto a propósito: acá solo interesa saber si conviene
        # seguir probando otras formas. La espera de verdad -que puede ser de
        # minutos, y que también sirve si lo abre el usuario a mano- la hace
        # ensure_engine después.
        limite = time.time() + segundos
        while time.time() < limite:
            if desktop_process_running() or engine_ready(timeout=5.0):
                return True
            time.sleep(1)
        return False

    intentos = []

    # 1) Vía Explorer: arranca como el usuario normal, no como administrador.
    try:
        subprocess.Popen(
            ["explorer.exe", str(ejecutable)],
            creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if aparecio():
            return True, "abierto a través del Explorador de Windows"
        intentos.append("por Explorer no apareció")
    except OSError as exc:
        intentos.append(f"Explorer falló ({exc})")

    # 2) ShellExecute: lo mismo que hacer doble clic en el ejecutable.
    paso("Docker no arrancó por Explorer; pruebo abrirlo como un doble clic...")
    try:
        SW_SHOWNORMAL = 1
        resultado = ctypes.windll.shell32.ShellExecuteW(
            None, "open", str(ejecutable), None, str(ejecutable.parent), SW_SHOWNORMAL
        )
        if resultado > 32 and aparecio():
            return True, "abierto con ShellExecute"
        intentos.append(f"ShellExecute devolvió {resultado}")
    except Exception as exc:  # noqa: BLE001
        intentos.append(f"ShellExecute falló ({exc})")

    # 3) Lanzarlo directo.
    paso("Sigo sin poder abrirlo; último intento, lanzándolo directo...")
    try:
        subprocess.Popen(
            [str(ejecutable)],
            cwd=str(ejecutable.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if aparecio():
            return True, "abierto directamente"
        intentos.append("lanzado directo pero no apareció el proceso")
    except OSError as exc:
        intentos.append(f"lanzarlo directo falló ({exc})")

    return False, "no pude abrir Docker Desktop (" + "; ".join(intentos) + ")"


def ensure_engine(espera_maxima: int = 300, avisar=None) -> tuple[bool, str]:
    """Deja el motor de Docker listo para usar.

    Devuelve (ok, explicación). `avisar` es una función opcional que recibe
    mensajes de progreso: arrancar Docker Desktop la primera vez del día
    puede tardar más de un minuto (levanta una máquina virtual de WSL2), y
    sin avisos parece que se colgó.

    Los caminos posibles:

    - ya está corriendo -> no hace nada (es el caso normal y es instantáneo);
    - no está instalado -> lo dice y abre la página de descarga;
    - instalado pero cerrado -> intenta abrirlo y espera al motor;
    - abriéndose -> no lo abre de nuevo, solo espera;
    - **no se pudo abrir solo** -> te pide que lo abras vos y SIGUE
      revisando; apenas el motor responde, continúa el encendido sin que
      tengas que apretar nada de nuevo. Ese último caso es el importante:
      abrir una app gráfica desde un proceso sin ventana y elevado (como
      corre el dashboard) no siempre funciona, y no tiene sentido tirar todo
      el encendido abajo por un paso que resolvés en dos segundos.
    """

    def paso(texto: str) -> None:
        if avisar is not None:
            avisar(texto)

    if engine_ready(timeout=10.0):
        return True, "Docker ya estaba corriendo"

    if platform.system() != "Windows":
        if not shutil.which("docker"):
            return False, (
                f"no encontré Docker instalado. Instalalo desde {URL_DESCARGA} "
                "y volvé a intentar"
            )
        return False, (
            "el motor de Docker no responde. Arrancalo con "
            "'sudo systemctl start docker' y volvé a intentar"
        )

    ejecutable = desktop_executable()

    # Caso "no está instalado": es distinto de "no arrancó", y la solución
    # también, así que se informa distinto.
    if ejecutable is None and not shutil.which("docker"):
        abierto = abrir_pagina_de_descarga()
        extra = (
            " Te abrí la página de descarga en el navegador."
            if abierto
            else f" Descargalo de {URL_DESCARGA}"
        )
        return False, (
            "Docker Desktop no está instalado y el laboratorio de la VPN lo "
            "necesita (el 'servidor' es un contenedor)." + extra +
            " Instalalo, reiniciá la PC si te lo pide, y volvé a apretar "
            "Encender VPN."
        )

    if ejecutable is None:
        # Rarísimo: el comando docker existe pero no aparece la aplicación.
        # Puede pasar con Docker en WSL sin Docker Desktop. No se puede
        # abrir nada, pero conviene explicar qué mirar.
        return False, (
            "encontré el comando 'docker' pero no la aplicación Docker "
            "Desktop para abrirla. Abrila a mano (o arrancá el motor de "
            "Docker en WSL) y volvé a intentar"
        )

    # Se INTENTA abrirlo, pero que no se pueda NO es motivo para cortar.
    #
    # Abrir una aplicación gráfica desde un proceso sin ventana y elevado -que
    # es como corre el dashboard- no siempre funciona, y depender de eso hacía
    # fracasar todo el encendido por un paso que el usuario puede resolver en
    # dos segundos. Así que se prueba, y pase lo que pase se entra igual a la
    # espera de abajo: si lo abre el programa, perfecto; si lo abrís vos a
    # mano, también. Lo único que importa es que el motor termine
    # respondiendo.
    lo_abrio = False
    if desktop_process_running():
        paso("Docker Desktop ya se está abriendo; espero a que el motor arranque...")
        lo_abrio = True
    else:
        paso("Docker Desktop está cerrado: intento abrirlo...")
        lo_abrio, detalle_apertura = _lanzar_docker_desktop(ejecutable, avisar=avisar)
        if lo_abrio:
            paso(f"Docker Desktop {detalle_apertura}; espero a que el motor arranque...")
        else:
            paso(f"No pude abrirlo yo ({detalle_apertura}).")
            paso(
                "ABRÍ DOCKER DESKTOP A MANO (menú Inicio -> Docker Desktop). "
                "Sigo revisando solo y, apenas esté listo, continúo con el "
                "encendido; no hace falta que aprietes nada más."
            )

    # Espera activa: acá se termina de resolver todo. Se pregunta por el motor
    # cada 3 segundos y se avisa cada 15, cambiando el mensaje según lo que
    # esté pasando -arrancando, o todavía cerrado esperándote-, para que en la
    # consola del panel se vea qué falta en vez de un silencio.
    limite = time.time() + espera_maxima
    ultimo_aviso = time.time()
    while time.time() < limite:
        if engine_ready(timeout=10.0):
            return True, (
                "Docker ya está listo"
                if lo_abrio
                else "Docker Desktop lo abriste vos y ya está listo: sigo"
            )
        if time.time() - ultimo_aviso >= 15:
            faltan = int(limite - time.time())
            if desktop_process_running():
                paso(
                    f"Docker está arrancando (levanta su máquina virtual); "
                    f"espero hasta {faltan}s más..."
                )
            else:
                paso(
                    f"Docker Desktop sigue cerrado. Abrilo desde el menú "
                    f"Inicio; sigo revisando hasta {faltan}s más y continúo "
                    f"solo apenas esté."
                )
            ultimo_aviso = time.time()
        time.sleep(3)

    if not desktop_process_running():
        return False, (
            f"Docker Desktop nunca se abrió (esperé {espera_maxima // 60} "
            f"minutos revisando). Abrilo desde el menú Inicio, esperá a que "
            f"el ícono de la ballena deje de moverse, y volvé a apretar "
            f"Encender VPN"
        )
    return False, (
        f"Docker Desktop está abierto pero su motor no terminó de arrancar en "
        f"{espera_maxima // 60} minutos. Esperá a que el ícono de la ballena "
        f"deje de moverse y volvé a apretar Encender VPN"
    )
