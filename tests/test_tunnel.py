import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from securevpn.config_loader import load_config  # noqa: E402
from securevpn.logger_db import LoggerDB  # noqa: E402
from securevpn.tunnel import DEFAULT_PEER_NAME, TunnelManager  # noqa: E402


def make_manager(tmp_path, monkeypatch=None, **cfg_overrides):
    cfg = load_config(str(tmp_path / "no-config.yaml"))  # defaults
    for dotted, value in cfg_overrides.items():
        section, key = dotted.split(".")
        setattr(getattr(cfg, section), key, value)
    logger_db = LoggerDB(str(tmp_path / "logs.db"))
    return TunnelManager(cfg, logger_db, data_dir=str(tmp_path / "data"))


def test_initialization_is_idempotent(tmp_path):
    manager = make_manager(tmp_path)
    manager.ensure_initialized()
    server_public = manager.server_public_key()
    peer = manager.registry.get(DEFAULT_PEER_NAME)

    manager.ensure_initialized()  # segunda vez: no debe regenerar nada
    assert manager.server_public_key() == server_public
    assert manager.registry.get(DEFAULT_PEER_NAME)["private_key"] == peer["private_key"]


def test_main_peer_conf_is_named_after_the_interface(tmp_path):
    """La app de Windows le pone al túnel el nombre del archivo .conf: el
    peer principal tiene que llamarse securevpn.conf para que el kill switch
    (que permite la interfaz por nombre) matchee."""
    manager = make_manager(tmp_path)
    path = manager.write_client_conf(DEFAULT_PEER_NAME)
    assert path.name == "securevpn.conf"


def test_securedns_probe_false_when_nothing_listens(tmp_path):
    manager = make_manager(
        tmp_path, **{"dns.securedns_probe_url": "http://127.0.0.1:1/x"}
    )
    assert manager.securedns_is_alive() is False


def test_client_conf_uses_fallback_dns_when_securedns_down(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    monkeypatch.setattr(TunnelManager, "securedns_is_alive", lambda self: False)
    conf = manager.render_client_conf(DEFAULT_PEER_NAME)
    assert "DNS = 9.9.9.9, 1.1.1.1" in conf


def test_client_conf_uses_securedns_when_alive(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    monkeypatch.setattr(TunnelManager, "securedns_is_alive", lambda self: True)
    conf = manager.render_client_conf(DEFAULT_PEER_NAME)
    assert "DNS = 127.0.0.1" in conf


def test_extra_peers_never_get_loopback_dns(tmp_path, monkeypatch):
    """El celular no puede usar 127.0.0.1 como DNS: su loopback es OTRA
    máquina. Aunque SecureDNS esté vivo, los peers extra llevan los públicos."""
    manager = make_manager(tmp_path)
    manager.ensure_initialized()
    manager.registry.add_peer("celular")
    monkeypatch.setattr(TunnelManager, "securedns_is_alive", lambda self: True)
    conf = manager.render_client_conf("celular")
    assert "DNS = 9.9.9.9, 1.1.1.1" in conf


def test_connect_activates_killswitch_only_after_handshake(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    order = []

    monkeypatch.setattr(TunnelManager, "securedns_is_alive", lambda self: False)
    monkeypatch.setattr(
        TunnelManager, "_platform_up",
        lambda self, conf: (order.append("up"), (True, "ok"))[1],
    )
    monkeypatch.setattr(
        TunnelManager, "_wait_for_handshake",
        lambda self: (order.append("handshake"), True)[1],
    )
    monkeypatch.setattr(
        type(manager.killswitch), "activate",
        lambda self: (order.append("killswitch"), ["cmd"])[1],
    )

    result = manager.connect()
    assert result["ok"] is True
    assert order == ["up", "handshake", "killswitch"]


def test_connect_without_handshake_never_touches_killswitch(tmp_path, monkeypatch):
    """La promesa central: si el túnel no funciona (servidor apagado), el
    kill switch NO se activa y no te quedás sin internet."""
    manager = make_manager(tmp_path)
    touched = []

    monkeypatch.setattr(TunnelManager, "securedns_is_alive", lambda self: False)
    monkeypatch.setattr(TunnelManager, "_platform_up", lambda self, conf: (True, "ok"))
    monkeypatch.setattr(TunnelManager, "_wait_for_handshake", lambda self: False)
    monkeypatch.setattr(
        type(manager.killswitch), "activate", lambda self: touched.append("ks")
    )

    result = manager.connect()
    assert result["ok"] is False
    assert touched == []
    assert any("kill switch" in m.lower() for m in result["messages"])


def test_disconnect_cleans_killswitch_first(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    order = []

    monkeypatch.setattr(type(manager.killswitch), "is_active", lambda self: True)
    monkeypatch.setattr(
        type(manager.killswitch), "deactivate",
        lambda self: (order.append("killswitch_off"), [])[1],
    )
    monkeypatch.setattr(
        TunnelManager, "_platform_down",
        lambda self: (order.append("down"), (True, "ok"))[1],
    )

    result = manager.disconnect()
    assert result["ok"] is True
    assert order == ["killswitch_off", "down"]
