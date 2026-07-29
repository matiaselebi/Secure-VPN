import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from securevpn.status import (  # noqa: E402
    humanize_bytes,
    humanize_handshake,
    parse_wg_dump,
)

# Salida real del formato `wg show <iface> dump`: primera línea la interfaz,
# después una línea por peer, todo separado por tabs.
SAMPLE_DUMP = (
    "PRIV\tPUBIFACE\t51820\toff\n"
    "PEERPUB\tPSK\t203.0.113.7:51820\t0.0.0.0/0,::/0\t1753500000\t123456\t7890123\t25\n"
)


def test_parse_dump_reads_interface_and_peer():
    status = parse_wg_dump(SAMPLE_DUMP)
    assert status["up"] is True
    assert status["listen_port"] == "51820"
    peer = status["peers"][0]
    assert peer["endpoint"] == "203.0.113.7:51820"
    assert peer["latest_handshake"] == 1753500000
    assert peer["rx_bytes"] == 123456
    assert peer["tx_bytes"] == 7890123


def test_parse_empty_dump_means_down():
    assert parse_wg_dump("") == {"up": False, "peers": []}


def test_humanize_bytes():
    assert humanize_bytes(0) == "0 B"
    assert humanize_bytes(999) == "999 B"
    assert humanize_bytes(2048) == "2.0 KB"
    assert humanize_bytes(5 * 1024 * 1024) == "5.0 MB"


def test_humanize_handshake_recent():
    assert humanize_handshake(1000, now=1012) == "hace 12 s"


def test_humanize_handshake_minutes():
    assert humanize_handshake(1000, now=1000 + 125) == "hace 2 min 5 s"


def test_humanize_handshake_never():
    assert "nunca" in humanize_handshake(0)
