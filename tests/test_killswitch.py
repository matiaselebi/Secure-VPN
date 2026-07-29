import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from securevpn.killswitch import RULE_PREFIX, KillSwitch  # noqa: E402


def make_killswitch(tmp_path, system="Windows", dry_run=True):
    ks = KillSwitch(
        tunnel_name="securevpn",
        endpoint_host="203.0.113.7",
        endpoint_port=51820,
        state_path=str(tmp_path / "killswitch_state.json"),
        dry_run=dry_run,
    )
    ks.system = system
    return ks


def flatten(commands):
    return " || ".join(" ".join(cmd) for cmd in commands)


def test_windows_activation_allows_before_blocking(tmp_path):
    """El orden importa: TODAS las reglas de permiso (loopback, túnel,
    endpoint, LAN, DHCP) tienen que crearse ANTES de cambiar la política por
    defecto a Block, o habría una ventana en la que se corta todo."""
    ks = make_killswitch(tmp_path)
    text = flatten(ks.build_activate_commands("203.0.113.7"))

    block_pos = text.index("DefaultOutboundAction Block")
    for rule in ("Allow_Loopback", "Allow_Tunnel", "Allow_Endpoint", "Allow_LAN", "Allow_DHCP"):
        assert text.index(rule) < block_pos


def test_windows_rules_all_use_securevpn_prefix(tmp_path):
    """Cada regla creada lleva el prefijo SecureVPN_, para que la limpieza
    borre solo lo nuestro y nunca toque las reglas de SecureProxy."""
    ks = make_killswitch(tmp_path)
    text = flatten(ks.build_activate_commands("203.0.113.7"))
    assert text.count("New-NetFirewallRule") == text.count(f"DisplayName '{RULE_PREFIX}")


def test_windows_allows_loopback_for_sibling_projects(tmp_path):
    """SecureDNS y SecureProxy viven en 127.0.0.1: el kill switch no puede
    romperlos jamás."""
    ks = make_killswitch(tmp_path)
    text = flatten(ks.build_activate_commands("203.0.113.7"))
    assert "127.0.0.0/8" in text


def test_windows_allows_the_wireguard_handshake(tmp_path):
    ks = make_killswitch(tmp_path)
    text = flatten(ks.build_activate_commands("203.0.113.7"))
    assert "RemotePort 51820" in text
    assert "203.0.113.7" in text


def test_windows_deactivation_restores_saved_policy(tmp_path):
    """Si un perfil YA estaba en Block antes de activar el kill switch (lo
    puso el usuario o su antivirus), desactivar tiene que respetarlo, no
    forzar Allow a ciegas."""
    ks = make_killswitch(tmp_path)
    text = flatten(
        ks.build_deactivate_commands({"Domain": "Allow", "Private": "Block", "Public": "Allow"})
    )
    assert "-Profile Private -DefaultOutboundAction Block" in text
    assert "-Profile Domain -DefaultOutboundAction Allow" in text
    assert "-Profile Public -DefaultOutboundAction Allow" in text


def test_windows_deactivation_removes_only_our_rules(tmp_path):
    ks = make_killswitch(tmp_path)
    text = flatten(ks.build_deactivate_commands(None))
    assert f"DisplayName '{RULE_PREFIX}*'" in text
    assert "Remove-NetFirewallRule" in text


def test_dry_run_activate_executes_nothing_but_reports(tmp_path):
    ks = make_killswitch(tmp_path, dry_run=True)
    executed = ks.activate()
    assert len(executed) >= 6
    assert all("ERROR" not in line for line in executed)
    # En dry_run no se guarda estado: el kill switch no queda "activo".
    assert not ks.is_active()


def test_linux_commands_use_own_nft_table(tmp_path):
    ks = make_killswitch(tmp_path, system="Linux")
    activate = ks.build_activate_commands("203.0.113.7")
    assert activate[0] == ["nft", "-f", "-"]
    rules = activate[1][1]
    assert "policy drop" in rules
    assert 'oifname "securevpn" accept' in rules
    assert "udp dport 51820 ip daddr 203.0.113.7 accept" in rules

    deactivate = ks.build_deactivate_commands(None)
    assert "delete" in deactivate[0]
