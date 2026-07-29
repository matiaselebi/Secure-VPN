"""Tests de que levantar el laboratorio no rompa cuando YA está levantado.

El caso real que lo motivó: el usuario levantó el laboratorio desde el panel
de SecureVPN y después apretó "Encender VPN" en SecureCenter. Como lab_up
siempre corría `docker compose up --build`, Docker intentaba descargar de
nuevo la imagen base y fallaba (el registro no era alcanzable con el proxy
del sistema encendido), abortando todo el encendido... aunque el laboratorio
estaba perfectamente arriba.
"""

import socket
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import securevpn.ops as ops  # noqa: E402
from securevpn.config_loader import load_config  # noqa: E402
from securevpn.logger_db import LoggerDB  # noqa: E402


def _cfg_con_puerto(port: int):
    cfg = load_config()
    cfg.server.ssh_host = "127.0.0.1"
    cfg.server.ssh_port = port
    return cfg


def _reloj_que_avanza(monkeypatch):
    """La espera final de do_lab_up es un loop hasta timeout. En los tests no
    interesa esperar de verdad: se simula un reloj que salta al futuro y un
    sleep que no duerme, así el loop termina al instante."""
    estado = {"t": 0.0}

    def fake_time():
        estado["t"] += 30.0
        return estado["t"]

    monkeypatch.setattr(ops.time, "time", fake_time)
    monkeypatch.setattr(ops.time, "sleep", lambda s: None)


def _receta_sin_cambios(monkeypatch, cfg):
    """do_lab_up recrea el contenedor cuando la "receta" (compose +
    Dockerfile) cambió respecto de la última construcción. En los tests que
    miran el camino normal, se simula que la receta es la misma."""
    monkeypatch.setattr(ops, "_leer_stamp", lambda: ops.lab_fingerprint(cfg))
    monkeypatch.setattr(ops, "_escribir_stamp", lambda huella: None)


def _docker_prendido(monkeypatch):
    """El motor de Docker ya está corriendo: ensure_engine no hace nada."""
    monkeypatch.setattr(ops, "ensure_engine", lambda *a, **k: (True, "Docker ya estaba corriendo"))


def test_lab_up_no_toca_docker_si_ya_esta_arriba(monkeypatch):
    """Si el SSH del laboratorio responde, no se ejecuta NINGÚN comando de
    docker: ni build, ni pull, ni up."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    cfg = _cfg_con_puerto(port)
    _receta_sin_cambios(monkeypatch, cfg)
    _docker_prendido(monkeypatch)
    llamadas = []
    monkeypatch.setattr(ops.subprocess, "run", lambda *a, **k: llamadas.append(a))

    try:
        ok, detalle = ops.do_lab_up(cfg, LoggerDB(tempfile.mktemp()))
    finally:
        listener.close()

    assert ok
    assert "ya estaba levantado" in detalle
    assert llamadas == [], "no debería haber corrido docker"


def test_lab_up_intenta_sin_build_antes_de_construir(monkeypatch):
    """Con el laboratorio abajo, primero prueba `up -d` (usa la imagen ya
    construida, sin internet) y solo si falla recurre a `--build`."""
    cfg = _cfg_con_puerto(1)  # nadie escucha: el laboratorio está abajo
    comandos = []

    class Res:
        def __init__(self, rc):
            self.returncode = rc
            self.stdout = ""
            self.stderr = "boom"

    def fake_run(argv, **kwargs):
        comandos.append(argv)
        return Res(0)  # el primer intento (sin --build) funciona

    _receta_sin_cambios(monkeypatch, cfg)
    _docker_prendido(monkeypatch)
    monkeypatch.setattr(ops, "compose_command", lambda: ["docker", "compose"])
    monkeypatch.setattr(ops, "ensure_lab_ssh_key", lambda p: None)
    monkeypatch.setattr(ops.subprocess, "run", fake_run)
    _reloj_que_avanza(monkeypatch)

    ops.do_lab_up(cfg, LoggerDB(tempfile.mktemp()))

    assert len(comandos) == 1, "no debería haber intentado construir"
    assert "--build" not in comandos[0]


def test_lab_up_cae_a_build_si_el_primer_intento_falla(monkeypatch):
    cfg = _cfg_con_puerto(1)
    comandos = []

    class Res:
        def __init__(self, rc):
            self.returncode = rc
            self.stdout = ""
            self.stderr = "no such image"

    def fake_run(argv, **kwargs):
        comandos.append(argv)
        # el primero (sin build) falla, el segundo (con build) anda
        return Res(1) if len(comandos) == 1 else Res(0)

    _receta_sin_cambios(monkeypatch, cfg)
    _docker_prendido(monkeypatch)
    monkeypatch.setattr(ops, "compose_command", lambda: ["docker", "compose"])
    monkeypatch.setattr(ops, "ensure_lab_ssh_key", lambda p: None)
    monkeypatch.setattr(ops.subprocess, "run", fake_run)
    _reloj_que_avanza(monkeypatch)

    ops.do_lab_up(cfg, LoggerDB(tempfile.mktemp()))

    assert len(comandos) == 2
    assert "--build" in comandos[1]


def test_lab_up_informa_el_error_si_ambos_intentos_fallan(monkeypatch):
    cfg = _cfg_con_puerto(1)

    class Res:
        returncode = 1
        stdout = ""
        stderr = "failed to resolve source metadata for docker.io/library/ubuntu:24.04"

    _receta_sin_cambios(monkeypatch, cfg)
    _docker_prendido(monkeypatch)
    monkeypatch.setattr(ops, "compose_command", lambda: ["docker", "compose"])
    monkeypatch.setattr(ops, "ensure_lab_ssh_key", lambda p: None)
    monkeypatch.setattr(ops.subprocess, "run", lambda *a, **k: Res())

    ok, detalle = ops.do_lab_up(cfg, LoggerDB(tempfile.mktemp()))

    assert not ok
    assert "docker.io" in detalle


def test_lab_up_recrea_el_contenedor_si_cambio_la_receta(monkeypatch):
    """El bug real: el compose se arregló (DNS fijo) pero en la PC del usuario
    el contenedor viejo seguía existiendo, y `docker compose up -d` no lo
    recrea. Resultado: el arreglo no se aplicaba nunca. Ahora, si la huella
    del compose/Dockerfile no coincide con la de la última construcción, se
    baja el contenedor y se vuelve a crear con --build."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    cfg = _cfg_con_puerto(port)  # el laboratorio ESTÁ arriba

    monkeypatch.setattr(ops, "_leer_stamp", lambda: "huella-vieja")
    escritas = []
    monkeypatch.setattr(ops, "_escribir_stamp", lambda huella: escritas.append(huella))
    _docker_prendido(monkeypatch)

    comandos = []

    class Res:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kwargs):
        comandos.append(argv)
        return Res()

    monkeypatch.setattr(ops, "compose_command", lambda: ["docker", "compose"])
    monkeypatch.setattr(ops, "ensure_lab_ssh_key", lambda p: None)
    monkeypatch.setattr(ops.subprocess, "run", fake_run)

    try:
        ok, _detalle = ops.do_lab_up(cfg, LoggerDB(tempfile.mktemp()))
    finally:
        listener.close()

    assert ok
    assert "down" in comandos[0], "primero tiene que bajar el contenedor viejo"
    assert "--build" in comandos[1], "y volver a crearlo con la receta nueva"
    assert escritas == [ops.lab_fingerprint(cfg)], "y anotar la huella nueva"


