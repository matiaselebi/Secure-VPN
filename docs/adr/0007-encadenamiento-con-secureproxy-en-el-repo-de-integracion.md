# ADR 0007: El encadenamiento forzado VPN→SecureProxy vive en el repo de integración

## Estado

Aceptada (alcance diferido al repo de integración del stack).

## Contexto

La visión del stack es tener las tres capas siempre encendidas: SecureDNS
filtrando nombres, SecureProxy filtrando conexiones, SecureVPN cifrando el
transporte. Surgió la pregunta de si SecureVPN debería FORZAR (por
iptables/routing) que todo el tráfico del túnel pase por SecureProxy, para
que ninguna app pueda esquivar el proxy.

El detalle técnico que define la respuesta: SecureProxy es un proxy
**explícito** - las aplicaciones le declaran el destino (método CONNECT /
URL absoluta). Si se le redirige tráfico a la fuerza con reglas de red, le
llegan conexiones crudas sin esa declaración, y un proxy explícito no puede
saber a dónde iban. Interceptar tráfico redirigido requiere un **modo
transparente** (deducir el destino del SNI de TLS o del Host de HTTP), que
es una capacidad del proxy, no algo que la VPN pueda imponer desde afuera.

## Decisión

- SecureVPN **no** implementa redirección forzada hacia SecureProxy.
- El encadenamiento que existe hoy y funciona: las apps que respetan el
  proxy del sistema hacen `app → SecureProxy → túnel` (la salida del proxy
  viaja por la VPN como todo el tráfico de la PC). Las apps que ignoran el
  proxy quedan cubiertas por SecureDNS (a nivel nombres) y por el cifrado
  del túnel (a nivel transporte), pero no por la reputación de IPs del
  proxy.
- La redirección forzada queda para el **repo de integración** del stack,
  y presupone agregarle a SecureProxy un modo transparente. El contrato
  técnico exacto (reglas, mecanismo de destino original, alcance, IPv6 y
  reparto de responsabilidades) está especificado en
  [`docs/contrato-redirect-secureproxy.md`](../contrato-redirect-secureproxy.md).

## Consecuencias

- Cada repo mantiene una responsabilidad clara: la VPN transporta, el
  proxy inspecciona, la integración compone.
- La sección Limitaciones del README es explícita sobre qué apps quedan
  fuera de la cobertura del proxy mientras tanto.
