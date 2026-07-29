#!/usr/bin/env bash
# Prueba de punta a punta del laboratorio, la misma que corre el CI:
#
#   1. levanta el contenedor "VPS de mentira" (Ubuntu pelado + SSH),
#   2. lo aprovisiona por SSH con scripts/provision_server.py (instala
#      WireGuard, escribe la config, levanta el túnel del lado servidor),
#   3. conecta el cliente con scripts/connect_vpn.py (wg-quick),
#   4. verifica HANDSHAKE real y hace ping a la IP interna del servidor
#      (10.66.66.1) A TRAVÉS del túnel cifrado,
#   5. desconecta y baja todo.
#
# Se corre con un config de prueba (modo split y kill switch en dry_run)
# porque en una máquina compartida (el runner de CI) no corresponde
# secuestrar TODO el tráfico ni cambiar la política del firewall del host.
# El modo full y el kill switch real tienen sus propios tests unitarios.
#
# Uso: bash scripts/lab_smoke_test.sh   (necesita root o sudo, y Docker)

set -euo pipefail
cd "$(dirname "$0")/.."

SUDO=""
if [ "$(id -u)" != "0" ]; then
    SUDO="sudo -E"
fi
PY="${PYTHON:-python3}"

echo "== [1/6] Config de prueba (split + kill switch dry_run) =="
cp config/config.yaml config/config.yaml.bak
cat > config/config.yaml <<'YAML'
server:
  endpoint_host: "127.0.0.1"
  endpoint_port: 51820
  ssh_host: "127.0.0.1"
  ssh_port: 2222
  ssh_user: "root"
  ssh_key_path: "data/lab_ssh_key"
  public_interface: "eth0"
tunnel:
  interface_name: "securevpn"
  mode: "split"
  split_allowed_ips: ["10.66.66.0/24"]
  network_v4: "10.66.66.0/24"
  # Solo IPv4, igual que el config por defecto: los contenedores del
  # laboratorio (tanto en Docker Desktop como en el runner del CI) no tienen
  # IPv6, y pedir una direccion v6 ahi hace fallar a wg-quick.
  network_v6: ""
  keepalive_seconds: 25
dns:
  use_securedns: false
  fallback_dns: []
killswitch:
  enabled: true
  dry_run: true
logging:
  db_path: "data/vpn_logs.db"
dashboard:
  host: "127.0.0.1"
  port: 8891
lab:
  compose_file: "docker/docker-compose.lab.yml"
YAML
trap 'mv -f config/config.yaml.bak config/config.yaml' EXIT

echo "== [2/6] Levantando el laboratorio =="
$PY scripts/lab_up.py

echo "== [3/6] Aprovisionando por SSH (igual que contra un VPS real) =="
$PY scripts/provision_server.py

echo "== [4/6] Conectando el cliente =="
$SUDO env "PATH=$PATH" $PY scripts/connect_vpn.py

echo "== [5/6] Verificando handshake y ping A TRAVÉS del túnel =="
$SUDO env "PATH=$PATH" $PY scripts/vpn_status.py
ping -c 3 -W 5 10.66.66.1
echo "OK: la IP interna del servidor respondió a través del túnel cifrado."

echo "== [6/6] Desconectando y bajando el laboratorio =="
$SUDO env "PATH=$PATH" $PY scripts/disconnect_vpn.py
$PY scripts/lab_down.py

echo ""
echo "SMOKE TEST OK: aprovisionamiento + handshake + tráfico por el túnel."