def test_lab_up_intenta_prender_docker_desktop_si_esta_apagado(monkeypatch):
    """Si el motor de Docker no responde, do_lab_up no arranca a ciegas:
    pasa por ensure_engine (que abre Docker Desktop) y si eso falla corta
    con un mensaje entendible, no con un error de docker."""
    cfg = _cfg_con_puerto(1)
    monkeypatch.setattr(ops, "_leer_stamp", lambda: ops.lab_fingerprint(cfg))
    llamadas = []

    def fake_ensure():
        llamadas.append(True)
        return False, "no encontré Docker instalado. Instalá Docker Desktop desde ..."

    monkeypatch.setattr(ops, "ensure_engine", fake_ensure)
    monkeypatch.setattr(ops.subprocess, "run", lambda *a, **k: pytest.fail("no debería tocar docker"))

    ok, detalle = ops.do_lab_up(cfg, LoggerDB(tempfile.mktemp()))

    assert llamadas, "tiene que haber intentado prender Docker"
    assert not ok
    assert "Docker Desktop" in detalle


def test_el_error_de_docker_explica_el_proxy_del_sistema(monkeypatch):
    """Construir la imagen es lo unico que necesita internet, y ahi Docker
    hereda el proxy del sistema (SecureProxy) que desde su maquina virtual es
    inalcanzable. El error crudo de Docker no dice nada de eso; se le agrega
    la causa y el arreglo."""
    monkeypatch.setattr(ops, "system_proxy_enabled", lambda: True)
    crudo = 'failed to resolve source metadata for docker.io/library/ubuntu:24.04'

    con_pista = ops._con_pista_de_proxy(crudo)

    assert crudo in con_pista, "el error original no se pierde"
    assert "proxy del sistema" in con_pista
    assert "Docker Desktop" in con_pista


def test_no_culpa_al_proxy_cuando_esta_apagado(monkeypatch):
    """Si el proxy del sistema esta apagado, la pista seria una pista falsa."""
    monkeypatch.setattr(ops, "system_proxy_enabled", lambda: False)
    crudo = "failed to resolve source metadata"

    assert ops._con_pista_de_proxy(crudo) == crudo


def test_no_culpa_al_proxy_por_errores_que_no_son_de_red(monkeypatch):
    monkeypatch.setattr(ops, "system_proxy_enabled", lambda: True)
    crudo = "invalid compose file: services must be a mapping"

    assert ops._con_pista_de_proxy(crudo) == crudo
