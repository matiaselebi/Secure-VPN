"""Tests del encendido completo (do_start_all) y del arranque de Docker.

Lo que se quiere garantizar acá es el ORDEN y las ESPERAS. El bug que
motivó esto: encender la VPN desde el dashboard de SecureCenter encadenaba
lab_up + provision + connect sin ninguna pausa, y el aprovisionamiento
arrancaba antes de que el contenedor tuviera red o sshd aceptara sesiones.
Desde el .bat andaba, porque entre opción y opción pasa un rato.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import securevpn.docker_desktop as dd  # noqa: E402
import securevpn.ops as ops  # noqa: E402
from securevpn.logger_db import LoggerDB  # noqa: E402


class CfgFalso:
    """Lo mínimo que do_start_all le pide a la config."""

    class tunnel:  # noqa: N801
        interface_name = "securevpn"


def _reloj_que_avanza(salto: float = 5.0):
    """Reloj falso que salta al futuro, para que las esperas no duerman de
    verdad en los tests."""
    estado = {"t": 0.0}

    def ahora():
        estado["t"] += salto
        return estado["t"]

    return ahora


class ManagerFalso:
    def __init__(self, cfg=None):
        self.cfg = cfg or CfgFalso()
        self.conectado = False
        self.desconectado = False

    def connect(self):
        self.conectado = True
        return {"ok": True, "messages": ["túnel arriba", "kill switch activado"]}

    def disconnect(self):
        self.desconectado = True
        return {"ok": True, "messages": []}


def _logger():
    return LoggerDB(tempfile.mktemp())


def _encadenar(monkeypatch, *, motor=True, lab=True, ssh=True, provision=True):
    """Arma el escenario reemplazando cada etapa por una respuesta fija y
    anotando el orden real en que se llamaron."""
    orden = []

    def fake_engine(*args, **kwargs):
        orden.append("docker")
        return motor, "Docker ya estaba corriendo" if motor else "Docker no arrancó"

    def fake_lab(cfg, logger_db):
        orden.append("lab")
        return lab, "laboratorio listo" if lab else "docker compose falló"

    def fake_ssh(cfg, espera_maxima=60.0):
        orden.append("ssh")
        return ssh

    def fake_provision(manager, logger_db):
        orden.append("provision")
        return provision, "SECUREVPN_PROVISION_OK" if provision else "apt falló"

    monkeypatch.setattr(ops, "ensure_engine", fake_engine)
    monkeypatch.setattr(ops, "_limpiar_intento_anterior", lambda m: (True, "nada previo"))
    monkeypatch.setattr(ops, "do_lab_up", fake_lab)
    monkeypatch.setattr(ops, "wait_ssh_usable", fake_ssh)
    monkeypatch.setattr(ops, "do_provision", fake_provision)
    return orden


def test_encendido_completo_en_el_orden_correcto(monkeypatch):
    orden = _encadenar(monkeypatch)
    manager = ManagerFalso()

    ok, lineas = ops.do_start_all(manager, _logger())

    assert ok
    assert orden == ["docker", "lab", "ssh", "provision"]
    assert manager.conectado, "y al final conecta el túnel"
    assert any("Conexión" in linea for linea in lineas)


def test_si_docker_no_arranca_no_sigue(monkeypatch):
    """Nada de intentar levantar el laboratorio cuando el motor no está: el
    error tiene que ser el de Docker, no un mensaje críptico de compose."""
    orden = _encadenar(monkeypatch, motor=False)
    manager = ManagerFalso()

    ok, lineas = ops.do_start_all(manager, _logger())

    assert not ok
    assert orden == ["docker"]
    assert not manager.conectado
    assert "Docker" in lineas[-1]


def test_espera_a_que_el_ssh_acepte_sesiones_antes_de_aprovisionar(monkeypatch):
    """Si sshd todavía no acepta sesiones, corta antes de aprovisionar en vez
    de fallar a mitad de camino con un error raro."""
    orden = _encadenar(monkeypatch, ssh=False)
    manager = ManagerFalso()

    ok, lineas = ops.do_start_all(manager, _logger())

    assert not ok
    assert orden == ["docker", "lab", "ssh"], "no llegó a aprovisionar"
    assert "SSH" in lineas[-1]


def test_si_falla_el_aprovisionamiento_no_conecta(monkeypatch):
    orden = _encadenar(monkeypatch, provision=False)
    manager = ManagerFalso()

    ok, _lineas = ops.do_start_all(manager, _logger())

    assert not ok
    assert orden == ["docker", "lab", "ssh", "provision"]
    assert not manager.conectado, "conectar sin servidor aprovisionado no tiene sentido"


def test_el_progreso_queda_en_el_log_de_eventos(monkeypatch):
    """Cada etapa se registra: así el dashboard muestra el avance en vivo
    aunque el encendido lo haya disparado el .bat."""
    _encadenar(monkeypatch)
    logger_db = _logger()

    ops.do_start_all(ManagerFalso(), logger_db)

    eventos = logger_db.recent_events(50)
    tipos = [e["event_type"] if isinstance(e, dict) else e[1] for e in eventos]
    assert any("encendido_vpn" == t for t in tipos)


def test_avisar_recibe_el_progreso_en_vivo(monkeypatch):
    _encadenar(monkeypatch)
    vistas = []

    ops.do_start_all(ManagerFalso(), _logger(), avisar=vistas.append)

    assert vistas, "el .bat tiene que poder imprimir el avance mientras pasa"
    assert vistas[0].startswith("Docker:")


# ---------------- Docker Desktop ----------------


def test_si_el_motor_responde_no_abre_nada(monkeypatch):
    """Docker Desktop minimizado en la bandeja SIGUE corriendo: lo que se
    mide es si el motor contesta, no si la ventana está a la vista."""
    monkeypatch.setattr(dd, "engine_ready", lambda timeout=10.0: True)
    monkeypatch.setattr(dd, "desktop_executable", lambda: (_ for _ in ()).throw(AssertionError("no debería buscarlo")))

    ok, detalle = dd.ensure_engine()

    assert ok
    assert "ya estaba corriendo" in detalle


def test_sin_docker_instalado_lo_dice_con_el_link(monkeypatch):
    monkeypatch.setattr(dd, "engine_ready", lambda timeout=10.0: False)
    monkeypatch.setattr(dd.shutil, "which", lambda nombre: None)

    ok, detalle = dd.ensure_engine()

    assert not ok
    assert "docker.com" in detalle


# ---------------- arranque limpio ----------------


class _ManagerConEstado(ManagerFalso):
    def __init__(self, estado):
        super().__init__()
        self._estado = estado


def _con_estado_de_tunel(monkeypatch, estado):
    import securevpn.status as status
    monkeypatch.setattr(status, "get_status", lambda iface: estado)


def test_baja_un_tunel_a_medias_antes_de_empezar(monkeypatch):
    """El caso real: un intento anterior instalo el tunel pero nunca hubo
    handshake (el servidor no estaba aprovisionado todavia). Esa interfaz
    quedo instalada y sin servir para nada; si no se baja, el intento nuevo
    choca contra ella y el error que sale no tiene que ver con la causa."""
    _con_estado_de_tunel(monkeypatch, {"up": True, "peers": [{"latest_handshake": 0}]})
    manager = ManagerFalso()

    ok, detalle = ops._limpiar_intento_anterior(manager)

    assert not ok, "tenia que haber algo para limpiar"
    assert manager.desconectado, "el tunel a medias no se bajo"
    assert "a medias" in detalle


def test_no_toca_un_tunel_que_esta_funcionando(monkeypatch):
    """Si ya hay handshake, la VPN anda: bajarla seria romper algo que
    funciona solo porque apretaste el boton dos veces."""
    _con_estado_de_tunel(
        monkeypatch, {"up": True, "peers": [{"latest_handshake": 1785252908}]}
    )
    manager = ManagerFalso()

    ok, _detalle = ops._limpiar_intento_anterior(manager)

    assert ok
    assert not manager.desconectado


def test_sin_tunel_previo_no_hace_nada(monkeypatch):
    _con_estado_de_tunel(monkeypatch, {"up": False, "peers": []})
    manager = ManagerFalso()

    ok, _detalle = ops._limpiar_intento_anterior(manager)

    assert ok
    assert not manager.desconectado


def test_un_error_revisando_el_estado_no_corta_el_encendido(monkeypatch):
    """La limpieza es una ayuda, no un requisito: si falla, el encendido
    sigue igual en vez de morir por el paso opcional."""
    import securevpn.status as status
    monkeypatch.setattr(
        status, "get_status", lambda iface: (_ for _ in ()).throw(OSError("boom"))
    )

    ok, detalle = ops._limpiar_intento_anterior(ManagerFalso())

    assert ok
    assert "sigo igual" in detalle


# ---------------- encontrar Docker Desktop ----------------


def test_encuentra_docker_desktop_a_partir_del_docker_del_path(monkeypatch, tmp_path):
    """El fallo real reportado: 'el comando docker existe pero no encontre
    Docker Desktop'. Pasaba porque se probaba una lista fija de carpetas.
    Ahora se deduce del propio docker.exe, que ya sabemos donde esta: el
    ejecutable de la aplicacion vive unas carpetas mas arriba, este instalado
    donde este (otro disco, otra carpeta, solo para un usuario)."""
    instalacion = tmp_path / "OtroDisco" / "Docker" / "Docker"
    (instalacion / "resources" / "bin").mkdir(parents=True)
    cli = instalacion / "resources" / "bin" / "docker.exe"
    cli.write_text("")
    app = instalacion / dd.NOMBRE_EJECUTABLE
    app.write_text("")

    monkeypatch.setattr(dd.platform, "system", lambda: "Windows")
    monkeypatch.setattr(dd.shutil, "which", lambda nombre: str(cli))

    assert dd.desktop_executable() == app


def test_si_no_esta_en_el_path_lo_busca_en_el_registro(monkeypatch, tmp_path):
    instalacion = tmp_path / "Docker" / "Docker"
    instalacion.mkdir(parents=True)
    app = instalacion / dd.NOMBRE_EJECUTABLE
    app.write_text("")

    monkeypatch.setattr(dd.platform, "system", lambda: "Windows")
    monkeypatch.setattr(dd.shutil, "which", lambda nombre: None)

    class _Reg:
        returncode = 0
        stdout = f"    AppPath    REG_SZ    {instalacion}\n"

    monkeypatch.setattr(dd, "_run", lambda argv, timeout=20.0: _Reg())

    assert dd.desktop_executable() == app


def test_si_docker_no_esta_instalado_avisa_y_abre_la_descarga(monkeypatch):
    """Lo que pidio el usuario: si no esta, que lo diga claro y lo deje a un
    clic de resolverlo, en vez de tirar un error tecnico."""
    monkeypatch.setattr(dd.platform, "system", lambda: "Windows")
    monkeypatch.setattr(dd, "engine_ready", lambda timeout=10.0: False)
    monkeypatch.setattr(dd.shutil, "which", lambda nombre: None)
    monkeypatch.setattr(dd, "desktop_executable", lambda: None)
    abiertas = []
    monkeypatch.setattr(dd, "abrir_pagina_de_descarga", lambda: abiertas.append(True) or True)

    ok, detalle = dd.ensure_engine()

    assert not ok
    assert abiertas, "tenia que abrir la pagina de descarga"
    assert "no está instalado" in detalle
    assert "Encender VPN" in detalle, "tiene que decir que hacer despues de instalarlo"


def test_si_ya_se_esta_abriendo_no_lanza_otra_instancia(monkeypatch, tmp_path):
    app = tmp_path / dd.NOMBRE_EJECUTABLE
    app.write_text("")
    monkeypatch.setattr(dd.platform, "system", lambda: "Windows")
    monkeypatch.setattr(dd, "desktop_executable", lambda: app)
    monkeypatch.setattr(dd, "desktop_process_running", lambda: True)
    monkeypatch.setattr(dd.shutil, "which", lambda nombre: "docker")
    monkeypatch.setattr(dd.time, "sleep", lambda s: None)

    llamadas = []
    monkeypatch.setattr(dd.subprocess, "Popen", lambda *a, **k: llamadas.append(a))

    listo = {"n": 0}
    def engine(timeout=10.0):
        listo["n"] += 1
        return listo["n"] > 2  # arranca al tercer chequeo
    monkeypatch.setattr(dd, "engine_ready", engine)

    ok, _detalle = dd.ensure_engine(espera_maxima=60)

    assert ok
    assert not llamadas, "ya se estaba abriendo: no habia que abrirlo de nuevo"


def test_avisa_mientras_espera_a_que_docker_arranque(monkeypatch, tmp_path):
    """Arrancar Docker la primera vez del dia tarda mas de un minuto porque
    levanta una maquina virtual. Sin avisos parece que se colgo."""
    app = tmp_path / dd.NOMBRE_EJECUTABLE
    app.write_text("")
    monkeypatch.setattr(dd.platform, "system", lambda: "Windows")
    monkeypatch.setattr(dd, "desktop_executable", lambda: app)
    monkeypatch.setattr(dd, "desktop_process_running", lambda: False)
    monkeypatch.setattr(dd.shutil, "which", lambda nombre: "docker")
    monkeypatch.setattr(dd, "engine_ready", lambda timeout=10.0: False)
    monkeypatch.setattr(dd.time, "sleep", lambda s: None)
    # La apertura en si tiene su propio test; acá interesa el aviso.
    monkeypatch.setattr(
        dd, "_lanzar_docker_desktop", lambda e, avisar=None: (True, "abierto")
    )

    avisos = []
    dd.ensure_engine(espera_maxima=1, avisar=avisos.append)

    assert any("cerrado" in a for a in avisos)


def test_abre_docker_por_explorer_para_que_ande_desde_el_dashboard(monkeypatch, tmp_path):
    """El caso reportado: desde el .bat abria Docker Desktop y desde el
    dashboard no. El dashboard corre sin ventana y elevado (lo arranca
    SecureCenter, que se auto-eleva), y una app grafica lanzada asi no
    siempre arranca. Pedirselo al Explorador de Windows la abre como si le
    hubieras hecho doble clic, con el usuario normal."""
    app = tmp_path / dd.NOMBRE_EJECUTABLE
    app.write_text("")
    lanzados = []
    monkeypatch.setattr(dd.subprocess, "Popen", lambda argv, **k: lanzados.append(argv))
    monkeypatch.setattr(dd, "desktop_process_running", lambda: True)  # aparecio
    monkeypatch.setattr(dd.time, "sleep", lambda s: None)

    ok, detalle = dd._lanzar_docker_desktop(app)

    assert ok
    assert "Explorador" in detalle
    assert lanzados[0][0] == "explorer.exe"
    assert str(app) in lanzados[0]


def test_si_explorer_no_lo_abre_prueba_otras_formas(monkeypatch, tmp_path):
    """Ninguna forma sola es confiable en todos los contextos, asi que se
    prueban en orden y se verifica que el proceso HAYA aparecido: lanzar algo
    sin error no significa que arranco."""
    app = tmp_path / dd.NOMBRE_EJECUTABLE
    app.write_text("")
    lanzados = []
    monkeypatch.setattr(dd.subprocess, "Popen", lambda argv, **k: lanzados.append(argv))
    monkeypatch.setattr(dd, "desktop_process_running", lambda: False)  # nunca aparece
    monkeypatch.setattr(dd, "engine_ready", lambda timeout=5.0: False)
    monkeypatch.setattr(dd.time, "sleep", lambda s: None)
    monkeypatch.setattr(dd.time, "time", _reloj_que_avanza())

    class _Shell32:
        @staticmethod
        def ShellExecuteW(*a):
            return 42  # "se lanzo bien" pero igual no aparece

    monkeypatch.setattr(dd, "ctypes", type("C", (), {"windll": type("W", (), {"shell32": _Shell32})}))

    ok, detalle = dd._lanzar_docker_desktop(app)

    assert not ok
    assert "no pude abrir Docker Desktop" in detalle
    assert any(argv[0] == "explorer.exe" for argv in lanzados), "falto probar Explorer"
    assert any(argv[0] == str(app) for argv in lanzados), "falto probar lanzarlo directo"


def _no_puede_abrirlo(monkeypatch, app):
    monkeypatch.setattr(dd.platform, "system", lambda: "Windows")
    monkeypatch.setattr(dd.shutil, "which", lambda nombre: "docker")
    monkeypatch.setattr(dd, "desktop_executable", lambda: app)
    monkeypatch.setattr(dd, "desktop_process_running", lambda: False)
    monkeypatch.setattr(dd.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        dd, "_lanzar_docker_desktop",
        lambda e, avisar=None: (False, "no pude abrir Docker Desktop (probé todo)"),
    )


def test_si_no_puede_abrirlo_te_pide_que_lo_abras_vos(monkeypatch, tmp_path):
    """Lo que se pidio: si no lo puede abrir desde el dashboard, que avise que
    lo abras y siga chequeando, en vez de cortar el encendido entero por un
    paso que se resuelve en dos segundos."""
    app = tmp_path / dd.NOMBRE_EJECUTABLE
    app.write_text("")
    _no_puede_abrirlo(monkeypatch, app)
    monkeypatch.setattr(dd, "engine_ready", lambda timeout=10.0: False)

    avisos = []
    ok, detalle = dd.ensure_engine(espera_maxima=1, avisar=avisos.append)

    assert not ok
    junto = " ".join(avisos)
    assert "ABRÍ DOCKER DESKTOP A MANO" in junto
    assert "no hace falta que aprietes nada más" in junto
    assert "Abrilo desde el menú Inicio" in detalle


def test_sigue_solo_apenas_lo_abris_a_mano(monkeypatch, tmp_path):
    """Y el otro lado de lo mismo: cuando el motor aparece -porque lo abriste
    vos- el encendido continua sin que haya que apretar nada de nuevo."""
    app = tmp_path / dd.NOMBRE_EJECUTABLE
    app.write_text("")
    _no_puede_abrirlo(monkeypatch, app)

    chequeos = {"n": 0}

    def engine(timeout=10.0):
        chequeos["n"] += 1
        return chequeos["n"] > 3  # al cuarto chequeo el usuario ya lo abrio

    monkeypatch.setattr(dd, "engine_ready", engine)

    ok, detalle = dd.ensure_engine(espera_maxima=120)

    assert ok
    assert "lo abriste vos" in detalle


def test_detecta_docker_sin_depender_del_filtro_de_tasklist(monkeypatch):
    """El filtro de tasklist con un nombre que lleva espacio no es confiable
    en todos los Windows y daba falsos negativos: decia que Docker no estaba
    cuando si estaba, y por eso los intentos de apertura se daban por
    fallidos aunque hubieran funcionado."""
    monkeypatch.setattr(dd.platform, "system", lambda: "Windows")

    class _Salida:
        returncode = 0
        stdout = (
            '"explorer.exe","1234","Console","1","50.000 KB"\n'
            '"Docker Desktop.exe","5678","Console","1","120.000 KB"\n'
        )

    usados = []

    def fake_run(argv, timeout=20.0):
        usados.append(argv)
        return _Salida()

    monkeypatch.setattr(dd, "_run", fake_run)

    assert dd.desktop_process_running() is True
    assert "/FI" not in usados[0], "no tiene que depender del filtro"


def test_reconoce_docker_arrancando_por_sus_procesos_de_fondo(monkeypatch):
    """A veces la ventana todavia no esta pero el backend ya arranco."""
    monkeypatch.setattr(dd.platform, "system", lambda: "Windows")

    class _Salida:
        returncode = 0
        stdout = '"com.docker.backend.exe","999","Console","1","80.000 KB"\n'

    monkeypatch.setattr(dd, "_run", lambda argv, timeout=20.0: _Salida())

    assert dd.desktop_process_running() is True


def test_no_confunde_otros_procesos_con_docker(monkeypatch):
    monkeypatch.setattr(dd.platform, "system", lambda: "Windows")

    class _Salida:
        returncode = 0
        stdout = '"chrome.exe","1","Console","1","1 KB"\n"python.exe","2","Console","1","1 KB"\n'

    monkeypatch.setattr(dd, "_run", lambda argv, timeout=20.0: _Salida())

    assert dd.desktop_process_running() is False
