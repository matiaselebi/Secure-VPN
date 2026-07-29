# ADR 0008: la imagen del laboratorio trae los paquetes horneados

## Estado

Aceptada.

## Contexto

El laboratorio ([ADR 0003](0003-laboratorio-docker-como-servidor-por-defecto.md))
nació como un Ubuntu pelado con SSH y nada más, para que el
aprovisionamiento tuviera que hacer todo el trabajo de verdad -incluido
instalar WireGuard, nftables y fail2ban con `apt`- igual que contra un VPS
recién creado.

En la práctica, ese `apt` resultó ser la parte más frágil de todo el
proyecto, y no por culpa del proyecto:

- Un contenedor de Docker hereda el **DNS del host**. Con SecureDNS
  encendido, el DNS del host es `127.0.0.1`... que adentro de la máquina
  virtual de Docker apunta a la propia VM, no a la PC. Resultado: sin
  resolución de nombres y `apt` colgado esperando timeouts (seis minutos
  reales en una prueba).
- Un contenedor también hereda el **proxy del sistema**. Con SecureProxy
  encendido, Docker intenta salir por `127.0.0.1:8888`, que desde la VM
  tampoco existe.
- Y arrancar el aprovisionamiento inmediatamente después de crear el
  contenedor pega en la ventana en que la red de Docker todavía no está
  usable, con el clásico "Unable to locate package".

Se pelearon los tres por separado (DNS fijo en el compose, reintentos con
timeouts cortos, diagnóstico traducido). Ayudaron, pero el encendido seguía
dependiendo de cómo estuviera configurada la red de Windows en ese momento
exacto, que es justo lo que el resto del stack cambia todo el tiempo.

## Decisión

**La imagen del laboratorio se construye con los paquetes ya instalados**
(`wireguard-tools`, `wireguard-go`, `nftables`, `fail2ban`, `iproute2`,
`iptables`), y **el script de aprovisionamiento mira qué falta antes de
tocar `apt`**: si no falta nada, no corre `apt-get update` ni
`apt-get install`. Ni siquiera necesita red.

Contra un VPS real no cambia absolutamente nada: ahí los paquetes no están,
el script los detecta faltantes y los instala como siempre, con sus
reintentos y su diagnóstico.

## Consecuencias

- El encendido de la VPN con el laboratorio deja de depender del DNS y del
  proxy de Windows, que era la causa raíz de casi todas las fallas
  reportadas. La descarga de paquetes pasa una sola vez, al construir la
  imagen.
- Se pierde una pizca de fidelidad: en el laboratorio ya no se ve a `apt`
  instalando. A cambio, se sigue viendo -y ejerciendo- todo lo demás, que
  es lo que el proyecto realmente quiere demostrar: escribir la
  configuración del servidor, activar el ruteo, cargar el firewall
  nftables, configurar fail2ban y levantar el túnel. La instalación de
  paquetes es la parte menos interesante y la más frágil del
  aprovisionamiento.
- Como la imagen ahora importa, `do_lab_up` guarda una huella de la receta
  (`docker-compose.lab.yml` + `Dockerfile.lab`) en `data/lab_build.stamp` y
  recrea el contenedor cuando cambia. Sin eso, un arreglo en el compose no
  se aplicaba nunca en una PC donde el laboratorio ya existía: `docker
  compose up -d` deja el contenedor viejo tal cual.
