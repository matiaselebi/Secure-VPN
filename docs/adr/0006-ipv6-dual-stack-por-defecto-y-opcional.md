# ADR 0006: IPv6 dual-stack por defecto, desactivable por config

## Estado

Aceptada.

## Contexto

Un error clásico de VPNs caseras es tunelizar solo IPv4: si la red del
cliente tiene IPv6, cualquier destino alcanzable por IPv6 sale por fuera
del túnel sin cifrar ("IPv6 leak"), silenciosamente. La solución es incluir
`::/0` en los AllowedIPs del cliente y dar direcciones v6 dentro del túnel.

Pero no todo entorno tiene IPv6: hay VPS baratos y kernels de contenedor
con IPv6 deshabilitado (nos pasó durante el desarrollo: el `ip -6 address
add` fallaba con "Operation not supported" y tumbaba el aprovisionamiento
entero).

## Decisión

- Default: **dual-stack** (red v4 + red v6 internas, `::/0` en el túnel
  completo). Un test unitario protege específicamente la presencia de
  `::/0` para que nadie la borre sin enterarse.
- `tunnel.network_v6: ""` en config.yaml desactiva IPv6 de punta a punta:
  las configs se generan limpias solo-IPv4 (sin pedazos de v6 colgando).

## Consecuencias

- Protección anti-leak por defecto, compatibilidad con entornos sin IPv6
  con un cambio de una línea.
- En modo solo-IPv4 sobre una red con IPv6 real, el kill switch (política
  de salida en Block) es el que evita el leak - otra razón para tenerlo
  activado.

## Actualización (laboratorio en Docker Desktop)

Con el laboratorio como servidor por defecto ([ADR 0003](0003-laboratorio-docker-como-servidor-por-defecto.md)),
el "servidor sin IPv6" dejó de ser un caso raro y pasó a ser **el caso
normal**: Docker Desktop en Windows crea los contenedores sin IPv6, y el
`ip -6 address add` de `wg-quick` tumbaba el aprovisionamiento entero por
una parte del túnel que en ese escenario no aporta nada.

Dos cambios, en dos capas distintas y a propósito:

1. **`tunnel.network_v6` viene vacío por defecto.** Es la configuración
   honesta para el escenario real de hoy: laboratorio, IPv4 solo. Pasar a
   un VPS con IPv6 es poner la red v6 de vuelta y re-aprovisionar.
2. **El aprovisionamiento se defiende igual.** Aunque el yaml pida doble
   stack, el script mira `/proc/net/if_inet6` en el servidor y, si no hay
   IPv6, quita las direcciones v6 de la configuración antes de levantar el
   túnel. Un config que no coincide con el servidor deja de ser un error
   fatal y pasa a ser una degradación anunciada.

Lo que **no** cambió: el cliente sigue mandando `::/0` al túnel en modo
completo. Podría parecer inútil apuntar IPv6 a un servidor que no lo
tiene, pero es justo al revés: si se sacara, el tráfico IPv6 saldría por
fuera del túnel (el leak que este ADR existe para evitar). Mandándolo al
túnel, no sale - y las aplicaciones caen a IPv4 solas.
