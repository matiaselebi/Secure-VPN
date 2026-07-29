"""Tests de la pestaña Configuracion del dashboard de SecureVPN.

Diferencia clave con el proxy y el DNS: aca NADA se aplica en caliente. Un
tunel ya levantado tiene su configuracion congelada en la interfaz de red,
asi que los cambios valen para la PROXIMA conexion. Los tests verifican que
se persistan, que se reflejen en la config que se genera al conectar, y que
la pagina lo avise en vez de prometer un efecto inmediato.
"""

import shutil
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

import securevpn.config_loader as cl  # noqa: E402
from securevpn.config_writer import read_value, set_value  # noqa: E402


@pytest.fixture
def config_temporal(tmp_path):
    destino = tmp_path / "config.yaml"
    shutil.copy(RAIZ / "config" / "config.yaml", destino)
    return destino


def test_escribir_no_borra_comentarios(config_temporal):
    original = config_temporal.read_text(encoding="utf-8")
    assert set_value(config_temporal, "tunnel", "mode", "split")
    nuevo = config_temporal.read_text(encoding="utf-8")
    assert [l for l in original.splitlines() if l.strip().startswith("#")] == \
           [l for l in nuevo.splitlines() if l.strip().startswith("#")]
    assert read_value(config_temporal, "tunnel", "mode") == "split"


def _dashboard(tmp_path, monkeypatch):
    from securevpn.config_loader import load_config
    from securevpn.dashboard import build_dashboard_server
    from securevpn.logger_db import LoggerDB
    from securevpn.tunnel import TunnelManager

    (tmp_path / "config").mkdir(exist_ok=True)
    archivo = tmp_path / "config" / "config.yaml"
    shutil.copy(RAIZ / "config" / "config.yaml", archivo)
    monkeypatch.setattr(cl, "PROJECT_ROOT", tmp_path)

    cfg = load_config(str(archivo))
    logger = LoggerDB(str(tmp_path / "l.db"))
    manager = TunnelManager(cfg, logger, data_dir=str(tmp_path / "data"))
    manager.ensure_initialized()
    server = build_dashboard_server("127.0.0.1", 0, manager, logger)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    return base, manager, server, archivo


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.read().decode("utf-8", "replace")


def test_cambiar_modo_de_tunel_afecta_la_config_generada(tmp_path, monkeypatch):
    """La prueba que importa: cambiar a split desde la web tiene que verse en
    el .conf que se genera para conectar."""
    base, manager, server, archivo = _dashboard(tmp_path, monkeypatch)
    try:
        assert "0.0.0.0/0" in manager.render_client_conf("pc")  # full por default

        _get(f"{base}/config?k=mode&v=split")

        assert manager.cfg.tunnel.mode == "split"
        assert read_value(archivo, "tunnel", "mode") == "split"
        assert "0.0.0.0/0" not in manager.render_client_conf("pc")
    finally:
        server.shutdown()


def test_apagar_killswitch_desde_el_panel(tmp_path, monkeypatch):
    base, manager, server, archivo = _dashboard(tmp_path, monkeypatch)
    try:
        _get(f"{base}/config?k=killswitch_enabled&v=0")
        assert manager.cfg.killswitch.enabled is False
        assert read_value(archivo, "killswitch", "enabled") is False
    finally:
        server.shutdown()


def test_desactivar_securedns_cambia_el_dns_del_tunel(tmp_path, monkeypatch):
    base, manager, server, archivo = _dashboard(tmp_path, monkeypatch)
    try:
        _get(f"{base}/config?k=use_securedns&v=0")
        assert manager.cfg.dns.use_securedns is False
        assert read_value(archivo, "dns", "use_securedns") is False
        # y el .conf pasa a usar los DNS publicos de respaldo
        assert "DNS = 9.9.9.9, 1.1.1.1" in manager.render_client_conf("pc")
    finally:
        server.shutdown()


def test_keepalive_se_guarda_y_se_usa(tmp_path, monkeypatch):
    base, manager, server, archivo = _dashboard(tmp_path, monkeypatch)
    try:
        _get(f"{base}/config?k=keepalive_seconds&v=15")
        assert read_value(archivo, "tunnel", "keepalive_seconds") == 15
        assert "PersistentKeepalive = 15" in manager.render_client_conf("pc")
    finally:
        server.shutdown()


def test_valores_invalidos_no_cambian_nada(tmp_path, monkeypatch):
    base, manager, server, _archivo = _dashboard(tmp_path, monkeypatch)
    try:
        _get(f"{base}/config?k=mode&v=inventado")
        assert manager.cfg.tunnel.mode == "full"
        _get(f"{base}/config?k=keepalive_seconds&v=99999")
        assert manager.cfg.tunnel.keepalive_seconds == 25
        _get(f"{base}/config?k=clave_no_listada&v=1")
        assert manager.cfg.tunnel.mode == "full"
    finally:
        server.shutdown()


def test_el_panel_avisa_que_aplica_en_la_proxima_conexion(tmp_path, monkeypatch):
    """Honestidad: nada de esto tiene efecto sobre un tunel ya levantado, y la
    pagina tiene que decirlo."""
    base, _manager, server, _archivo = _dashboard(tmp_path, monkeypatch)
    try:
        body = _get(f"{base}/")
        assert "Configuración" in body and "tab-config" in body
        assert "PRÓXIMA" in body
    finally:
        server.shutdown()
