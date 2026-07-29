# SecureVPN

![CI](https://github.com/matiaselebi/secure-vpn/actions/workflows/ci.yml/badge.svg)

VPN personal basada en **WireGuard**, automatizada de punta a punta.
Tercera capa de un stack personal de **seguridad en profundidad**, junto a
[SecureProxy](https://github.com/matiaselebi/secure-proxy) y
[SecureDNS](https://github.com/matiaselebi/secure-dns): SecureDNS decide *qué
nombres* se pueden resolver (bloquea dominios maliciosos antes de que exista
la conexión), SecureProxy inspecciona *las conexiones* que sí se hacen
(reputación de IPs, nodos TOR, blocklists), y SecureVPN cifra *el camino*
por el que viaja todo. Las capas se integran: el túnel usa SecureDNS como
resolver cuando está corriendo, y el kill switch respeta explícitamente a
los dos hermanos. Los tres se encienden y se miran juntos desde
[SecureCenter](https://github.com/matiaselebi/secure-center).

**Decisión de diseño central: acá no se implementa criptografía.** El
protocolo, el cifrado y el manejo de claves son de WireGuard (auditado,
parte del kernel de Linux); la generación de claves usa la librería
`cryptography` de Python (la implementación de referencia del ecosistema).
Lo que este proyecto aporta es todo lo que rodea al túnel: aprovisionamiento
del servidor por SSH, generación de configs, kill switch condicional,
integración con SecureDNS, dashboard y panel de control. Automatizar bien
una solución probada > reinventar mal una solución propia.

## Qué hace

- **Genera todas las claves y configs**: claves X25519 del servidor y de
  cada peer (validadas contra el `wg pubkey` oficial en los tests), clave
  presimétrica adicional por peer (la defensa extra de WireGuard, pensada
  incluso para un hipotético futuro con computación cuántica), asignación
  automática de IPs internas, y los archivos `.conf` de ambos lados.
- **Aprovisiona el servidor solo, por SSH**: un comando instala WireGuard,
  activa el ruteo, carga un firewall nftables mínimo (todo cerrado salvo
  SSH y el puerto del túnel, más el NAT de salida), enciende fail2ban para
  SSH, y deja el túnel corriendo y arrancando en cada boot. Idempotente:
  re-correrlo es la forma oficial de aplicar cambios (por ejemplo, tras
  agregar un peer).
- **Modo laboratorio**: un contenedor Docker hace de "VPS de mentira" - un
  Ubuntu pelado con SSH al que el MISMO script de aprovisionamiento
  convierte en servidor VPN. Todo el proyecto funciona gratis y offline;
  cuando haya un VPS real, solo cambian las IPs en `config/config.yaml`.
- **Kill switch condicional** (Windows): al conectar, la política de salida
  del Firewall de Windows pasa a "bloquear", con permisos explícitos para el
  túnel, el handshake, la red local y loopback (donde viven SecureDNS y
  SecureProxy). Se activa **solo después de confirmar el primer handshake**
  y se revierte al desconectar, restaurando la política que había antes.
  Reglas con prefijo `SecureVPN_`: la limpieza jamás toca reglas ajenas.
- **Túnel completo por defecto**, con **split tunneling** configurable
  (una línea en el yaml decide qué tráfico entra al túnel).
- **Dual-stack IPv4 + IPv6** dentro del túnel, incluyendo el `::/0` en
  AllowedIPs que evita el clásico IPv6 leak (y con opción de túnel solo-IPv4
  para servidores sin IPv6).
- **Integración con SecureDNS**: al conectar, si SecureDNS está corriendo,
  el túnel lo usa como resolver (toda consulta pasa por tu filtro y viaja
  cifrada); si no, usa DNS públicos de respaldo para no dejarte sin nombres.
- **Peers extra con QR**: `celular` se agrega con una opción del panel y se
  escanea con la app oficial de WireGuard.
- **Dashboard web** (`http://127.0.0.1:8891/`) con el mismo estilo oscuro
  que sus hermanos, y desde donde se maneja TODO: estado del túnel con
  último handshake y tráfico, kill switch, Conectar / Desconectar /
  Restaurar internet, agregar y quitar peers (con su QR), y la pestaña
  Servidor con Aprovisionar / Levantar laboratorio / Bajar laboratorio
  (las operaciones largas corren en segundo plano y su progreso aparece en
  la pestaña Eventos). Se inicia solo al conectar la VPN y queda registrado
  para arrancar con Windows - sin opciones aparte, plug and play.
- **Panel `SecureVPN.bat`** con auto-elevación de permisos, como en los
  otros dos proyectos: las mismas acciones que el dashboard, para cuando
  preferís la consola, más "RESTAURAR INTERNET" de emergencia.
- Cada acción queda registrada en SQLite (`data/vpn_logs.db`).

## Qué NO hace (alcance del proyecto)

Las decisiones de diseño detrás de cada punto están documentadas como ADRs
cortas en [`docs/adr/`](docs/adr/).

- **No implementa el protocolo ni criptografía propia.** Es la decisión de
  diseño número uno, no una limitación: un ingeniero de seguridad serio
  automatiza WireGuard, no lo reescribe
  ([ADR 0001](docs/adr/0001-automatizar-wireguard-en-vez-de-implementar-el-protocolo.md)).
- **No te hace anónimo.** Una VPN cambia *en quién confiás*: tu proveedor de
  internet deja de ver tu tráfico, pero el operador del servidor (vos mismo
  en el laboratorio, o tu proveedor de VPS el día de mañana) pasa a poder
  verlo. Para anonimato real existen otras herramientas (Tor) con otros
  costos.
- **No es multi-hop** (encadenar dos servidores): duplica latencia e
  infraestructura por un beneficio casi nulo en este modelo de amenaza.
- **No necesita fail2ban para WireGuard**: el protocolo no responde a
  paquetes sin una clave válida, así que es invisible a escaneos de puertos
  y no ofrece nada que "fuerza-brutear". fail2ban se instala igual, pero
  para el SSH del servidor, que sí vive bajo ataque constante en cualquier
  IP pública.
- **No hace DoH/DoT**: el cifrado de DNS hacia los upstreams públicos es
  responsabilidad (ya implementada) de SecureDNS; dentro del túnel, las
  consultas ya viajan cifradas por WireGuard.
- **En modo laboratorio no hay protección práctica del tráfico real**: el
  túnel es real (handshake, cifrado, todo medible), pero como servidor y
  cliente conviven en tu PC, el trayecto protegido mide cero metros. El
  laboratorio existe para desarrollar, testear y demostrar; la protección
  diaria llega con un servidor fuera de tu casa
  ([ADR 0003](docs/adr/0003-laboratorio-docker-como-servidor-por-defecto.md)).
- **La imagen del laboratorio trae los paquetes ya instalados**, así que
  ahí el aprovisionamiento no ejerce la instalación con `apt` (sí todo lo
  demás: configuración, ruteo, firewall, fail2ban y el túnel). Contra un
  VPS real los instala como siempre. El motivo es que ese `apt` dependía
  del DNS y del proxy de Windows -que este mismo stack cambia- y era la
  causa raíz de casi todas las fallas de encendido
  ([ADR 0008](docs/adr/0008-el-laboratorio-trae-los-paquetes-horneados.md)).

## Limitaciones

Explícitas, porque conocer los bordes es parte del diseño:

- **El kill switch tiene una ventana de segundos** entre que el túnel sube
  y el handshake se confirma; se aceptó a cambio de jamás dejarte sin
  internet por un túnel que no funciona
  ([ADR 0002](docs/adr/0002-kill-switch-condicional-y-post-handshake.md)).
- **Si SecureDNS está caído al conectar, se navega sin su filtro** (con DNS
  públicos, igual de cifrado): fail-open deliberado, avisado en el mensaje
  de conexión ([ADR 0005](docs/adr/0005-dns-fail-open-al-conectar.md)).
- **Los peers móviles no usan SecureDNS** (el 127.0.0.1 del celular es otra
  máquina): llevan DNS públicos hasta que el roadmap publique el resolver
  dentro del túnel.
- **Contra el laboratorio no se verifica la huella SSH** (es tu propio
  localhost y el contenedor la regenera en cada rebuild); contra un VPS
  real sí, siempre
  ([ADR 0004](docs/adr/0004-ssh-sin-verificacion-de-huella-solo-contra-localhost.md)).
- **Con el laboratorio, el túnel es solo IPv4**: Docker Desktop en Windows
  crea los contenedores sin IPv6, así que el servidor no puede tomar una
  dirección v6. El cliente igual sigue mandando `::/0` al túnel (no hay fuga
  de IPv6: simplemente no sale), y los sitios con doble stack caen a IPv4
  por su cuenta. Contra un VPS real con IPv6 se vuelve doble stack poniendo
  `network_v6` en el yaml
  ([ADR 0006](docs/adr/0006-ipv6-dual-stack-por-defecto-y-opcional.md)).
- **El operador del servidor ve el tráfico saliente**: una VPN mueve la
  confianza, no la elimina (ver "Qué NO hace").
- **La VPN no fuerza el tráfico a través de SecureProxy**: las apps que
  respetan el proxy del sistema hacen `app → SecureProxy → túnel`; las que
  lo ignoran viajan cifradas igual, pero sin la reputación de IPs del
  proxy. El encadenamiento forzado requiere un modo transparente en el
  proxy y vive en el repo de integración del stack
  ([ADR 0007](docs/adr/0007-encadenamiento-con-secureproxy-en-el-repo-de-integracion.md)).

## Modo "siempre prendida" (junto a SecureDNS y SecureProxy)

El stack está pensado para quedar encendido permanente, y la VPN acompaña
por diseño, sin daemons vigilantes propios:

- **Arranque con Windows**: "Conectar" instala el túnel como servicio de
  Windows con inicio automático (`wireguard.exe /installtunnelservice`).
  Reiniciás la PC y el túnel vuelve solo; deja de hacerlo recién cuando
  elegís "Desconectar". El dashboard también queda registrado para
  arrancar en cada inicio.
- **Reconexión automática**: es parte del protocolo WireGuard - no hay
  "sesión" que se caiga. Si el servidor se reinicia o la red parpadea, el
  cliente rehace el handshake solo (ayudado por el keepalive de 25s) en
  cuanto la otra punta vuelve a estar disponible.
- **Kill switch persistente**: la política de salida en Block del Firewall
  de Windows sobrevive reinicios. Si el túnel está caído al prender la PC,
  ningún tráfico sale sin cifrar hasta que el servicio reconecte - el
  bloqueo cubre TODO el tráfico de la máquina, no solo el de la VPN
  (loopback y red local siguen permitidos, así SecureDNS y SecureProxy
  operan normalmente durante el corte).

## Estructura del proyecto

```
secure-vpn/
├── README.md
├── requirements.txt
├── .gitignore
├── LICENSE
├── config/
│   └── config.yaml
├── data/                     # claves, peers, configs y logs (fuera de git)
├── src/securevpn/
│   ├── __init__.py
│   ├── config_loader.py
│   ├── keys.py               # claves X25519 vía `cryptography` + PSK
│   ├── peers.py               # registro de peers con IPs automáticas
│   ├── wg_config.py           # render de .conf (server/cliente, full/split)
│   ├── killswitch.py          # firewall condicional con prefijo SecureVPN_
│   ├── tunnel.py              # conectar/desconectar/restaurar (orquestación)
│   ├── status.py              # parseo de `wg show dump` para dashboard/panel
│   ├── provision.py           # aprovisionamiento por SSH (VPS o laboratorio)
│   ├── docker_desktop.py      # encuentra, abre y espera a Docker Desktop
│   ├── ops.py                 # operaciones compartidas entre .bat y dashboard
│   ├── logger_db.py           # eventos en SQLite
│   └── dashboard.py           # dashboard web (puerto 8891)
├── scripts/
│   ├── start_vpn.py           # encendido completo (Docker→lab→provisión→túnel)
│   ├── connect_vpn.py / disconnect_vpn.py / restore_internet.py
│   ├── vpn_status.py
│   ├── add_peer.py            # nuevo dispositivo + QR en consola
│   ├── provision_server.py
│   ├── lab_up.py / lab_down.py
│   ├── lab_smoke_test.sh      # e2e: aprovisionar + handshake + ping (CI)
│   └── run_dashboard.py / stop_dashboard.py
├── docker/
│   ├── Dockerfile.lab         # el "VPS de mentira": Ubuntu + SSH + paquetes
│   └── docker-compose.lab.yml
├── docs/adr/                  # decisiones de diseño documentadas (ADRs)
├── SecureVPN.bat              # panel de control para Windows
├── tests/
└── .github/                   # CI + Dependabot (deps al día solas)
```

## Requisitos

- **Python 3.11+** y las dependencias de `requirements.txt`.
- **La app oficial de WireGuard** ([wireguard.com/install](https://www.wireguard.com/install/))
  en la PC cliente (Windows).
- **Docker Desktop** solo si vas a usar el modo laboratorio.
- Un VPS con Debian/Ubuntu solo si vas a usar un servidor real (opcional).

## Instalación

```powershell
git clone https://github.com/matiaselebi/secure-vpn.git
cd secure-vpn
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Uso

Con `SecureVPN.bat` (pide permisos de administrador solo; los necesita para
instalar el túnel como servicio y manejar el firewall del kill switch):

1. **Opción 1 - Encender VPN**: es el único paso necesario. Hace todo el
   camino, en orden y esperando a que cada etapa esté realmente lista antes
   de largar la siguiente:

   - limpia lo que haya quedado a medias de un intento anterior (un túnel
     instalado pero sin handshake se baja antes de empezar, así el intento
     nuevo no choca contra el viejo);
   - si Docker Desktop está cerrado, lo abre y espera a que su motor arranque
     (si ya estaba corriendo no lo toca; minimizado en la bandeja cuenta como
     corriendo). Lo abre **a través del Explorador de Windows**, no como un
     hijo suyo: así arranca como el usuario normal y en su sesión, sin
     importar si quien lo pidió es el menú `.bat` o el dashboard corriendo
     sin ventana y elevado. Si Docker **no está instalado**, te lo dice y te
     abre la página de descarga en el navegador, en vez de tirar un error
     técnico;
   - levanta el laboratorio, y lo vuelve a crear si cambió su receta
     (`docker-compose.lab.yml` o `Dockerfile.lab`) desde la última vez;
   - espera a que el SSH del servidor acepte sesiones de verdad, no solo a
     que el puerto abra;
   - aprovisiona el servidor;
   - genera la config del cliente (eligiendo SecureDNS si está vivo),
     levanta el túnel, espera el handshake y recién ahí activa el kill
     switch;
   - deja el dashboard corriendo (y arrancando solo con Windows) y lo abre
     en el navegador.

2. **Opción 3 - Ver estado** (o el dashboard): handshake reciente y bytes
   moviéndose = túnel funcionando.

Las opciones 5, 6 y 7 (levantar laboratorio / aprovisionar / bajar
laboratorio) siguen estando sueltas para cuando querés hacer una sola cosa
-por ejemplo re-aprovisionar tras agregar un peer- pero para el uso normal
alcanza con la 1.

A partir de ahí podés manejar todo desde el dashboard sin volver al .bat:
tiene el mismo botón **Encender VPN (todo en uno)**.

### Por qué el encendido es un solo paso y no tres

Encadenar "levantar laboratorio", "aprovisionar" y "conectar" desde afuera
-cada uno arrancando apenas terminaba el anterior- producía fallas de
carrera: el aprovisionamiento empezaba antes de que el contenedor tuviera
red utilizable, o intentaba entrar por SSH antes de que `sshd` aceptara
sesiones. Curiosamente andaba bien desde el menú `.bat` y fallaba desde el
dashboard: en el menú, el tiempo que tarda una persona en apretar la
siguiente opción alcanzaba como espera. Ahora las esperas son explícitas y
viven en `scripts/start_vpn.py`, así que el resultado no depende de qué tan
rápido apretás.

Para sumar el celular: **opción 4** (o el formulario de la pestaña Peers
del dashboard), escanear el QR con la app oficial de WireGuard, y
re-aprovisionar para que el servidor lo acepte. Ojo: el QR contiene la
clave privada de ese peer - no lo compartas. Para darlo de baja: "Quitar"
en la pestaña Peers + re-aprovisionar, y borrar el túnel de la app del
dispositivo.

### Dashboard

`http://127.0.0.1:8891/` - misma lógica de ciclo de vida que SecureDNS y
SecureProxy: el dashboard prende con el servicio y se apaga con él. La
opción 1 (Encender VPN) lo inicia y lo deja arrancando con Windows; la
opción 2 (Desconectar) apaga TODO - túnel, kill switch, dashboard y
autostart. Sin opciones aparte ni preguntas.

Arriba muestra el estado del túnel con último handshake y tráfico, el estado
del kill switch y el de los dos hermanos, más los botones **Encender VPN
(todo en uno)**, Conectar, Desconectar y Restaurar internet. Abajo, cuatro
pestañas:

- **Peers**: agregar con formulario, quitar, ver el QR de cada uno.
- **Servidor**: aprovisionar y levantar/bajar el laboratorio. Las
  operaciones largas corren en segundo plano y van avisando en Eventos, así
  la página nunca se queda colgada esperando.
- **Eventos**: historial completo, incluido el paso a paso del encendido.
- **Configuración**: modo del túnel (full/split), kill switch, uso de
  SecureDNS y keepalive.

Una aclaración honesta sobre esa última pestaña: a diferencia del proxy y
del DNS, acá **nada se aplica en caliente**. Un túnel ya levantado tiene su
configuración congelada en la interfaz de red, así que todo lo que cambies
rige desde la PRÓXIMA conexión. La página lo dice en cada opción en vez de
simular un efecto inmediato que no existe.

Mismo estilo y comportamiento que los dashboards de SecureProxy (8889) y
SecureDNS (8890).

### Pasar del laboratorio a un VPS real

En `config/config.yaml`: `endpoint_host` y `ssh_host` con la IP del VPS,
`ssh_port: 22`, `ssh_key_path` con tu clave SSH. Si el VPS tiene IPv6,
volvé a poner `tunnel.network_v6: "fd42:4242:4242::/64"` para tener doble
stack. Después, opción 6 (aprovisionar) y opción 1 (encender). Nada más
cambia: es el mismo código, y ahí sí instala los paquetes con `apt` porque
en un servidor recién creado no están.

## El kill switch, explicado

En el modelo del Firewall de Windows las reglas de bloqueo le ganan a las
de permiso, así que "bloquear todo + permitir el túnel" no funcionaría. Lo
correcto (y lo que hace este proyecto) es cambiar la **acción por defecto**
de salida a Block y crear permisos explícitos para: loopback (SecureDNS,
SecureProxy y los dashboards siguen andando), la interfaz del túnel, el
paquete UDP del handshake hacia el servidor, la red local y DHCP.

Tres garantías, todas cubiertas por tests:

1. los permisos se crean **antes** de cerrar la puerta general (sin ventana
   de corte),
2. si el handshake no llega, el kill switch **ni se toca**,
3. al desconectar se restaura **la política que había antes** (si tu perfil
   ya estaba en Block por otra razón, se respeta), y solo se borran reglas
   con prefijo `SecureVPN_`.

Y si algo quedara a medias: opción 10, "RESTAURAR INTERNET".

## Tests

```bash
pytest tests/ -v
```

**127 tests, todos verdes.** Cobertura: generación de claves (incluida la validación cruzada contra el
`wg pubkey` oficial cuando wireguard-tools está instalado), registro de
peers (IPs secuenciales, persistencia, reuso de huecos), render de configs
(full/split, dual-stack y solo-IPv4, el `::/0` anti IPv6-leak protegido por
test propio), kill switch (orden permisos→bloqueo, prefijo `SecureVPN_`,
restauración de la política previa, dry-run), parseo de `wg show dump`,
script de aprovisionamiento (contenido, verificación final, manejo de
contenedores sin systemd, que no toque `apt` si los paquetes ya están, y la
caída a IPv4 solo cuando el servidor no tiene IPv6), orquestación de
conexión (kill switch solo tras handshake; limpieza antes de bajar el
túnel), encendido completo (que las etapas vayan en orden, que corte donde
corresponde y que no conecte si el servidor no quedó aprovisionado),
laboratorio (que no reconstruya si ya está arriba, que recree cuando cambió
la receta), arranque de Docker Desktop (encontrarlo esté donde esté
instalado, abrirlo probando varias formas y verificando que el proceso
realmente apareció, seguir esperando -pidiéndotelo- cuando no puede abrirlo
solo y continuar apenas lo abrís vos, no abrir una segunda instancia si ya
se está abriendo, y avisar bien cuando directamente no está instalado),
limpieza de un túnel a medias antes de reintentar y
dashboard (estado, QR, acciones, eventos, panel de configuración).

Además, el CI corre el **laboratorio completo de verdad**
(`scripts/lab_smoke_test.sh`): contenedor + aprovisionamiento por SSH +
conexión + **handshake real + ping a través del túnel cifrado**. No es un
mock: si eso está en verde, el proyecto funciona.

## Configuración (`config/config.yaml`)

- `server.*`: dónde está la otra punta (endpoint del túnel y acceso SSH
  para aprovisionar).
- `tunnel.mode`: `"full"` (todo el tráfico) o `"split"` (solo
  `split_allowed_ips`).
- `tunnel.network_v4` / `network_v6`: redes internas del túnel.
  `network_v6: ""` (el default hoy) = solo IPv4, porque el laboratorio corre
  sobre Docker Desktop, que crea los contenedores sin IPv6. Contra un VPS
  real con IPv6, poniendo ahí la red v6 y re-aprovisionando el túnel pasa a
  doble stack.
- `tunnel.keepalive_seconds`: cada cuánto el cliente manda un paquetito para
  que el NAT del router no se olvide del túnel.
- `lab.compose_file`: el compose del laboratorio. Cambiarlo (o cambiar el
  Dockerfile) hace que el próximo encendido recree el contenedor, para que
  el cambio se aplique de verdad.
- `dns.use_securedns`: integra con SecureDNS si está corriendo;
  `fallback_dns` cubre cuando no lo está.
- `killswitch.enabled` / `killswitch.dry_run`: el kill switch condicional;
  dry_run muestra los comandos sin ejecutarlos, como en SecureProxy.
- `dashboard.port`: 8891 (proxy 8888, DNS 8890).

## Roadmap

- Instalador/desinstalador del autostart del túnel (hoy el túnel se
  instala como servicio al conectar; el autostart automático es solo del
  dashboard).
- Publicar SecureDNS dentro del túnel para que el celular también use el
  filtro (hoy los peers móviles llevan DNS públicos).
- Rotación programada de claves de peers.
- Dashboard unificado de los tres proyectos en el repo de integración
  (docker-compose) que une SecureProxy + SecureDNS + SecureVPN.

## Aviso

Proyecto educativo/de portfolio, pensado para uso personal. El modo
laboratorio no protege tráfico real (ver "Qué NO hace"); un VPS es
requisito para protección efectiva en redes ajenas.

## Autor

Matias Elebi - [LinkedIn](https://www.linkedin.com/in/matiaselebi/) · [GitHub](https://github.com/matiaselebi)

## Licencia

MIT
