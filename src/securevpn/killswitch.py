"""Kill switch condicional: bloquear todo lo que intente salir fuera del túnel.

La idea de seguridad: si el túnel se cae en medio de una sesión (se reinicia
el servidor, se corta el UDP, etc.), el tráfico NO debe "desbordar" hacia la
conexión normal sin cifrar - mejor quedarse sin internet un momento que
filtrar tráfico en claro sin darte cuenta.

La condición acordada: el kill switch se activa SOLO cuando vos conectás la
VPN desde el panel, y se limpia al desconectar. Apagar el servidor con la
VPN desconectada jamás te deja sin internet. Y por las dudas, el panel tiene
"Restaurar internet", que revierte todo incondicionalmente.

Cómo funciona en Windows (sin instalar nada): se usa el propio Firewall de
Windows. En su modelo, las reglas de BLOQUEO le ganan a las de permiso, así
que el truco correcto no es "bloquear todo + permitir el túnel" (no
funcionaría), sino cambiar la ACCIÓN POR DEFECTO de salida a "bloquear" y
agregar reglas que permiten exactamente lo necesario:

- loopback (ahí viven SecureDNS, SecureProxy y los dashboards),
- la interfaz del túnel (todo lo que ya viaja cifrado),
- el paquete UDP hacia el endpoint del servidor (sin esto el túnel no
  podría ni siquiera hacer el handshake),
- la red local (impresora, otros dispositivos de tu casa),
- DHCP (para que el router pueda renovarte la IP).

Antes de tocar nada se guarda la política por defecto que tenía cada perfil
del firewall, para restaurar EXACTAMENTE lo que había y no lo que suponemos
que había. Todas las reglas llevan el prefijo `SecureVPN_`, así la limpieza
borra solo lo nuestro y nunca toca, por ejemplo, las reglas
`SecureProxy_Block_*` de SecureProxy.

En Linux (para desarrollo y laboratorio) se hace lo equivalente con una
tabla nftables propia.

Igual que el FirewallManager de SecureProxy: con dry_run=True solo se
muestran los comandos sin ejecutarlos.
"""

import json
import platform
import socket
import subprocess
from pathlib import Path

RULE_PREFIX = "SecureVPN_"
NFT_TABLE = "securevpn_killswitch"


