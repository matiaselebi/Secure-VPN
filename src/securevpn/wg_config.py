"""Renderizado de archivos de configuración WireGuard (.conf).

WireGuard se configura con archivos INI simples: una sección [Interface]
(este extremo del túnel) y una sección [Peer] por cada extremo remoto
autorizado. Este módulo genera esos archivos a partir del config.yaml y el
registro de peers - es la "automatización" del proyecto: el protocolo, el
cifrado y el manejo de paquetes los hace WireGuard.

Dato importante para leer estos archivos: AllowedIPs significa cosas
distintas según el lado. En el SERVIDOR, es "qué IPs internas puede usar
este peer" (control de acceso). En el CLIENTE, es "qué destinos se rutean
por el túnel" (0.0.0.0/0 = todo → túnel completo; una lista corta = split
tunneling).
"""

from .peers import server_ip_v4, server_ip_v6

FULL_TUNNEL_ALLOWED_IPS = "0.0.0.0/0, ::/0"


def render_server_config(
    server_private_key: str,
    listen_port: int,
    network_v4: str,
    network_v6: str,
    peers: list[dict],
) -> str:
    """El wg0.conf que vive en el servidor: su clave privada, sus IPs
    internas del túnel (la .1 de cada red), y un [Peer] por dispositivo
    autorizado. Cualquier paquete que no venga firmado por una de estas
    claves públicas se descarta en silencio (por eso WireGuard es invisible
    a los escaneos de puertos)."""
    prefix_v4 = network_v4.split("/")[1]
    addresses = [f"{server_ip_v4(network_v4)}/{prefix_v4}"]
    if network_v6:
        prefix_v6 = network_v6.split("/")[1]
        addresses.append(f"{server_ip_v6(network_v6)}/{prefix_v6}")
    lines = [
        "[Interface]",
        f"Address = {', '.join(addresses)}",
        f"ListenPort = {listen_port}",
        f"PrivateKey = {server_private_key}",
    ]
    for peer in peers:
        # /32 y /128: este peer puede usar EXACTAMENTE su IP asignada y
        # ninguna otra (si intentara usar la IP de otro peer, el servidor
        # descarta el paquete).
        peer_allowed = [f"{peer['ip_v4']}/32"]
        if network_v6 and peer.get("ip_v6"):
            peer_allowed.append(f"{peer['ip_v6']}/128")
        lines += [
            "",
            f"# peer: {peer['name']}",
            "[Peer]",
            f"PublicKey = {peer['public_key']}",
            f"PresharedKey = {peer['preshared_key']}",
            f"AllowedIPs = {', '.join(peer_allowed)}",
        ]
    return "\n".join(lines) + "\n"


def client_allowed_ips(mode: str, split_allowed_ips: list[str]) -> str:
    """Qué tráfico del cliente entra al túnel.

    - "full": todo IPv4 y todo IPv6. Incluir ::/0 no es decorativo: si solo
      se tunelizara IPv4, cualquier destino alcanzable por IPv6 saldría por
      fuera del túnel sin cifrar (el clásico "IPv6 leak" de VPNs mal hechas).
    - "split": solo las redes listadas en el config.
    """
    if mode == "full":
        return FULL_TUNNEL_ALLOWED_IPS
    if mode == "split":
        if not split_allowed_ips:
            raise ValueError("modo split sin split_allowed_ips: el túnel no rutearía nada")
        return ", ".join(split_allowed_ips)
    raise ValueError(f"tunnel.mode inválido: {mode!r} (esperaba 'full' o 'split')")


def render_client_config(
    peer: dict,
    server_public_key: str,
    endpoint_host: str,
    endpoint_port: int,
    mode: str,
    split_allowed_ips: list[str],
    dns_servers: list[str],
    keepalive_seconds: int,
) -> str:
    """El .conf que usa un dispositivo cliente (tu PC con la app oficial de
    WireGuard, o el celular escaneando el QR de este mismo archivo)."""
    addresses = [f"{peer['ip_v4']}/32"]
    if peer.get("ip_v6"):
        addresses.append(f"{peer['ip_v6']}/128")
    lines = [
        f"# SecureVPN - peer: {peer['name']}",
        "[Interface]",
        f"PrivateKey = {peer['private_key']}",
        f"Address = {', '.join(addresses)}",
    ]
    if dns_servers:
        lines.append(f"DNS = {', '.join(dns_servers)}")
    lines += [
        "",
        "[Peer]",
        f"PublicKey = {server_public_key}",
        f"PresharedKey = {peer['preshared_key']}",
        f"Endpoint = {endpoint_host}:{endpoint_port}",
        f"AllowedIPs = {client_allowed_ips(mode, split_allowed_ips)}",
        # Sin esto, un router hogareño con NAT "olvida" el mapeo del túnel
        # tras un rato de silencio y el servidor pierde cómo alcanzarnos.
        f"PersistentKeepalive = {keepalive_seconds}",
    ]
    return "\n".join(lines) + "\n"


def choose_dns_servers(
    use_securedns: bool,
    securedns_ip: str,
    fallback_dns: list[str],
    securedns_alive: bool,
) -> list[str]:
    """Con qué DNS se arma el .conf del cliente al momento de conectar.

    La regla acordada para la integración con SecureDNS: si está configurado
    Y respondiendo en este momento, el túnel lo usa (toda consulta pasa por
    tu filtro). Si no está corriendo, se usan los públicos de respaldo - la
    prioridad es que conectar la VPN jamás te deje sin resolución de nombres.
    """
    if use_securedns and securedns_alive:
        return [securedns_ip]
    return list(fallback_dns)
