# Contrato técnico: redirección de tráfico del túnel hacia SecureProxy

Estado: **especificado, no implementado**. Este documento fija el contrato
que el repo de integración va a implementar y que SecureProxy necesita para
sumar su modo transparente. SecureVPN se compromete a las garantías de la
sección 6. Complementa a la
[ADR 0007](adr/0007-encadenamiento-con-secureproxy-en-el-repo-de-integracion.md).

## 0. Topología asumida

La redirección vive en el **servidor VPN (Linux)**, con SecureProxy
corriendo **en esa misma máquina/compose** (en el repo de integración:
contenedores hermanos compartiendo namespace de red, o el mismo host). No
en el cliente Windows: ahí no hay iptables/nftables, y el mecanismo de
recuperación de destino (sección 2) es de Linux.

El flujo objetivo, con las tres capas prendidas:

```
apps del cliente ──(túnel WireGuard cifrado)──> servidor VPN
                                                  ├─ TCP 80/443 ──> SecureProxy (transparente, :8888) ──> internet
                                                  └─ resto ───────> NAT (masquerade) ──> internet
```

## 1. La regla concreta: REDIRECT en `nat PREROUTING`

Se usa **REDIRECT** (no DNAT) porque el proxy es local a la máquina que
aplica la regla: REDIRECT reescribe el destino hacia la dirección local y
el kernel conserva el destino original consultable por el proxy (sección
2). DNAT hacia otra máquina perdería esa información (requeriría TPROXY,
complejidad que este contrato no asume).

La cadena es **PREROUTING** porque el tráfico de los peers *entra* por la
interfaz del túnel (para el servidor es tráfico ajeno a rutear, no propio;
OUTPUT solo vería el tráfico generado por el servidor mismo - usarla
causaría además un loop con la salida del propio proxy).

nftables (forma canónica del contrato):

```
table ip securevpn_redirect {
  chain prerouting {
    type nat hook prerouting priority dstnat;
    iifname "securevpn" tcp dport { 80, 443 } redirect to :8888
  }
}
```

Equivalente iptables, si el entorno lo requiere:

```
iptables -t nat -A PREROUTING -i securevpn -p tcp -m multiport --dports 80,443 -j REDIRECT --to-ports 8888
```

Anclar la regla a `iifname "securevpn"` es lo que la hace segura: solo el
tráfico que ya viajó cifrado por el túnel se desvía al proxy; el SSH y el
WireGuard del propio servidor jamás la tocan.

## 2. Cómo recibe SecureProxy ese tráfico: TCP cruda + SO_ORIGINAL_DST

El proxy recibe una **conexión TCP cruda** (sin CONNECT, sin URL
absoluta). El contrato para reconstruir el destino tiene dos niveles:

1. **Obligatorio - `SO_ORIGINAL_DST`**: tras el REDIRECT, el kernel Linux
   guarda el destino original; el proxy lo lee con
   `getsockopt(SOL_IP, SO_ORIGINAL_DST)` y obtiene IP:puerto reales. Con
   eso solo, ya puede aplicar TODA su inteligencia de IPs (AbuseIPDB,
   Feodo Tracker, nodos TOR) y saber si conectar al 80 o al 443.
2. **Enriquecimiento - SNI / Host, best-effort**: para el filtrado por
   *dominio*, en el puerto 443 el proxy espía el `ClientHello` de TLS y
   extrae el **SNI** (leer, no descifrar: coherente con su ADR 0001 de "sin
   inspección TLS"); en el puerto 80, el header **Host**. Si el SNI no está
   (clientes con ECH, protocolos raros sobre 443), degrada a los chequeos
   por IP del nivel 1 - nunca corta por no poder clasificar.

`SO_ORIGINAL_DST` es Linux-only: segunda razón por la que el modo
transparente vive en el servidor de integración y no en Windows.

## 3. Alcance: SOLO TCP 80/443; el resto pasa directo

La redirección aplica únicamente a `tcp dport { 80, 443 }`. Todo lo demás
(UDP, juegos, SSH, cualquier puerto no-web) sigue el camino normal del
túnel → masquerade → internet, sin tocar el proxy. Razones:

- SecureProxy habla HTTP/HTTPS; meterle tráfico arbitrario solo puede
  romperlo (y romperle la conexión al usuario).
- **DNS (53/853) queda explícitamente FUERA del redirect**: los nombres
  son la jurisdicción de SecureDNS. Redirigir DNS al proxy duplicaría
  capas y rompería el DoT.
- Espeja la cobertura actual del proxy explícito: gana en que ninguna app
  puede esquivarlo, no en clases nuevas de tráfico.

## 4. IPv6: fase 1 solo IPv4

El contrato inicial cubre IPv4 (`table ip`). El tráfico IPv6 del túnel
sale directo (cifrado y con DNS filtrado, pero sin proxy). La extensión a
dual-stack está prevista y es mecánica - `table ip6` equivalente +
`IP6T_SO_ORIGINAL_DST` del lado del proxy - y se encara cuando el modo
transparente IPv4 esté probado. Mientras tanto, si se quiere garantizar
que TODO el tráfico web pase por el proxy, la opción operativa es correr
el túnel solo-IPv4 (`tunnel.network_v6: ""`).

## 5. ¿De quién es este código? Del repo de integración

Las reglas de este contrato **NO las instala SecureVPN**. El
aprovisionamiento de SecureVPN monta transporte (WireGuard + firewall
mínimo + NAT) y funciona con o sin proxy al lado; la redirección solo
tiene sentido cuando el compose de integración despliega SecureProxy (con
su futuro modo transparente) junto al servidor WireGuard. Responsabilidad
de cada parte:

**El repo de integración instala/configura:**

1. La tabla `securevpn_redirect` de la sección 1.
2. Una excepción de entrada en el firewall del servidor (el REDIRECT
   convierte el tráfico en local, así que pasa por la chain `input`, que
   SecureVPN deja con política drop):
   `iifname "securevpn" tcp dport 8888 accept`
3. SecureProxy en modo transparente escuchando en 8888 en el servidor.

**SecureProxy (cuando encare el modo transparente) implementa:**
`SO_ORIGINAL_DST` + parseo best-effort de SNI/Host (sección 2).

## 6. Garantías de compatibilidad que asume SecureVPN (esto SÍ nos ata)

1. El nombre de la interfaz del túnel sale de `tunnel.interface_name`
   (default `securevpn`) y es estable.
2. El firewall aprovisionado usa solo los hooks `input`, `forward`
   (filter) y `postrouting` (nat/masquerade): **el hook `prerouting` de
   nat queda libre** para la tabla de redirección - sin conflictos.
3. Las tablas nftables propias se llaman `securevpn` y `securevpn_nat`;
   el prefijo `securevpn_redirect` queda reservado para la integración.
4. El servidor rutea los peers con forwarding activado y masquerade en la
   interfaz pública: el tráfico no redirigido conserva su camino actual.

Cualquier cambio de SecureVPN que rompa 1-4 es un cambio de contrato y se
documenta acá primero.
