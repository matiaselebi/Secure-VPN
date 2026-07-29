"""Dashboard web de SecureVPN.

Mismo estilo visual y las mismas convenciones que los dashboards de
SecureProxy y SecureDNS (fondo oscuro, tarjetas, pestañas, refresco
automático cada 5 segundos, acciones que cierran la conexión HTTP para
evitar cuelgues de keep-alive). Puerto 8891, siguiendo la serie: proxy
8888, DNS 8890.

Muestra: estado del túnel (con el último handshake y el tráfico
enviado/recibido), estado del kill switch, los peers registrados (con el
QR de cada uno para escanear desde el celular), y el historial de eventos.
Acciones: Conectar, Desconectar y Restaurar internet.
"""

import html as html_lib
import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

import qrcode
import qrcode.image.svg

from .logger_db import LoggerDB
from .ops import (
    do_lab_down,
    do_lab_up,
    do_provision,
    do_start_all,
    secureproxy_is_alive,
    server_ssh_reachable,
)
from .status import get_status, humanize_bytes, humanize_handshake
from .tunnel import TunnelManager


class DashboardRequestHandler(BaseHTTPRequestHandler):
    manager: TunnelManager
    logger_db: LoggerDB

    # Las operaciones largas (aprovisionar, laboratorio) corren en un hilo
    # aparte para no colgar la página; de a una por vez. El progreso se ve
    # en la pestaña Eventos (la página se refresca sola cada 5 segundos).
    _bg_lock = threading.Lock()
    _bg_running: str | None = None

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        parsed_path = urlsplit(self.path)
        clean_path = parsed_path.path.rstrip("/")
        routes = {
            "/connect": lambda: self._handle_action(self.manager.connect),
            "/start-all": lambda: self._start_background(
                "encendido completo",
                lambda: do_start_all(self.manager, self.logger_db),
            ),
            "/disconnect": lambda: self._handle_action(self.manager.disconnect),
            "/restore": lambda: self._handle_action(self.manager.restore_internet),
            "/addpeer": lambda: self._handle_add_peer(parsed_path.query),
            "/removepeer": lambda: self._handle_remove_peer(parsed_path.query),
            "/provision": lambda: self._start_background(
                "aprovisionamiento",
                lambda: do_provision(self.manager, self.logger_db),
            ),
            "/lab-up": lambda: self._start_background(
                "levantar laboratorio",
                lambda: do_lab_up(self.manager.cfg, self.logger_db),
            ),
            "/lab-down": lambda: self._start_background(
                "bajar laboratorio",
                lambda: do_lab_down(self.manager.cfg, self.logger_db),
            ),
            "/qr": lambda: self._handle_qr(parsed_path.query),
            "/health": self._handle_health,
            "/state": self._handle_state,
            "/config": lambda: self._handle_config_change(parsed_path.query),
        }
        if clean_path in routes:
            routes[clean_path]()
            return
        self._serve_dashboard()

    def _start_background(self, name: str, operation) -> None:
        """Lanza una operación larga en segundo plano, de a una por vez, y
        vuelve al dashboard al instante. El resultado aparece en Eventos."""
        base = DashboardRequestHandler
        with base._bg_lock:
            if base._bg_running is not None:
                self.logger_db.log_event(
                    "ocupado",
                    f"ya hay una operación en curso ({base._bg_running}); '{name}' ignorado",
                    ok=False,
                )
                self._redirect_to_dashboard()
                return
            base._bg_running = name

        def runner():
            try:
                operation()
            except Exception as exc:  # noqa: BLE001
                self.logger_db.log_event(name, f"error inesperado: {exc}", ok=False)
            finally:
                with base._bg_lock:
                    base._bg_running = None

        threading.Thread(target=runner, daemon=True).start()
        self._redirect_to_dashboard()

    def _handle_add_peer(self, query_string: str) -> None:
        params = parse_qs(query_string)
        name = (params.get("name") or [""])[0].strip()
        if name:
            try:
                if self.manager.registry.get(name) is None:
                    self.manager.ensure_initialized()
                    self.manager.registry.add_peer(name)
                    self.manager.write_client_conf(name)
                    self.logger_db.log_event(
                        "peer_added",
                        f"peer '{name}' creado - RE-APROVISIONÁ el servidor para que lo acepte",
                    )
            except ValueError as exc:
                self.logger_db.log_event("peer_added", str(exc), ok=False)
        self._redirect_to_dashboard()

    def _handle_remove_peer(self, query_string: str) -> None:
        params = parse_qs(query_string)
        name = (params.get("name") or [""])[0].strip()
        if name == "pc":
            # El peer principal es la propia PC: sin él no hay VPN que conectar.
            self.logger_db.log_event(
                "peer_removed", "el peer 'pc' es esta PC y no se puede quitar", ok=False
            )
        elif name and self.manager.registry.remove_peer(name):
            self.logger_db.log_event(
                "peer_removed",
                f"peer '{name}' quitado - RE-APROVISIONÁ para que el servidor deje de aceptarlo "
                "(y borrá el túnel de la app de WireGuard de ese dispositivo)",
            )
        self._redirect_to_dashboard()

    # ---------- configuración editable desde el dashboard ----------

    # Lista blanca explícita de lo editable. A diferencia del proxy y el DNS,
    # acá NADA se aplica en caliente: el túnel ya levantado tiene su
    # configuración congelada en la interfaz de red. Todo esto se usa en la
    # PRÓXIMA conexión, y la página lo dice claramente para no engañar.
    OPCIONES_EDITABLES = {
        "mode": {"seccion": "tunnel", "tipo": "opcion", "valores": ("full", "split")},
        "killswitch_enabled": {
            "seccion": "killswitch", "clave_real": "enabled", "tipo": "booleano",
        },
        "use_securedns": {"seccion": "dns", "tipo": "booleano"},
        "keepalive_seconds": {
            "seccion": "tunnel", "tipo": "numero", "min": 0, "max": 600,
        },
    }

    def _handle_config_change(self, query_string: str) -> None:
        from .config_loader import PROJECT_ROOT
        from .config_writer import set_value

        params = parse_qs(query_string)
        clave = (params.get("k") or [""])[0]
        valor_crudo = (params.get("v") or [""])[0]
        spec = self.OPCIONES_EDITABLES.get(clave)
        if spec is None:
            self._redirect_to_dashboard()
            return

        if spec["tipo"] == "opcion":
            if valor_crudo not in spec["valores"]:
                self._redirect_to_dashboard()
                return
            valor = valor_crudo
        elif spec["tipo"] == "booleano":
            valor = valor_crudo.lower() in ("1", "true", "on", "si", "sí")
        else:
            try:
                valor = int(valor_crudo)
            except ValueError:
                self._redirect_to_dashboard()
                return
            if not (spec["min"] <= valor <= spec["max"]):
                self._redirect_to_dashboard()
                return

        clave_real = spec.get("clave_real", clave)
        set_value(PROJECT_ROOT / "config" / "config.yaml", spec["seccion"], clave_real, valor)

        # Se actualiza también el objeto en memoria, para que el cambio valga
        # en la próxima conexión sin tener que reiniciar el dashboard.
        seccion_obj = getattr(self.manager.cfg, spec["seccion"], None)
        if seccion_obj is not None:
            setattr(seccion_obj, clave_real, valor)

        self.logger_db.log_event(
            "config", f"{spec['seccion']}.{clave_real} = {valor} (aplica en la próxima conexión)"
        )
        self._redirect_to_dashboard()

    def _render_config_panel(self) -> str:
        cfg = self.manager.cfg
        modo = cfg.tunnel.mode
        ks = bool(cfg.killswitch.enabled)
        usar_dns = bool(cfg.dns.use_securedns)
        keepalive = cfg.tunnel.keepalive_seconds
        split = ", ".join(cfg.tunnel.split_allowed_ips) or "(vacío)"

        def boton_modo(valor: str, etiqueta: str, descripcion: str) -> str:
            activo = "ok-btn" if modo == valor else ""
            marca = " (activo)" if modo == valor else ""
            return (
                f"<form method='get' action='/config' style='display:inline'>"
                f"<input type='hidden' name='k' value='mode'>"
                f"<input type='hidden' name='v' value='{valor}'>"
                f"<button class='{activo}' type='submit'>{etiqueta}{marca}</button></form>"
                f"<span class='empty'> {descripcion}</span>"
            )

        def interruptor(clave: str, encendido: bool, prender: str, apagar: str) -> str:
            nuevo = "0" if encendido else "1"
            texto = apagar if encendido else prender
            clase = "danger-btn" if encendido else "ok-btn"
            estado = "ACTIVADO" if encendido else "desactivado"
            return (
                f"<strong>{estado}</strong> &nbsp;"
                f"<form method='get' action='/config' style='display:inline'>"
                f"<input type='hidden' name='k' value='{clave}'>"
                f"<input type='hidden' name='v' value='{nuevo}'>"
                f"<button class='{clase}' type='submit'>{texto}</button></form>"
            )

        return f"""
    <p class="empty"><strong>Todo lo de esta pestaña se aplica en la PRÓXIMA
    conexión.</strong> Un túnel ya levantado tiene su configuración congelada
    en la interfaz de red: para que un cambio tenga efecto, desconectá y
    volvé a conectar.</p>

    <h2>Qué tráfico entra al túnel</h2>
    <p>{boton_modo("full", "Túnel completo", "todo el tráfico de la PC viaja cifrado. Máxima protección; tu red local (impresora y demás) sigue funcionando igual.")}</p>
    <p>{boton_modo("split", "Split tunneling", f"solo entra al túnel lo listado en el config: {html_lib.escape(split)}. El resto sale directo (más rápido para juegos, menos protegido).")}</p>

    <h2>Kill switch</h2>
    <p class="empty">Si el túnel se cae, se bloquea todo el tráfico de la PC
    para que nada salga sin cifrar. Se activa recién cuando el handshake
    confirma que el túnel funciona, y se revierte al desconectar. Ojo: con
    split tunneling el kill switch bloquearía justo el tráfico que querés que
    salga directo, así que esa combinación no tiene sentido.</p>
    <p>{interruptor("killswitch_enabled", ks, "Activar", "Desactivar")}</p>

    <h2>Usar SecureDNS dentro del túnel</h2>
    <p class="empty">Al conectar, si SecureDNS está corriendo, el túnel lo usa
    como resolver: toda consulta pasa por tu filtro de amenazas y además viaja
    cifrada. Si no está corriendo, se usan los DNS públicos de respaldo para
    no dejarte sin resolución de nombres.</p>
    <p>{interruptor("use_securedns", usar_dns, "Activar", "Desactivar")}</p>

    <h2>Keepalive</h2>
    <p class="empty">Cada cuántos segundos el cliente manda un paquetito para
    mantener vivo el túnel a través de routers con NAT. Bajarlo ayuda en redes
    problemáticas; subirlo ahorra un poco de batería en el celular.</p>
    <form class="add-form" method="get" action="/config">
      <input type="hidden" name="k" value="keepalive_seconds">
      <input type="number" name="v" min="0" max="600" value="{keepalive}" required>
      <button type="submit">Guardar</button>
    </form>
    <p class="empty">Actual: <strong>{keepalive}</strong> segundos (default: 25)</p>

    <h2>Lo que se cambia desde el archivo</h2>
    <p class="empty">Viven en <code>config/config.yaml</code>: la dirección del
    servidor (lo único que tocás el día que pases del laboratorio a un VPS
    real), las redes internas del túnel, qué destinos entran en modo split, y
    el modo dry-run del kill switch (que muestra los comandos del firewall sin
    ejecutarlos).</p>
"""

    def _handle_state(self) -> None:
        """Estado REAL del túnel, en texto plano y de una sola línea.

        Punto de integración con SecureCenter: que el dashboard responda no
        significa que el túnel esté conectado (son cosas distintas: el
        dashboard es un proceso, el túnel es una interfaz de red). Sin este
        endpoint, un orquestador que solo mire el puerto muestra "activo"
        aunque el túnel esté caído. Valores de tunnel: connected (hay
        handshake), up (interfaz levantada sin handshake) o down."""
        status = get_status(self.manager.cfg.tunnel.interface_name)
        if not status.get("up"):
            tunnel = "down"
        elif any(peer["latest_handshake"] > 0 for peer in status.get("peers", [])):
            tunnel = "connected"
        else:
            tunnel = "up"
        killswitch = "on" if self.manager.killswitch.is_active() else "off"
        self._plain_text(f"tunnel={tunnel} killswitch={killswitch}")

    def _handle_health(self) -> None:
        """Respuesta mínima para que el .bat pueda chequear si el dashboard
        está corriendo (misma idea que /cache-count en SecureDNS)."""
        self._plain_text("ok")

    def _plain_text(self, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _handle_action(self, action) -> None:
        try:
            action()
        except Exception as exc:  # noqa: BLE001 - el dashboard no debe caerse
            self.logger_db.log_event("dashboard_error", str(exc), ok=False)
        self._redirect_to_dashboard()

    def _handle_qr(self, query_string: str) -> None:
        params = parse_qs(query_string)
        peer_name = (params.get("peer") or [""])[0].strip()
        peer = self.manager.registry.get(peer_name)
        if peer is None:
            self.send_error(404, "peer inexistente")
            return
        conf_text = self.manager.render_client_conf(peer_name)
        image = qrcode.make(conf_text, image_factory=qrcode.image.svg.SvgPathImage)
        buffer = io.BytesIO()
        image.save(buffer)
        body = buffer.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "image/svg+xml")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _redirect_to_dashboard(self) -> None:
        """Igual que en SecureProxy/SecureDNS: cerrar la conexión evita que
        una pestaña en segundo plano reuse una conexión ya muerta."""
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _serve_dashboard(self) -> None:
        status = get_status(self.manager.cfg.tunnel.interface_name)
        connected = status.get("up") and any(
            peer["latest_handshake"] > 0 for peer in status.get("peers", [])
        )
        killswitch_on = self.manager.killswitch.is_active()

        if status.get("peers"):
            first = status["peers"][0]
            handshake = humanize_handshake(first["latest_handshake"])
            rx = humanize_bytes(first["rx_bytes"])
            tx = humanize_bytes(first["tx_bytes"])
        else:
            handshake, rx, tx = "-", "-", "-"

        estado_txt = "CONECTADO" if connected else "DESCONECTADO"
        estado_color = "#7bd88f" if connected else "#ff8a8a"
        ks_txt = "ACTIVO" if killswitch_on else "inactivo"
        ks_color = "#7bd88f" if killswitch_on else "#9aa0a6"

        # Estado de los hermanos del stack: la gracia de tener los tres
        # proyectos es verlos respirar juntos desde cualquiera de ellos.
        dns_alive = self.manager.securedns_is_alive()
        proxy_alive = secureproxy_is_alive(self.manager.cfg)
        dns_txt = "activo" if dns_alive else "apagado"
        dns_color = "#7bd88f" if dns_alive else "#9aa0a6"
        proxy_txt = "activo" if proxy_alive else "apagado"
        proxy_color = "#7bd88f" if proxy_alive else "#9aa0a6"

        def peer_row(peer):
            name = html_lib.escape(peer["name"])
            if peer["name"] == "pc":
                remove_cell = "<span class='empty'>esta PC</span>"
            else:
                remove_cell = (
                    f"<a class='danger' href=\"/removepeer?name={quote(peer['name'])}\" "
                    f"onclick=\"return confirm('¿Quitar el peer ' + '{name}' + '? "
                    "Después re-aprovisioná el servidor.')\">Quitar</a>"
                )
            # Sin IPv6 (túnel solo IPv4) la celda vacía parecía un dato que
            # falta; mejor decir qué está pasando.
            celda_v6 = (
                html_lib.escape(peer["ip_v6"])
                if peer.get("ip_v6")
                else "<span class='empty'>solo IPv4</span>"
            )
            return (
                f"<tr><td>{name}</td>"
                f"<td>{html_lib.escape(peer['ip_v4'])}</td>"
                f"<td>{celda_v6}</td>"
                f"<td>{html_lib.escape(peer['created'][:10])}</td>"
                f"<td><a href=\"/qr?peer={quote(peer['name'])}\" target=\"_blank\">Ver QR</a></td>"
                f"<td>{remove_cell}</td></tr>"
            )

        peers_rows = "".join(
            peer_row(peer) for peer in self.manager.registry.peers
        ) or "<tr><td colspan='6'>Sin peers todavía (se crean al conectar o desde el panel).</td></tr>"

        base = DashboardRequestHandler
        with base._bg_lock:
            bg_running = base._bg_running
        if bg_running:
            server_line = f"Operación en curso: <strong>{html_lib.escape(bg_running)}</strong> (mirá la pestaña Eventos; esta página se refresca sola)."
        elif server_ssh_reachable(self.manager.cfg):
            server_line = "Servidor SSH <strong style='color:#7bd88f'>alcanzable</strong>: se puede aprovisionar/re-aprovisionar."
        else:
            server_line = "Servidor SSH <strong style='color:#ff8a8a'>no alcanzable</strong>: si usás el laboratorio, levantalo primero."

        error_badge = "<span class='err'>ERROR</span>"
        events_rows = "".join(
            f"<tr><td>{html_lib.escape(str(ts))}</td>"
            f"<td>{html_lib.escape(str(event))}</td>"
            f"<td>{html_lib.escape(str(detail))}</td>"
            f"<td>{'OK' if ok else error_badge}</td></tr>"
            for ts, event, detail, ok in self.logger_db.recent_events(limit=25)
        ) or "<tr><td colspan='4'>Sin eventos todavía.</td></tr>"

        endpoint = (
            f"{self.manager.cfg.server.endpoint_host}:{self.manager.cfg.server.endpoint_port}"
        )

        config_html = self._render_config_panel()

        page = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>SecureVPN - Dashboard</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "Segoe UI", sans-serif; background:#0f1115; color:#e6e6e6; padding:2rem; max-width: 960px; margin: 0 auto; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 0.25rem; }}
  h2 {{ font-size: 1.1rem; }}
  .subtitle {{ color:#9aa0a6; font-size:0.85rem; margin-top:0; }}
  .stats {{ display:flex; gap:1.25rem; margin: 1.5rem 0; flex-wrap: wrap; align-items: stretch; }}
  .card {{ background:#1a1d24; border-radius:8px; padding:1rem 1.5rem; min-width:140px; }}
  .card .value {{ font-size:1.8rem; font-weight:600; }}
  .card .label {{ color:#9aa0a6; font-size:0.85rem; }}
  /* Los botones de una tarjeta de acciones se apilan alineados a la
     izquierda y todos del mismo ancho: así tienen la misma forma entre sí
     -sin uno más ancho que otro según el largo del texto- y el ojo los lee
     como una sola columna, igual que en SecureProxy y SecureDNS. */
  .card.action {{ display:flex; align-items:stretch; justify-content:center; gap:0.5rem; flex-direction:column; }}
  .card.action form {{ display:block; }}
  .card.action button {{ width:100%; text-align:center; }}
  table {{ width:100%; border-collapse: collapse; margin-top:0.5rem; }}
  th, td {{ text-align:left; padding:0.5rem 0.75rem; border-bottom:1px solid #2a2e37; font-size:0.85rem; }}
  th {{ color:#9aa0a6; font-weight:500; }}
  .empty {{ color:#9aa0a6; font-size:0.85rem; }}
  code {{ background:#1a1d24; padding:0.1rem 0.35rem; border-radius:4px; }}
  h2 {{ margin-top:1.4rem; }}
  a {{ color:#7fb2ff; }}
  .err {{ color:#ff8a8a; }}
  .tabs {{ display:flex; gap:0.5rem; margin-top:1.5rem; border-bottom:1px solid #2a2e37; }}
  /* border-radius:0 explícito: las pestañas son <button> y heredaban el
     redondeo de la regla general, lo que curvaba las puntas de la línea
     azul del subrayado. Acá la línea tiene que ser recta. */
  .tab-btn {{ background:none; border:none; border-radius:0; color:#9aa0a6; font-size:0.9rem; font-family:inherit; padding:0.6rem 1rem; cursor:pointer; border-bottom:2px solid transparent; }}
  .tab-btn.active {{ color:#e6e6e6; border-bottom:2px solid #7fb2ff; }}
  .tab-panel {{ display:none; padding-top:1rem; }}
  .tab-panel.active {{ display:block; }}
  button {{ background:#2a2e37; border:none; color:#e6e6e6; border-radius:6px; padding:0.5rem 1rem; cursor:pointer; }}
  button.ok-btn {{ background:#1e3a26; color:#7bd88f; }}
  button.danger-btn {{ background:#3a1f22; color:#ff8a8a; }}
  a.danger {{ color:#ff8a8a; }}
  .add-form {{ display:flex; gap:0.5rem; margin-bottom:1rem; }}
  .add-form input[type=text] {{ flex:1; background:#1a1d24; border:1px solid #2a2e37; color:#e6e6e6; border-radius:6px; padding:0.5rem 0.75rem; }}
</style>
</head>
<body>
  <h1>SecureVPN</h1>
  <p class="subtitle">Panel de control - se actualiza solo cada 5 segundos - endpoint: {html_lib.escape(endpoint)}</p>
  <div class="stats">
    <div class="card"><div class="value" style="color:{estado_color}">{estado_txt}</div><div class="label">Estado del túnel</div></div>
    <div class="card"><div class="value" style="font-size:1.1rem">{html_lib.escape(handshake)}</div><div class="label">Último handshake</div></div>
    <div class="card"><div class="value" style="font-size:1.1rem">{html_lib.escape(rx)}</div><div class="label">Recibido</div></div>
    <div class="card"><div class="value" style="font-size:1.1rem">{html_lib.escape(tx)}</div><div class="label">Enviado</div></div>
    <div class="card"><div class="value" style="color:{ks_color}; font-size:1.1rem">{ks_txt}</div><div class="label">Kill switch</div></div>
    <div class="card"><div class="value" style="color:{dns_color}; font-size:1.1rem">{dns_txt}</div><div class="label">SecureDNS (filtro de nombres)</div></div>
    <div class="card"><div class="value" style="color:{proxy_color}; font-size:1.1rem">{proxy_txt}</div><div class="label">SecureProxy (filtro de conexiones)</div></div>
    <div class="card action">
      <form method="get" action="/start-all" onsubmit="return confirm('¿Encender la VPN completa? Abre Docker si hace falta, levanta el laboratorio, aprovisiona y conecta. El progreso aparece en Eventos.')">
        <button type="submit" class="ok-btn">Encender VPN (todo en uno)</button>
      </form>
      <form method="get" action="/connect" onsubmit="return confirm('¿Conectar la VPN? (usa el servidor que ya esté aprovisionado)')">
        <button type="submit">Conectar</button>
      </form>
      <form method="get" action="/disconnect" onsubmit="return confirm('¿Desconectar la VPN?')">
        <button type="submit">Desconectar</button>
      </form>
      <form method="get" action="/restore" onsubmit="return confirm('¿Restaurar internet? Revierte firewall y baja el túnel, pase lo que pase.')">
        <button type="submit" class="danger-btn">Restaurar internet</button>
      </form>
    </div>
  </div>

  <div class="tabs">
    <button class="tab-btn active" data-tab="peers" onclick="showTab('peers', this)">Peers</button>
    <button class="tab-btn" data-tab="servidor" onclick="showTab('servidor', this)">Servidor</button>
    <button class="tab-btn" data-tab="eventos" onclick="showTab('eventos', this)">Eventos</button>
    <button class="tab-btn" data-tab="config" onclick="showTab('config', this)">Configuración</button>
  </div>

  <div id="tab-peers" class="tab-panel active">
    <h2>Peers (dispositivos autorizados)</h2>
    <p class="empty">El QR se escanea con la app oficial de WireGuard del celular
    (Agregar túnel → Crear desde código QR). Ojo: el QR contiene la clave privada
    de ese peer - no lo compartas ni lo dejes a la vista. Después de agregar o
    quitar un peer, re-aprovisioná el servidor (pestaña Servidor) para que el
    cambio llegue a la otra punta.</p>
    <form class="add-form" method="get" action="/addpeer">
      <input type="text" name="name" placeholder="nombre del dispositivo (ej: celular)" required>
      <button type="submit">Agregar peer</button>
    </form>
    <table>
      <tr><th>Nombre</th><th>IP del túnel (v4)</th><th>IP del túnel (v6)</th><th>Creado</th><th>QR</th><th>Acción</th></tr>
      {peers_rows}
    </table>
  </div>

  <div id="tab-servidor" class="tab-panel">
    <h2>Servidor (la otra punta del túnel)</h2>
    <p class="empty">{server_line}</p>
    <div class="stats">
      <div class="card action">
        <form method="get" action="/lab-up" onsubmit="return confirm('¿Levantar el laboratorio Docker? La primera vez tarda un poco.')">
          <button type="submit">Levantar laboratorio</button>
        </form>
      </div>
      <div class="card action">
        <form method="get" action="/provision" onsubmit="return confirm('¿Aprovisionar el servidor por SSH? Tarda 1-3 minutos; el progreso aparece en Eventos.')">
          <button type="submit" class="ok-btn">Aprovisionar servidor</button>
        </form>
      </div>
      <div class="card action">
        <form method="get" action="/lab-down" onsubmit="return confirm('¿Bajar el laboratorio? Si la VPN está conectada, desconectala primero.')">
          <button type="submit" class="danger-btn">Bajar laboratorio</button>
        </form>
      </div>
    </div>
    <p class="empty">Aprovisionar instala/actualiza todo por SSH (WireGuard,
    firewall nftables, fail2ban, la lista de peers) y es idempotente: correrlo
    de nuevo re-aplica la configuración. Funciona igual contra el laboratorio
    que contra un VPS real - eso se elige en config/config.yaml.</p>
  </div>

  <div id="tab-eventos" class="tab-panel">
    <h2>Últimos eventos</h2>
    <table>
      <tr><th>Fecha/hora (UTC)</th><th>Evento</th><th>Detalle</th><th>Resultado</th></tr>
      {events_rows}
    </table>
  </div>

  <div id="tab-config" class="tab-panel">
    {config_html}
  </div>

<script>
var TAB_STORAGE_KEY = 'securevpn_dashboard_tab';
function showTab(name, btn) {{
  document.querySelectorAll('.tab-panel').forEach(function(el) {{ el.classList.remove('active'); }});
  document.querySelectorAll('.tab-btn').forEach(function(el) {{ el.classList.remove('active'); }});
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
  try {{ localStorage.setItem(TAB_STORAGE_KEY, name); }} catch (e) {{ /* nada */ }}
}}
(function() {{
  var saved = null;
  try {{ saved = localStorage.getItem(TAB_STORAGE_KEY); }} catch (e) {{ /* nada */ }}
  if (saved) {{
    var btn = document.querySelector('.tab-btn[data-tab="' + saved + '"]');
    if (btn) {{ showTab(saved, btn); }}
  }}
}})();
</script>
</body>
</html>"""

        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True


def build_dashboard_server(
    host: str,
    port: int,
    manager: TunnelManager,
    logger_db: LoggerDB,
) -> ThreadingHTTPServer:
    handler_class = type(
        "InjectedDashboardHandler",
        (DashboardRequestHandler,),
        {"manager": manager, "logger_db": logger_db},
    )
    server = ThreadingHTTPServer((host, port), handler_class)
    server.daemon_threads = True
    return server
