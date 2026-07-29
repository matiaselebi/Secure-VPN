"""Aprovisionamiento del servidor por SSH (infrastructure-as-code casero).

Un solo comando deja un Linux pelado convertido en servidor WireGuard:

1. instala wireguard-tools, nftables y fail2ban,
2. escribe el wg0.conf del servidor (generado por este proyecto),
3. activa el ruteo de paquetes (ip_forward) para IPv4 e IPv6,
4. carga un firewall nftables mínimo: TODO cerrado salvo SSH y el puerto
   UDP de WireGuard, más el NAT (masquerade) para que el tráfico de los
   peers salga a internet con la IP del servidor,
5. enciende fail2ban vigilando SSH (WireGuard no lo necesita: ni siquiera
   responde a paquetes sin clave válida, es invisible a escaneos; el SSH
   en cambio se lleva ataques de fuerza bruta constantes en cualquier IP
   pública),
6. levanta el túnel y lo deja arrancando solo en cada boot.

El MISMO script funciona contra un VPS real (Debian/Ubuntu con systemd) y
contra el contenedor del laboratorio (sin systemd: detecta y usa comandos
directos; sin módulo de kernel: instala wireguard-go). Por eso el
laboratorio demuestra lo mismo que haría en producción.

La ejecución es por el cliente `ssh` del sistema (Windows 10+ lo trae de
fábrica), sin dependencias de Python extra: el script viaja por stdin, así
que ni siquiera hace falta copiar archivos antes.
"""

import subprocess

PROVISION_OK_MARKER = "SECUREVPN_PROVISION_OK"


