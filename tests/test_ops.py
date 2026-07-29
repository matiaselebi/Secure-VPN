import shutil
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from securevpn.config_loader import load_config  # noqa: E402
from securevpn.ops import (  # noqa: E402
    ensure_lab_ssh_key,
    secureproxy_is_alive,
    server_ssh_reachable,
)


def test_secureproxy_probe_true_and_false(tmp_path):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    cfg = load_config(str(tmp_path / "no.yaml"))
    cfg.stack.secureproxy_host, cfg.stack.secureproxy_port = "127.0.0.1", port
    assert secureproxy_is_alive(cfg) is True
    listener.close()

    cfg.stack.secureproxy_port = 1  # nadie escucha ahí
    assert secureproxy_is_alive(cfg) is False


def test_ssh_reachable_true_with_local_listener(tmp_path):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    cfg = load_config(str(tmp_path / "no.yaml"))
    cfg.server.ssh_host, cfg.server.ssh_port = "127.0.0.1", port
    assert server_ssh_reachable(cfg) is True
    listener.close()


def test_ssh_reachable_false_when_nothing_listens(tmp_path):
    cfg = load_config(str(tmp_path / "no.yaml"))
    cfg.server.ssh_host, cfg.server.ssh_port = "127.0.0.1", 1  # nadie escucha ahí
    assert server_ssh_reachable(cfg) is False


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="requiere ssh-keygen")
def test_lab_ssh_key_generated_once(tmp_path):
    key = tmp_path / "lab_ssh_key"
    ensure_lab_ssh_key(key)
    assert key.exists()
    assert (tmp_path / "lab_ssh_key.pub").exists()

    fingerprint = key.read_bytes()
    ensure_lab_ssh_key(key)  # segunda llamada: no regenera
    assert key.read_bytes() == fingerprint
