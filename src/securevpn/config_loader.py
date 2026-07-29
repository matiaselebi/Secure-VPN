"""Carga la configuración desde config/config.yaml.

Misma convención que SecureProxy y SecureDNS: dataclasses con defaults
sensatos, y un config.yaml que solo pisa lo que quieras cambiar.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class ServerConfig:
    endpoint_host: str = "127.0.0.1"
    endpoint_port: int = 51820
    ssh_host: str = "127.0.0.1"
    ssh_port: int = 2222
    ssh_user: str = "root"
    ssh_key_path: str = "data/lab_ssh_key"
    public_interface: str = "eth0"


@dataclass
class TunnelConfig:
    interface_name: str = "securevpn"
    mode: str = "full"  # "full" | "split"
    split_allowed_ips: list = field(
        default_factory=lambda: ["10.66.66.0/24", "fd42:4242:4242::/64"]
    )
    network_v4: str = "10.66.66.0/24"
    network_v6: str = "fd42:4242:4242::/64"
    keepalive_seconds: int = 25


@dataclass
class DnsSettings:
    use_securedns: bool = True
    securedns_ip: str = "127.0.0.1"
    # Cómo saber si SecureDNS está vivo al momento de conectar: se le pega al
    # endpoint /cache-count de su dashboard (responde un número en texto
    # plano; lo agregamos en SecureDNS justamente como punto de integración).
    securedns_probe_url: str = "http://127.0.0.1:8890/cache-count"
    fallback_dns: list = field(default_factory=lambda: ["9.9.9.9", "1.1.1.1"])


@dataclass
class KillswitchConfig:
    enabled: bool = True
    dry_run: bool = False


@dataclass
class LoggingConfig:
    db_path: str = "data/vpn_logs.db"


@dataclass
class DashboardConfig:
    host: str = "127.0.0.1"
    port: int = 8891


@dataclass
class LabConfig:
    compose_file: str = "docker/docker-compose.lab.yml"


@dataclass
class StackConfig:
    """Dónde viven los hermanos del stack, para mostrarlos en el dashboard
    y en "Ver estado" (SecureDNS ya tiene su probe en DnsSettings)."""

    secureproxy_host: str = "127.0.0.1"
    secureproxy_port: int = 8888


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    tunnel: TunnelConfig = field(default_factory=TunnelConfig)
    dns: DnsSettings = field(default_factory=DnsSettings)
    killswitch: KillswitchConfig = field(default_factory=KillswitchConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    lab: LabConfig = field(default_factory=LabConfig)
    stack: StackConfig = field(default_factory=StackConfig)

    def resolve_path(self, relative_path: str) -> Path:
        path = Path(relative_path)
        if path.is_absolute():
            return path
        return PROJECT_ROOT / path


def load_config(config_path: str | None = None) -> Config:
    if config_path is None:
        config_path = str(PROJECT_ROOT / "config" / "config.yaml")

    raw: dict = {}
    if Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    return Config(
        server=ServerConfig(**raw.get("server", {})),
        tunnel=TunnelConfig(**raw.get("tunnel", {})),
        dns=DnsSettings(**raw.get("dns", {})),
        killswitch=KillswitchConfig(**raw.get("killswitch", {})),
        logging=LoggingConfig(**raw.get("logging", {})),
        dashboard=DashboardConfig(**raw.get("dashboard", {})),
        lab=LabConfig(**raw.get("lab", {})),
        stack=StackConfig(**raw.get("stack", {})),
    )