class KillSwitch:
    def __init__(
        self,
        tunnel_name: str,
        endpoint_host: str,
        endpoint_port: int,
        state_path: str,
        dry_run: bool = False,
    ):
        self.tunnel_name = tunnel_name
        self.endpoint_host = endpoint_host
        self.endpoint_port = endpoint_port
        self.state_path = Path(state_path)
        self.dry_run = dry_run
        self.system = platform.system()

    # ---------- generación de comandos (testeable sin ejecutar nada) ----------

    def build_activate_commands(self, endpoint_ip: str) -> list[list[str]]:
        if self.system == "Windows":
            ps = [
                f"New-NetFirewallRule -DisplayName '{RULE_PREFIX}Allow_Loopback' "
                "-Direction Outbound -Action Allow -RemoteAddress @('127.0.0.0/8','::1')",
                f"New-NetFirewallRule -DisplayName '{RULE_PREFIX}Allow_Tunnel' "
                f"-Direction Outbound -Action Allow -InterfaceAlias '{self.tunnel_name}'",
                f"New-NetFirewallRule -DisplayName '{RULE_PREFIX}Allow_Endpoint' "
                f"-Direction Outbound -Action Allow -Protocol UDP "
                f"-RemotePort {self.endpoint_port} -RemoteAddress '{endpoint_ip}'",
                f"New-NetFirewallRule -DisplayName '{RULE_PREFIX}Allow_LAN' "
                "-Direction Outbound -Action Allow -RemoteAddress 'LocalSubnet'",
                f"New-NetFirewallRule -DisplayName '{RULE_PREFIX}Allow_DHCP' "
                "-Direction Outbound -Action Allow -Protocol UDP -RemotePort 67",
                # Recién al final, con todos los permisos ya en su lugar, se
                # cierra la puerta general:
                "Set-NetFirewallProfile -Profile Domain,Private,Public "
                "-DefaultOutboundAction Block",
            ]
            return [self._powershell(cmd) for cmd in ps]

        # Linux: tabla propia con política de salida "drop" + excepciones.
        nft_rules = f"""
table inet {NFT_TABLE} {{
  chain output {{
    type filter hook output priority 0; policy drop;
    oif lo accept
    oifname "{self.tunnel_name}" accept
    udp dport {self.endpoint_port} ip daddr {endpoint_ip} accept
    ip daddr {{ 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 }} accept
    ip6 daddr fe80::/10 accept
    udp dport 67 accept
    ct state established,related accept
  }}
}}
"""
        return [["nft", "-f", "-"], ["__stdin__", nft_rules]]

    def build_deactivate_commands(self, previous_defaults: dict | None) -> list[list[str]]:
        if self.system == "Windows":
            ps = []
            defaults = previous_defaults or {}
            for profile in ("Domain", "Private", "Public"):
                action = defaults.get(profile, "Allow")
                if action.lower().startswith("block"):
                    # Si el perfil YA estaba en Block antes de nosotros, se respeta.
                    action = "Block"
                else:
                    action = "Allow"
                ps.append(
                    f"Set-NetFirewallProfile -Profile {profile} -DefaultOutboundAction {action}"
                )
            ps.append(
                f"Get-NetFirewallRule -DisplayName '{RULE_PREFIX}*' | Remove-NetFirewallRule"
            )
            return [self._powershell(cmd) for cmd in ps]

        return [["nft", "delete", "table", "inet", NFT_TABLE]]

    @staticmethod
    def _powershell(command: str) -> list[str]:
        return ["powershell", "-NoProfile", "-Command", command]

    # ---------- ejecución ----------

    def activate(self) -> list[str]:
        """Activa el kill switch. Devuelve los comandos ejecutados (o que se
        habrían ejecutado, en dry_run) como strings para loguear/mostrar."""
        endpoint_ip = self._resolve_endpoint_ip()
        self._save_state(self._read_current_defaults())
        return self._run_all(self.build_activate_commands(endpoint_ip))

    def deactivate(self) -> list[str]:
        """Desactiva el kill switch restaurando la política que había antes,
        y borra únicamente las reglas con prefijo SecureVPN_."""
        state = self._load_state()
        executed = self._run_all(self.build_deactivate_commands(state))
        if not self.dry_run and self.state_path.exists():
            self.state_path.unlink()
        return executed

    def is_active(self) -> bool:
        """El kill switch se considera activo si dejamos estado guardado (se
        guarda al activar y se borra al desactivar con éxito)."""
        return self.state_path.exists()

    def _resolve_endpoint_ip(self) -> str:
        """La regla de firewall necesita una IP concreta; si el endpoint es
        un hostname, se resuelve acá (antes de cerrar nada, obvio)."""
        try:
            return socket.gethostbyname(self.endpoint_host)
        except OSError:
            return self.endpoint_host

    def _read_current_defaults(self) -> dict:
        if self.system != "Windows" or self.dry_run:
            return {}
        try:
            result = subprocess.run(
                self._powershell(
                    "(Get-NetFirewallProfile | ForEach-Object "
                    "{ $_.Name + '=' + $_.DefaultOutboundAction }) -join ';'"
                ),
                capture_output=True, text=True, timeout=30, check=True,
            )
            defaults = {}
            for part in result.stdout.strip().split(";"):
                if "=" in part:
                    name, action = part.split("=", 1)
                    defaults[name.strip()] = action.strip()
            return defaults
        except (subprocess.SubprocessError, OSError):
            return {}

    def _save_state(self, defaults: dict) -> None:
        if self.dry_run:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(defaults), encoding="utf-8")

    def _load_state(self) -> dict:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                return {}
        return {}

    def _run_all(self, commands: list[list[str]]) -> list[str]:
        executed = []
        stdin_data = None
        # Convención interna: un elemento ["__stdin__", data] alimenta por
        # stdin al comando anterior (lo usa la variante nftables).
        cleaned: list[tuple[list[str], str | None]] = []
        for cmd in commands:
            if cmd and cmd[0] == "__stdin__":
                cleaned[-1] = (cleaned[-1][0], cmd[1])
            else:
                cleaned.append((cmd, None))

        for cmd, stdin_data in cleaned:
            pretty = " ".join(cmd) + (" <<EOF ...(reglas)... EOF" if stdin_data else "")
            executed.append(pretty)
            if self.dry_run:
                continue
            try:
                subprocess.run(
                    cmd, input=stdin_data, capture_output=True, text=True, timeout=60,
                )
            except (subprocess.SubprocessError, OSError) as exc:
                executed.append(f"ERROR ejecutando '{pretty}': {exc}")
        return executed