def render_provision_script(
    server_conf: str,
    interface_name: str,
    listen_port: int,
    public_interface: str,
    ssh_port: int,
) -> str:
    """Genera el script bash que se ejecuta en el servidor. Es idempotente:
    correrlo dos veces deja el mismo resultado (re-aprovisionar un servidor
    tras cambiar peers o puertos es simplemente volver a correrlo)."""
    return f"""#!/usr/bin/env bash
# 'set -eu' es portable a todo shell POSIX (bash, dash, el sh de un
# contenedor mínimo). 'pipefail' es un extra de bash: se activa SOLO si el
# shell que terminó ejecutando el script lo soporta, para no abortar con
# "invalid option name" en imágenes cuyo /bin/sh no es bash.
set -eu
( set -o pipefail ) 2>/dev/null && set -o pipefail || true
export DEBIAN_FRONTEND=noninteractive

PAQUETES="wireguard-tools nftables fail2ban iproute2"
# Timeouts cortos y sin reintentos internos de apt: si no hay red, se quiere
# fallar RÁPIDO y decirlo, no quedarse colgado varios minutos por servidor.
APT_OPTS="-o Acquire::http::Timeout=10 -o Acquire::https::Timeout=10 -o Acquire::Retries=0"

# Primero se MIRA qué falta, en vez de correr apt a ciegas. En el
# laboratorio la imagen ya trae los paquetes horneados, así que no falta
# nada y todo este bloque -el único que necesita internet- se saltea
# entero: aprovisionar deja de depender del DNS y del proxy de Windows.
# En un VPS real recién creado no hay nada instalado y se instala como
# siempre, que es lo que el proyecto quiere demostrar.
echo "[provision] revisando qué paquetes faltan..."
FALTAN=""
for paquete in $PAQUETES; do
    if ! dpkg -s "$paquete" >/dev/null 2>&1; then
        FALTAN="$FALTAN $paquete"
    fi
done

if [ -z "$FALTAN" ]; then
    echo "[provision] los paquetes ya estaban instalados; no hace falta descargar nada"
else
    echo "[provision] faltan:$FALTAN"
    # La red de un servidor/contenedor recién creado tarda un momento en
    # estar usable (DNS y NAT). Si el aprovisionamiento arranca de inmediato
    # -como pasa cuando un orquestador encadena "levantar laboratorio" y
    # "aprovisionar" sin pausa- el primer apt falla con "Unable to locate
    # package". Por eso se reintenta antes de darse por vencido.
    echo "[provision] verificando que el servidor tenga red..."
    HAY_RED=0
    for intento in 1 2 3; do
        if timeout 20 apt-get update -y -qq $APT_OPTS >/dev/null 2>&1; then
            HAY_RED=1
            break
        fi
        echo "[provision] sin acceso a los repositorios todavía; reintento $intento de 3..."
        sleep 3
    done

    if [ "$HAY_RED" != "1" ]; then
        echo "[provision] ERROR: el servidor no llega a los repositorios de paquetes."
        echo "[provision] Causa más común en el laboratorio: Docker se quedó sin DNS"
        echo "[provision] o sin salida a internet porque heredó la configuración de"
        echo "[provision] Windows con SecureDNS/SecureProxy encendidos."
        echo "[provision] Solución: bajá y volvé a levantar el laboratorio para que"
        echo "[provision] reconstruya la imagen ya con los paquetes adentro."
        exit 1
    fi

    INSTALADO=0
    for intento in 1 2 3; do
        if timeout 180 apt-get install -y -qq $APT_OPTS $FALTAN >/dev/null 2>&1; then
            INSTALADO=1
            break
        fi
        echo "[provision] falló la instalación; reintento $intento de 3..."
        timeout 20 apt-get update -y -qq $APT_OPTS >/dev/null 2>&1 || true
        sleep 3
    done

    if [ "$INSTALADO" != "1" ]; then
        echo "[provision] ERROR: no se pudieron instalar los paquetes ($FALTAN)."
        exit 1
    fi
fi

# ¿Este kernel puede crear interfaces wireguard? Si no (contenedores, como
# el laboratorio), se usa wireguard-go: la implementación oficial en
# espacio de usuario. En un VPS real moderno, el módulo del kernel está.
if ip link add wg-probe type wireguard 2>/dev/null; then
    ip link del wg-probe
    echo "[provision] wireguard por kernel: OK"
elif command -v wireguard-go >/dev/null 2>&1; then
    echo "[provision] sin módulo de kernel: uso wireguard-go (ya instalado)"
else
    echo "[provision] sin módulo de kernel: instalando wireguard-go (espacio de usuario)"
    apt-get install -y -qq $APT_OPTS wireguard-go >/dev/null 2>&1 || {{
        echo "[provision] ERROR: no pude instalar wireguard-go y este kernel no"
        echo "[provision] soporta wireguard. Sin alguno de los dos no hay túnel."
        exit 1
    }}
fi

echo "[provision] escribiendo configuración del túnel..."
mkdir -p /etc/wireguard
install -m 600 /dev/null /etc/wireguard/{interface_name}.conf
cat > /etc/wireguard/{interface_name}.conf <<'WGCONF'
{server_conf}WGCONF

echo "[provision] activando ruteo de paquetes (ip_forward)..."
cat > /etc/sysctl.d/99-securevpn.conf <<'SYSCTL'
net.ipv4.ip_forward=1
net.ipv6.conf.all.forwarding=1
SYSCTL
if pidof systemd >/dev/null 2>&1; then
    # VPS real: sin ip_forward los peers no salen a internet, así que si esto
    # falla el aprovisionamiento tiene que fallar.
    sysctl -q -p /etc/sysctl.d/99-securevpn.conf || sysctl -q -w net.ipv4.ip_forward=1
else
    # Contenedor del laboratorio: /proc/sys suele venir de solo lectura y el
    # ruteo ya lo hace Docker. Es best-effort, no motivo para abortar.
    sysctl -q -p /etc/sysctl.d/99-securevpn.conf 2>/dev/null || \
        sysctl -q -w net.ipv4.ip_forward=1 2>/dev/null || \
        echo "[provision] AVISO: no pude tocar ip_forward acá (normal en el laboratorio)"
fi

echo "[provision] cargando firewall nftables (todo cerrado salvo SSH y WireGuard)..."
# Se borran SOLO las tablas propias (jamás el ruleset completo): si el repo
# de integración instaló su tabla de redirección hacia SecureProxy (ver
# docs/contrato-redirect-secureproxy.md), re-aprovisionar no debe pisarla.
nft delete table inet securevpn 2>/dev/null || true
nft delete table inet securevpn_nat 2>/dev/null || true
cat > /etc/nftables.conf <<'NFT'
table inet securevpn {{
  chain input {{
    type filter hook input priority 0; policy drop;
    ct state established,related accept
    iif lo accept
    tcp dport {{ 22, {ssh_port} }} accept comment "SSH"
    udp dport {listen_port} accept comment "WireGuard"
    ip protocol icmp accept
    meta l4proto ipv6-icmp accept
  }}
  chain forward {{
    type filter hook forward priority 0; policy drop;
    # MSS clamping: los paquetes que van al túnel llevan cifrado extra, así
    # que el paquete "lleno" del cliente ya no entra. Sin esto, esos paquetes
    # grandes se descartan en silencio y algunas páginas/streaming cargan a
    # medias y se cuelgan (el clásico "MTU black hole" de las VPN). Esta regla
    # le baja el tamaño máximo (MSS) a cada conexión TCP al arrancar, así el
    # cliente manda paquetes que sí entran. Técnica estándar de servidor VPN.
    iifname "{interface_name}" tcp flags syn tcp option maxseg size set rt mtu
    iifname "{interface_name}" accept comment "peers hacia internet"
    ct state established,related accept
  }}
}}

table inet securevpn_nat {{
  chain postrouting {{
    type nat hook postrouting priority 100;
    oifname "{public_interface}" masquerade comment "los peers salen con la IP del servidor"
  }}
}}
NFT
if pidof systemd >/dev/null 2>&1; then
    # VPS real: el firewall es obligatorio; si falla, el aprovisionamiento falla.
    nft -f /etc/nftables.conf
else
    # Contenedor de laboratorio: algunos kernels anfitriones no exponen
    # nftables adentro del contenedor. Ahí el firewall es best-effort (el
    # laboratorio ya está aislado: solo escucha en 127.0.0.1 de tu PC).
    nft -f /etc/nftables.conf 2>/dev/null || \
        echo "[provision] AVISO: nftables no disponible en este contenedor; en un VPS real se aplica siempre."
fi

echo "[provision] configurando fail2ban (protege SSH de fuerza bruta)..."
cat > /etc/fail2ban/jail.local <<'F2B'
[sshd]
enabled = true
backend = systemd
maxretry = 5
findtime = 10m
bantime = 1h
F2B
# En un contenedor sin systemd, fail2ban lee logs de archivo:
if ! pidof systemd >/dev/null 2>&1; then
    sed -i 's/backend = systemd/backend = auto/' /etc/fail2ban/jail.local
    touch /var/log/auth.log
fi

# ¿Este servidor tiene IPv6? Docker Desktop en Windows crea los contenedores
# SIN IPv6 por defecto, y ahí `ip -6 address add` falla con
# "RTNETLINK answers: Operation not supported" y se cae todo el
# aprovisionamiento... por una parte del túnel que ni siquiera hace falta.
# Si no hay IPv6, se quitan las direcciones v6 de la configuración y el
# túnel queda solo IPv4, que anda perfecto. En un VPS real con IPv6 (lo
# normal hoy) no se toca nada y el doble stack queda como estaba.
if [ ! -e /proc/net/if_inet6 ]; then
    echo "[provision] este servidor no tiene IPv6: dejo el túnel solo con IPv4"
    # Saca los elementos con ':' (direcciones v6) de las listas separadas por
    # comas, tanto si van primeras como si van después.
    sed -i '/^\\(Address\\|AllowedIPs\\)/ s/,[[:space:]]*[^,]*:[^,]*//g' /etc/wireguard/{interface_name}.conf
    sed -i '/^\\(Address\\|AllowedIPs\\)/ s/=[[:space:]]*[^,]*:[^,]*,[[:space:]]*/= /' /etc/wireguard/{interface_name}.conf
fi

echo "[provision] levantando el túnel..."
if pidof systemd >/dev/null 2>&1; then
    systemctl enable --now nftables >/dev/null 2>&1 || true
    systemctl enable --now fail2ban >/dev/null 2>&1 || true
    systemctl enable wg-quick@{interface_name} >/dev/null 2>&1 || true
    systemctl restart wg-quick@{interface_name}
else
    # Contenedor del laboratorio: sin systemd, comandos directos.
    command -v wireguard-go >/dev/null && export WG_QUICK_USERSPACE_IMPLEMENTATION=wireguard-go
    fail2ban-client -x start >/dev/null 2>&1 || true
    wg-quick down {interface_name} >/dev/null 2>&1 || true
    wg-quick up {interface_name}
fi

wg show {interface_name} >/dev/null
echo "{PROVISION_OK_MARKER}"
"""


