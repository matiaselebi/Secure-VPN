import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from securevpn.config_loader import load_config  # noqa: E402


def test_defaults_when_no_file(tmp_path):
    cfg = load_config(str(tmp_path / "no-existe.yaml"))
    assert cfg.server.endpoint_port == 51820
    assert cfg.tunnel.mode == "full"
    assert cfg.dns.use_securedns is True
    assert cfg.killswitch.enabled is True
    assert cfg.dashboard.port == 8891


def test_yaml_overrides_only_what_it_mentions(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
server:
  endpoint_host: "203.0.113.7"
tunnel:
  mode: "split"
""",
        encoding="utf-8",
    )
    cfg = load_config(str(config_file))
    assert cfg.server.endpoint_host == "203.0.113.7"
    assert cfg.server.endpoint_port == 51820  # no mencionado: default
    assert cfg.tunnel.mode == "split"
    assert cfg.dashboard.port == 8891


def test_repo_config_yaml_is_valid():
    """El config.yaml que viaja en el repo tiene que cargar sin errores."""
    cfg = load_config()
    assert cfg.tunnel.interface_name == "securevpn"
    assert cfg.dns.securedns_probe_url.startswith("http://127.0.0.1:8890")
