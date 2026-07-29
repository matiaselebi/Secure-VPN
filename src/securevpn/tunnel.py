"""Orquestación del túnel: inicializar, conectar, desconectar, restaurar.

Acá se une todo: claves + registro de peers + render de configs + kill
switch + el binario oficial de WireGuard de cada plataforma:

- Windows: la app oficial (wireguard.exe) sabe instalar un túnel como
  servicio del sistema con `/installtunnelservice archivo.conf`. Eso usa
  el panel .bat y el dashboard por debajo.
- Linux: `wg-quick up/down` (lo trae wireguard-tools). Si el kernel no
  tiene el módulo wireguard (contenedores, este es el caso del CI), se usa
  automáticamente wireguard-go, la implementación oficial en espacio de
  usuario.

Orden de conexión pensado para no dejarte nunca sin internet:
1. se levanta el túnel,
2. se espera el PRIMER HANDSHAKE confirmado (o sea: el túnel funciona),
3. recién entonces se activa el kill switch.
Si el handshake no llega (servidor apagado, puerto bloqueado), el kill
switch NI SE TOCA y se informa el problema.
"""

import os
import platform
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config_loader import Config
from .keys import derive_public_key, generate_private_key
from .killswitch import KillSwitch
from .logger_db import LoggerDB
from .peers import PeerRegistry
from .status import WINDOWS_WIREGUARD_DIR, get_status
from .wg_config import choose_dns_servers, render_client_config, render_server_config

DEFAULT_PEER_NAME = "pc"
HANDSHAKE_WAIT_SECONDS = 20


def find_wireguard_exe() -> str | None:
    """El ejecutable de la app oficial de WireGuard para Windows."""
    candidate = WINDOWS_WIREGUARD_DIR / "wireguard.exe"
    if candidate.exists():
        return str(candidate)
    return shutil.which("wireguard")


