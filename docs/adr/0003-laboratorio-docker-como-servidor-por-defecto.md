# ADR 0003: Laboratorio Docker como servidor por defecto (y VPS como upgrade)

## Estado

Aceptada.

## Contexto

Una VPN necesita "otra punta" fuera de la PC del usuario para dar
protección práctica. Las opciones: exigir un VPS desde el día uno (costo o
fricción de crear cuentas), usar la propia casa como servidor (depende de
poder abrir puertos; con CGNAT, común en Argentina, suele ser inviable), o
un modo laboratorio local.

## Decisión

El repo funciona completo contra un **contenedor Docker local** que hace de
"VPS de mentira": un Ubuntu pelado con SSH al que el MISMO script de
aprovisionamiento convierte en servidor WireGuard. Deliberadamente el
contenedor no trae nada pre-instalado: el laboratorio demuestra el flujo
real, no una maqueta. Pasar a un VPS real es cambiar IPs en
`config/config.yaml` - el código no distingue.

El contenedor publica sus puertos solo en 127.0.0.1 (no se expone a la red
local), corre con lo mínimo (`NET_ADMIN` + `/dev/net/tun`), y usa
wireguard-go si el kernel anfitrión no presta el módulo wireguard.

## Consecuencias

- Cualquiera clona el repo y lo prueba entero gratis y offline; el CI
  ejercita el flujo completo (aprovisionar + handshake + ping por el túnel).
- El README es explícito en que el laboratorio NO protege tráfico real:
  el túnel es real, pero el trayecto protegido mide cero metros (ambas
  puntas en la misma PC). Honestidad > marketing.
- Los peers móviles (celular) recién sirven con un servidor alcanzable
  desde afuera.
