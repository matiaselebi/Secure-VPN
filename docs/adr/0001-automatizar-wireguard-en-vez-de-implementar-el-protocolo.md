# ADR 0001: Automatizar WireGuard en vez de implementar el protocolo

## Estado

Aceptada.

## Contexto

Una VPN necesita un protocolo de túnel cifrado. Las opciones eran:
implementar algo propio (aunque sea "simple") o automatizar una solución
existente y auditada. Implementar criptografía o protocolos de red propios
es la fuente clásica de vulnerabilidades: incluso equipos profesionales se
equivocan en detalles (nonces reusados, timing attacks, validaciones
faltantes) que no se ven en un code review casual.

## Decisión

Se automatiza **WireGuard**: protocolo auditado, ~4k líneas de código,
incluido en el kernel de Linux, con apps oficiales para Windows/iOS/Android.
Este proyecto no toca criptografía en ningún punto:

- Las claves X25519 se generan con la librería `cryptography` (la
  implementación de referencia del ecosistema Python). Los tests validan
  compatibilidad byte a byte contra el `wg pubkey` oficial.
- La clave presimétrica por peer son 32 bytes del generador criptográfico
  del sistema operativo (`secrets`).
- El cifrado del túnel, el handshake y el manejo de paquetes son 100% de
  WireGuard (kernel, app oficial de Windows, o wireguard-go).

Lo que el proyecto aporta es la capa de operación: aprovisionamiento,
generación de configs, kill switch, integración con SecureDNS, dashboard.

## Consecuencias

- La seguridad del túnel es la de WireGuard, no la nuestra: exactamente lo
  que se busca.
- El proyecto depende de que WireGuard esté instalado en cada extremo
  (documentado en Requisitos).
- En una entrevista, la respuesta a "¿por qué no lo hiciste vos?" es esta
  ADR.
