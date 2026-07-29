# ADR 0004: SSH sin verificación de huella SOLO contra localhost

## Estado

Aceptada.

## Contexto

El aprovisionamiento entra por SSH. La verificación de la huella del
servidor (host key) protege contra suplantación en la red... pero el
laboratorio es un contenedor en la propia máquina del usuario que además
regenera su huella en cada rebuild: con `StrictHostKeyChecking` estricto,
cada rebuild del laboratorio rompía el flujo con un error intimidante de
"POSSIBLE DNS SPOOFING", sin que exista ninguna red que pueda interponerse
entre una PC y su propio 127.0.0.1.

## Decisión

En `build_ssh_command()`:

- destino `127.0.0.1` / `localhost` / `::1` → `StrictHostKeyChecking=no` +
  `UserKnownHostsFile=/dev/null` (no hay adversario posible en ese trayecto).
- cualquier otro destino (VPS real) → `StrictHostKeyChecking=accept-new`:
  la primera conexión guarda la huella y cualquier cambio posterior
  (posible suplantación) corta la conexión.

Cubierto por tests unitarios para ambos casos.

## Consecuencias

- El laboratorio se puede reconstruir infinitas veces sin fricción.
- Un VPS real conserva la protección estándar contra suplantación.
- Si alguien apunta `ssh_host` a un túnel/port-forward local hacia una
  máquina remota, pierde la verificación: caso borde documentado acá.
