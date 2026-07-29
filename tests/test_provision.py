import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from unittest import mock  # noqa: E402

from securevpn.provision import (  # noqa: E402
    PROVISION_OK_MARKER,
    build_ssh_command,
    provision_server,
    render_provision_script,
)

SERVER_CONF = "[Interface]\nPrivateKey = X\n"


def render():
    return render_provision_script(
        server_conf=SERVER_CONF,
        interface_name="securevpn",
        listen_port=51820,
        public_interface="eth0",
        ssh_port=2222,
    )


def test_script_header_is_portable_no_bare_pipefail():
    """Regresión: 'set -euo pipefail' abortaba con 'invalid option name' en
    contenedores cuyo shell no es bash. El header debe usar 'set -eu' y
    activar pipefail solo si el shell lo soporta."""
    script = render()
    assert "set -eu\n" in script
    assert "set -euo pipefail" not in script


def test_script_installs_the_stack():
    script = render()
    assert "wireguard-tools" in script
    assert "nftables" in script
    assert "fail2ban" in script


def test_script_embeds_server_conf():
    assert SERVER_CONF in render()


def test_script_enables_forwarding_v4_and_v6():
    script = render()
    assert "net.ipv4.ip_forward=1" in script
    assert "net.ipv6.conf.all.forwarding=1" in script


def test_firewall_is_default_deny_with_only_ssh_and_wireguard():
    script = render()
    assert "policy drop" in script
    assert "tcp dport { 22, 2222 } accept" in script
    assert "udp dport 51820 accept" in script
    assert "masquerade" in script


def test_firewall_never_flushes_foreign_tables():
    """Contrato con el repo de integración (docs/contrato-redirect-
    secureproxy.md): re-aprovisionar borra SOLO las tablas propias, jamás
    'flush ruleset' - la tabla de redirección hacia SecureProxy debe
    sobrevivir."""
    script = render()
    assert "flush ruleset" not in script
    assert "delete table inet securevpn" in script
    assert "delete table inet securevpn_nat" in script


def test_forward_chain_has_mss_clamping():
    """MSS clamping en la cadena forward: evita el 'MTU black hole' que hace
    que algunas páginas/streaming carguen a medias sobre la VPN."""
    script = render()
    assert "tcp option maxseg size set rt mtu" in script


def test_script_handles_containers_without_systemd_or_kernel_module():
    """El mismo script debe funcionar en un VPS real (systemd + módulo de
    kernel) y en el contenedor del laboratorio (ninguno de los dos)."""
    script = render()
    assert "pidof systemd" in script
    assert "wireguard-go" in script
    assert "WG_QUICK_USERSPACE_IMPLEMENTATION" in script


def test_script_ends_with_verification_and_marker():
    """El script no dice "OK" gratis: antes verifica que la interfaz exista
    (`wg show`), y el marcador final es lo que provision_server() busca para
    declarar éxito."""
    script = render()
    assert "wg show securevpn" in script
    assert script.rstrip().endswith(f'echo "{PROVISION_OK_MARKER}"')


def test_ssh_command_uses_key_port_and_safe_host_checking():
    command = build_ssh_command("203.0.113.7", 2222, "root", "/tmp/key")
    text = " ".join(command)
    assert "-p 2222" in text
    assert "-i /tmp/key" in text
    assert "StrictHostKeyChecking=accept-new" in text
    assert command[-2] == "root@203.0.113.7"
    assert command[-1] == "bash -s"


def test_script_sent_to_server_never_contains_carriage_returns():
    """Regresión del bug de CRLF: aunque el script llegue con finales de
    línea de Windows (archivo fuente descomprimido en Windows, o lo que
    sea), lo que viaja por SSH al contenedor debe ser LF puro - un solo
    '\\r' rompe el bash del servidor."""
    script_con_crlf = "#!/usr/bin/env bash\r\nset -eu\r\necho hola\r\n"

    captured = {}

    class FakeResult:
        returncode = 0
        stdout = PROVISION_OK_MARKER.encode()
        stderr = b""

    def fake_run(command, input, capture_output, timeout):
        captured["input"] = input
        return FakeResult()

    with mock.patch("securevpn.provision.subprocess.run", side_effect=fake_run):
        provision_server(script_con_crlf, "127.0.0.1", 2222, "root", None)

    assert isinstance(captured["input"], bytes)  # se manda como bytes, no texto
    assert b"\r" not in captured["input"]  # sin un solo carriage return


