# ADR 0005: DNS fail-open al conectar (SecureDNS si está vivo, públicos si no)

## Estado

Aceptada.

## Contexto

La integración con SecureDNS empuja el resolver local (127.0.0.1) como DNS
del túnel, para que toda consulta pase por el filtro de amenazas. Pero
SecureDNS es un proceso que el usuario puede tener apagado. Si el .conf del
cliente dijera `DNS = 127.0.0.1` incondicionalmente y SecureDNS no
estuviera corriendo, conectar la VPN dejaría la PC sin resolución de
nombres - "internet rota" desde la perspectiva del usuario, con una causa
nada obvia de diagnosticar.

## Decisión

Al momento de conectar, se sondea si SecureDNS está vivo (GET al endpoint
`/cache-count` de su dashboard, agregado en SecureDNS justamente como punto
de integración). Vivo → el túnel usa SecureDNS. Caído → el túnel usa los
DNS públicos de respaldo (Quad9/Cloudflare) y lo dice en el mensaje de
conexión. Es la misma filosofía fail-open que la consulta a AbuseIPDB en
SecureProxy: la degradación elegante le gana al corte de servicio.

Los peers que no son esta PC (el celular) siempre llevan los DNS públicos:
su 127.0.0.1 es otra máquina.

## Consecuencias

- Conectar la VPN nunca deja al usuario sin nombres, con o sin SecureDNS.
- Trade-off explícito: con SecureDNS caído se navega sin el filtro de
  amenazas (pero igual de cifrado). El mensaje de conexión lo avisa.
- Publicar SecureDNS dentro del túnel para los peers móviles queda en el
  roadmap.