def build_ssh_command(
    ssh_host: str, ssh_port: int, ssh_user: str, ssh_key_path: str | None
) -> list[str]:
    command = ["ssh", "-p", str(ssh_port)]
    if ssh_host in ("127.0.0.1", "localhost", "::1"):
        # Laboratorio local: el "servidor" es un contenedor en TU propia PC,
        # que además cambia de huella cada vez que se reconstruye. Verificar
        # la huella acá no aporta seguridad (no hay red que pueda suplantar
        # a tu propio localhost) y rompería el flujo en cada rebuild.
        command += [
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
        ]
    else:
        # VPS real: la primera vez acepta y guarda la huella del servidor;
        # después, si cambiara (posible suplantación), se niega a conectar.
        command += ["-o", "StrictHostKeyChecking=accept-new"]
    command += ["-o", "ConnectTimeout=15"]
    if ssh_key_path:
        command += ["-i", ssh_key_path]
    command += [f"{ssh_user}@{ssh_host}", "bash -s"]
    return command


def provision_server(
    script: str,
    ssh_host: str,
    ssh_port: int,
    ssh_user: str,
    ssh_key_path: str | None = None,
    timeout_seconds: int = 600,
) -> tuple[bool, str]:
    """Ejecuta el script en el servidor, mandándolo por stdin del ssh.
    Devuelve (ok, salida completa).

    El script se envía como BYTES con finales de línea Unix forzados. Esto
    es clave en Windows: si se mandara en modo texto (text=True), Python
    traduciría cada '\\n' a '\\r\\n', y el bash del contenedor Linux se
    rompería con errores tipo "$'\\r': command not found". Normalizar +
    enviar bytes garantiza LF puro sin importar el SO ni cómo quedaron los
    finales de línea del archivo fuente al descomprimir."""
    command = build_ssh_command(ssh_host, ssh_port, ssh_user, ssh_key_path)
    script_lf = script.replace("\r\n", "\n").replace("\r", "\n")
    try:
        result = subprocess.run(
            command,
            input=script_lf.encode("utf-8"),
            capture_output=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError:
        return False, (
            "no encontré el comando 'ssh'. En Windows 10+ viene incluido "
            "(OpenSSH Client); activalo en Configuración → Aplicaciones → "
            "Características opcionales."
        )
    except subprocess.TimeoutExpired:
        return False, f"el aprovisionamiento superó los {timeout_seconds}s y se cortó"

    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    output = stdout + ("\n" + stderr if stderr else "")
    ok = result.returncode == 0 and PROVISION_OK_MARKER in stdout
    return ok, output.strip()