def test_ssh_command_without_key_omits_identity():
    command = build_ssh_command("203.0.113.7", 22, "root", None)
    assert "-i" not in command


def test_ssh_to_localhost_lab_skips_hostkey_checking():
    """Contra el laboratorio local (127.0.0.1) no se verifica la huella SSH:
    es tu propia máquina y el contenedor cambia de huella en cada rebuild."""
    command = " ".join(build_ssh_command("127.0.0.1", 2222, "root", None))
    assert "StrictHostKeyChecking=no" in command
    assert "UserKnownHostsFile=/dev/null" in command


def test_ssh_to_real_vps_verifies_hostkey():
    command = " ".join(build_ssh_command("203.0.113.7", 22, "root", None))
    assert "StrictHostKeyChecking=accept-new" in command
    assert "UserKnownHostsFile=/dev/null" not in command


def test_apt_reintenta_cuando_la_red_del_contenedor_no_esta_lista():
    """Un contenedor recien creado tarda un momento en tener DNS/NAT. Si el
    aprovisionamiento arranca de inmediato (orquestado, sin pausa humana), el
    primer apt falla. El script reintenta en vez de asumir que ya esta lista."""
    script = render()
    assert "for intento in 1 2 3" in script
    assert "reintento $intento de 3" in script


def test_apt_falla_rapido_en_vez_de_colgarse():
    """Sin red, apt puede quedarse minutos esperando timeouts por servidor.
    Con timeouts cortos y sin reintentos internos, falla rapido y avisa."""
    script = render()
    assert "Acquire::http::Timeout=10" in script
    assert "Acquire::Retries=0" in script
    assert "timeout 20 apt-get update" in script


def test_diagnostico_de_red_apunta_a_la_causa_real():
    script = render()
    assert "no llega a los repositorios" in script
    assert "DNS" in script            # la causa raiz mas comun
    assert "volvé a levantar el laboratorio" in script   # la solucion concreta
    assert "exit 1" in script         # y corta, no sigue con un servidor a medias


def test_no_usa_apt_si_los_paquetes_ya_estan():
    """El laboratorio trae los paquetes horneados en la imagen, asi que el
    aprovisionamiento no tiene que bajar nada: mira que falta y, si no falta
    nada, ni siquiera corre apt. Eso es lo que lo vuelve inmune al DNS y al
    proxy de Windows, que era la causa de que fallara el encendido."""
    script = render()
    assert "dpkg -s" in script                 # chequea paquete por paquete
    assert 'if [ -z "$FALTAN" ]; then' in script
    assert "no hace falta descargar nada" in script
    # y cuando SI falta algo, instala solo lo que falta (no la lista entera)
    assert "apt-get install -y -qq $APT_OPTS $FALTAN" in script


def test_wireguard_go_no_se_reinstala_si_ya_esta():
    script = render()
    assert "command -v wireguard-go" in script
    assert "ya instalado" in script


def test_cae_a_ipv4_solo_si_el_servidor_no_tiene_ipv6():
    """Docker Desktop en Windows crea los contenedores sin IPv6. Ahi
    'ip -6 address add' falla con "Operation not supported" y se caia TODO el
    aprovisionamiento por una parte del tunel que ni siquiera hace falta.
    Ahora se detecta y se sigue con IPv4 solo."""
    script = render()
    assert "/proc/net/if_inet6" in script      # la forma estandar de preguntar
    assert "solo con IPv4" in script
    # y lo que hace es sacar las direcciones v6 de la config, no abortar
    assert "sed -i" in script
    assert "Address" in script and "AllowedIPs" in script


def test_ip_forward_es_obligatorio_en_un_vps_y_best_effort_en_el_laboratorio():
    """En un VPS real, sin ip_forward los peers no salen a internet: si falla,
    tiene que fallar el aprovisionamiento. En el contenedor /proc/sys suele
    ser de solo lectura y el ruteo lo hace Docker: ahi es un aviso."""
    script = render()
    assert "net.ipv4.ip_forward=1" in script
    assert "no pude tocar ip_forward" in script
