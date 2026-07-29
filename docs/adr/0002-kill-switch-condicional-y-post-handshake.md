# ADR 0002: Kill switch condicional, activado solo después del handshake

## Estado

Aceptada.

## Contexto

Un kill switch "siempre activo" (como el de algunas VPN comerciales) corta
todo el tráfico apenas el túnel deja de existir, esté o no el usuario
intentando usar la VPN. En este proyecto el servidor puede ser un
contenedor de laboratorio que el usuario apaga a voluntad, y los otros dos
proyectos del stack (SecureDNS, SecureProxy) corren en la misma PC: un kill
switch agresivo podía dejar al usuario sin internet por apagar el
laboratorio, o romper los servicios hermanos.

Además, en Windows las reglas de bloqueo del firewall le ganan a las de
permiso, así que "bloquear todo + permitir el túnel" no funciona: hay que
cambiar la acción por defecto de salida y crear permisos explícitos.

## Decisión

1. El kill switch se activa **solo cuando el usuario conecta la VPN** desde
   el panel/dashboard, y **solo después de confirmar el primer handshake**
   (túnel demostradamente funcional). Sin handshake, no se toca el firewall.
2. Al desconectar se restaura la política por defecto **que había antes**
   (guardada al activar), no un valor asumido.
3. Todas las reglas llevan prefijo `SecureVPN_`; la limpieza borra solo eso
   (las `SecureProxy_Block_*` de SecureProxy jamás se tocan).
4. Permisos explícitos: loopback (SecureDNS/SecureProxy/dashboards), la
   interfaz del túnel, el UDP del handshake al endpoint, la red local y
   DHCP - creados ANTES de cerrar la puerta general.
5. Botón "Restaurar internet": reversión incondicional para cualquier
   estado a medias.

## Consecuencias

- Ventana pequeña sin kill switch entre "túnel arriba" y "handshake
  confirmado" (~segundos): aceptada a cambio de la garantía de nunca dejar
  al usuario sin conectividad por un túnel que no funciona.
- El orden permisos→bloqueo, el prefijo y la restauración de la política
  previa están cubiertos por tests unitarios.
