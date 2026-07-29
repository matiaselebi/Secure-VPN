import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from securevpn.config_loader import load_config  # noqa: E402
from securevpn.dashboard import build_dashboard_server  # noqa: E402
from securevpn.logger_db import LoggerDB  # noqa: E402
from securevpn.tunnel import DEFAULT_PEER_NAME, TunnelManager  # noqa: E402


@pytest.fixture
def dashboard(tmp_path):
    cfg = load_config(str(tmp_path / "no-config.yaml"))
    logger_db = LoggerDB(str(tmp_path / "logs.db"))
    manager = TunnelManager(cfg, logger_db, data_dir=str(tmp_path / "data"))
    manager.ensure_initialized()
    server = build_dashboard_server("127.0.0.1", 0, manager, logger_db)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    yield f"http://127.0.0.1:{port}", manager, logger_db
    server.shutdown()


def get(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def test_dashboard_shows_status_and_peers(dashboard):
    base_url, manager, _ = dashboard
    status, body = get(base_url + "/")
    assert status == 200
    assert "SecureVPN" in body
    assert "DESCONECTADO" in body  # sin túnel real en los tests
    assert DEFAULT_PEER_NAME in body
    assert "Kill switch" in body


def test_health_endpoint_for_the_bat_menu(dashboard):
    base_url, _, _ = dashboard
    status, body = get(base_url + "/health")
    assert status == 200
    assert body == "ok"


def test_qr_returns_svg_with_the_peer_conf(dashboard):
    base_url, manager, _ = dashboard
    status, body = get(base_url + f"/qr?peer={DEFAULT_PEER_NAME}")
    assert status == 200
    assert "<svg" in body


def test_qr_for_unknown_peer_is_404(dashboard):
    base_url, _, _ = dashboard
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        get(base_url + "/qr?peer=fantasma")
    assert excinfo.value.code == 404


def test_events_appear_in_dashboard(dashboard):
    base_url, _, logger_db = dashboard
    logger_db.log_event("connect", "prueba-visible-en-dashboard")
    _, body = get(base_url + "/")
    assert "prueba-visible-en-dashboard" in body


def test_connect_action_calls_manager_and_redirects(dashboard, monkeypatch):
    base_url, manager, _ = dashboard
    called = []
    monkeypatch.setattr(
        type(manager), "connect", lambda self: called.append("connect") or {"ok": True, "messages": []}
    )
    status, _ = get(base_url + "/connect")  # urllib sigue el redirect al dashboard
    assert status == 200
    assert called == ["connect"]


def test_add_and_remove_peer_from_dashboard(dashboard):
    base_url, manager, _ = dashboard
    get(base_url + "/addpeer?name=celular")
    assert manager.registry.get("celular") is not None

    _, body = get(base_url + "/")
    assert "celular" in body

    get(base_url + "/removepeer?name=celular")
    assert manager.registry.get("celular") is None


def test_main_pc_peer_cannot_be_removed(dashboard):
    """El peer 'pc' es la propia PC: sin él no habría VPN que conectar."""
    base_url, manager, _ = dashboard
    get(base_url + "/removepeer?name=pc")
    assert manager.registry.get("pc") is not None


def test_provision_runs_in_background_and_redirects_fast(dashboard, monkeypatch):
    import threading as threading_mod

    import securevpn.dashboard as dashboard_mod

    base_url, _, _ = dashboard
    done = threading_mod.Event()

    def fake_provision(manager, logger_db):
        done.set()
        return True, "ok"

    monkeypatch.setattr(dashboard_mod, "do_provision", fake_provision)
    status, _ = get(base_url + "/provision")  # vuelve al dashboard al instante
    assert status == 200
    assert done.wait(timeout=5)  # y la operación corrió en segundo plano


def test_only_one_background_operation_at_a_time(dashboard, monkeypatch):
    import threading as threading_mod

    import securevpn.dashboard as dashboard_mod

    base_url, _, logger_db = dashboard
    release = threading_mod.Event()
    started = threading_mod.Event()

    def slow_lab_up(cfg, logger):
        started.set()
        release.wait(timeout=10)
        return True, "ok"

    monkeypatch.setattr(dashboard_mod, "do_lab_up", slow_lab_up)
    get(base_url + "/lab-up")
    assert started.wait(timeout=5)

    get(base_url + "/lab-up")  # segunda mientras la primera sigue corriendo
    release.set()

    import time

    time.sleep(0.3)
    events = " | ".join(str(e) for e in logger_db.recent_events(limit=10))
    assert "ya hay una operación en curso" in events


def test_server_tab_is_present(dashboard):
    base_url, _, _ = dashboard
    _, body = get(base_url + "/")
    assert "Aprovisionar servidor" in body
    assert "Levantar laboratorio" in body
    assert "Bajar laboratorio" in body


def test_dashboard_shows_sibling_stack_status(dashboard):
    """La sincronía del stack, visible: el dashboard de la VPN muestra si
    SecureDNS y SecureProxy están prendidos (en los tests no lo están)."""
    base_url, _, _ = dashboard
    _, body = get(base_url + "/")
    assert "SecureDNS (filtro de nombres)" in body
    assert "SecureProxy (filtro de conexiones)" in body