class TunnelManager:
    def __init__(self, cfg: Config, logger_db: LoggerDB, data_dir: str | None = None):
        self.cfg = cfg
        self.logger_db = logger_db
        # data_dir se puede inyectar en los tests para no tocar el data/ real.
        self.data_dir = Path(data_dir) if data_dir else cfg.resolve_path("data")
        self.registry = PeerRegistry(
            str(self.data_dir / "peers.json"),
            cfg.tunnel.network_v4,
            cfg.tunnel.network_v6,
        )
        self.killswitch = KillSwitch(
            tunnel_name=cfg.tunnel.interface_name,
            endpoint_host=cfg.server.endpoint_host,
            endpoint_port=cfg.server.endpoint_port,
            state_path=str(self.data_dir / "killswitch_state.json"),
            dry_run=cfg.killswitch.dry_run,
        )

    # ---------- claves y configs ----------

    @property
    def server_private_key_path(self) -> Path:
        return self.data_dir / "server_private.key"

    def ensure_initialized(self) -> None:
        """Genera (una sola vez) las claves del servidor y el peer principal
        de esta PC. Idempotente: si ya existen, no toca nada."""
        if not self.server_private_key_path.exists():
            self.data_dir.mkdir(parents=True, exist_ok=True)
            private = generate_private_key()
            self.server_private_key_path.write_text(private, encoding="utf-8")
            (self.data_dir / "server_public.key").write_text(
                derive_public_key(private), encoding="utf-8"
            )
            self.logger_db.log_event("init", "claves del servidor generadas")
        if self.registry.get(DEFAULT_PEER_NAME) is None:
            self.registry.add_peer(DEFAULT_PEER_NAME)
            self.logger_db.log_event("peer_added", f"peer principal '{DEFAULT_PEER_NAME}'")

    def server_public_key(self) -> str:
        return (self.data_dir / "server_public.key").read_text(encoding="utf-8").strip()

    def render_server_conf(self) -> str:
        self.ensure_initialized()
        return render_server_config(
            self.server_private_key_path.read_text(encoding="utf-8").strip(),
            self.cfg.server.endpoint_port,
            self.cfg.tunnel.network_v4,
            self.cfg.tunnel.network_v6,
            self.registry.peers,
        )

    def securedns_is_alive(self) -> bool:
        """Probe rápido: el dashboard de SecureDNS responde /cache-count si
        el resolver está corriendo (punto de integración entre proyectos)."""
        try:
            with urllib.request.urlopen(self.cfg.dns.securedns_probe_url, timeout=2) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def render_client_conf(self, peer_name: str, dns_servers: list[str] | None = None) -> str:
        self.ensure_initialized()
        peer = self.registry.get(peer_name)
        if peer is None:
            raise ValueError(f"no existe el peer {peer_name!r}")
        if dns_servers is None:
            # Los peers que no son esta PC (el celular) no pueden usar el
            # SecureDNS de 127.0.0.1 (su loopback es OTRA máquina): llevan
            # los DNS públicos de respaldo.
            if peer_name == DEFAULT_PEER_NAME:
                dns_servers = choose_dns_servers(
                    self.cfg.dns.use_securedns,
                    self.cfg.dns.securedns_ip,
                    self.cfg.dns.fallback_dns,
                    self.securedns_is_alive(),
                )
            else:
                dns_servers = list(self.cfg.dns.fallback_dns)
        return render_client_config(
            peer,
            self.server_public_key(),
            self.cfg.server.endpoint_host,
            self.cfg.server.endpoint_port,
            self.cfg.tunnel.mode,
            self.cfg.tunnel.split_allowed_ips,
            dns_servers,
            self.cfg.tunnel.keepalive_seconds,
        )

    def write_client_conf(self, peer_name: str) -> Path:
        """Escribe el .conf de un peer en data/clients/. El del peer principal
        se llama como la interfaz (securevpn.conf), porque la app de Windows
        le pone al túnel el nombre del archivo."""
        clients_dir = self.data_dir / "clients"
        clients_dir.mkdir(parents=True, exist_ok=True)
        filename = (
            f"{self.cfg.tunnel.interface_name}.conf"
            if peer_name == DEFAULT_PEER_NAME
            else f"{peer_name}.conf"
        )
        path = clients_dir / filename
        path.write_text(self.render_client_conf(peer_name), encoding="utf-8")
        return path

    # ---------- conectar / desconectar ----------

    def connect(self) -> dict:
        """Conecta la VPN. Devuelve un resumen {ok, mensajes} para que el
        panel o el dashboard lo muestren."""
        messages: list[str] = []
        self.ensure_initialized()

        alive = self.securedns_is_alive()
        if self.cfg.dns.use_securedns:
            messages.append(
                "SecureDNS detectado: el túnel lo usa como DNS."
                if alive
                else "SecureDNS no está corriendo: se usan los DNS de respaldo "
                f"({', '.join(self.cfg.dns.fallback_dns)})."
            )

        conf_path = self.write_client_conf(DEFAULT_PEER_NAME)
        ok, detail = self._platform_up(conf_path)
        if not ok:
            self.logger_db.log_event("connect", detail, ok=False)
            messages.append(f"ERROR levantando el túnel: {detail}")
            return {"ok": False, "messages": messages}
        messages.append("Túnel levantado. Esperando primer handshake...")

        if self._wait_for_handshake():
            messages.append("Handshake confirmado: el túnel funciona.")
            if self.cfg.killswitch.enabled:
                executed = self.killswitch.activate()
                self.logger_db.log_event("killswitch_on", f"{len(executed)} comandos")
                messages.append(
                    "Kill switch ACTIVADO: nada sale de esta PC fuera del túnel "
                    "(loopback y red local siguen permitidos)."
                )
            self.logger_db.log_event("connect", f"peer '{DEFAULT_PEER_NAME}'")
            return {"ok": True, "messages": messages}

        # Sin handshake: honestidad ante todo, y NADA de kill switch.
        self.logger_db.log_event("connect", "sin handshake", ok=False)
        messages.append(
            "El túnel se instaló pero NO hubo handshake (¿el servidor está "
            "levantado? ¿el laboratorio está corriendo? ¿el puerto UDP "
            f"{self.cfg.server.endpoint_port} es alcanzable?). El kill switch "
            "NO se activó para no dejarte sin internet."
        )
        return {"ok": False, "messages": messages}

    def disconnect(self) -> dict:
        messages: list[str] = []
        if self.killswitch.is_active():
            self.killswitch.deactivate()
            self.logger_db.log_event("killswitch_off", "restaurado al desconectar")
            messages.append("Kill switch desactivado: firewall restaurado como estaba.")
        ok, detail = self._platform_down()
        event_ok = ok
        messages.append("Túnel desconectado." if ok else f"Aviso al bajar el túnel: {detail}")
        self.logger_db.log_event("disconnect", detail, ok=event_ok)
        return {"ok": ok, "messages": messages}

    def restore_internet(self) -> dict:
        """Botón de emergencia: revierte el firewall y baja el túnel, pase lo
        que pase y sin depender de estado guardado."""
        self.killswitch.deactivate()
        self._platform_down()
        self.logger_db.log_event("restore", "restauración de emergencia")
        return {
            "ok": True,
            "messages": [
                "Firewall restaurado (reglas SecureVPN_ eliminadas, política "
                "por defecto restablecida) y túnel bajado. Internet normal."
            ],
        }

    def _wait_for_handshake(self) -> bool:
        deadline = time.time() + HANDSHAKE_WAIT_SECONDS
        while time.time() < deadline:
            status = get_status(self.cfg.tunnel.interface_name)
            if status.get("up") and any(
                peer["latest_handshake"] > 0 for peer in status.get("peers", [])
            ):
                return True
            time.sleep(1)
        return False

    def _platform_up(self, conf_path: Path) -> tuple[bool, str]:
        if platform.system() == "Windows":
            exe = find_wireguard_exe()
            if exe is None:
                return False, (
                    "no encontré la app de WireGuard (instalala desde wireguard.com)"
                )
            # Si quedó un servicio de túnel de un intento anterior (p. ej. uno
            # que no logró handshake), se desinstala antes de reinstalar:
            # conectar dos veces seguidas nunca debe fallar por eso.
            subprocess.run(
                [exe, "/uninstalltunnelservice", self.cfg.tunnel.interface_name],
                capture_output=True, text=True, timeout=60,
            )
            time.sleep(1)  # la desinstalación del servicio es asincrónica
            result = subprocess.run(
                [exe, "/installtunnelservice", str(conf_path)],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                return False, (result.stderr or result.stdout or "error desconocido").strip()
            return True, "servicio de túnel instalado"

        env = self._linux_env()
        # wg-quick necesita bajar el túnel previo si quedó colgado.
        subprocess.run(
            ["wg-quick", "down", str(conf_path)],
            capture_output=True, text=True, timeout=60, env=env,
        )
        result = subprocess.run(
            ["wg-quick", "up", str(conf_path)],
            capture_output=True, text=True, timeout=120, env=env,
        )
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "error desconocido").strip()
        return True, "wg-quick up"

    def _platform_down(self) -> tuple[bool, str]:
        if platform.system() == "Windows":
            exe = find_wireguard_exe()
            if exe is None:
                return False, "no encontré la app de WireGuard"
            result = subprocess.run(
                [exe, "/uninstalltunnelservice", self.cfg.tunnel.interface_name],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                return False, detail or "el túnel no estaba instalado"
            return True, "servicio de túnel desinstalado"

        conf_path = self.data_dir / "clients" / f"{self.cfg.tunnel.interface_name}.conf"
        result = subprocess.run(
            ["wg-quick", "down", str(conf_path)],
            capture_output=True, text=True, timeout=60, env=self._linux_env(),
        )
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "").strip()
        return True, "wg-quick down"

    @staticmethod
    def _linux_env() -> dict:
        """En Linux sin módulo de kernel (contenedores, CI), wg-quick puede
        usar wireguard-go (implementación oficial en espacio de usuario) si
        está instalada. En un Linux normal con kernel moderno, no hace falta."""
        env = dict(os.environ)
        if not Path("/sys/module/wireguard").exists() and shutil.which("wireguard-go"):
            env["WG_QUICK_USERSPACE_IMPLEMENTATION"] = "wireguard-go"
        return env
