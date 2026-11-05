"""Local HTTP bridge: Cursor/agent = pilot, kill switch always armed.

Run:
    python tello_agent_bridge.py --auto

Endpoints (default http://127.0.0.1:8765):
    GET  /health
    GET  /status
    GET  /zones
    POST /connect     JSON {"mode": "auto"|"sim"|"real"}
    POST /exec        JSON {"cmd": "go to ZONE_7"}
    POST /nl          JSON {"text": "fly from the beginning to area 7"}
    POST /abort       soft stop + land in place
"""

from __future__ import annotations

import json
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from tello_irl.controller import DroneSession
from tello_irl.nl import interpret_nl


def _log(msg: str) -> None:
    print(msg, flush=True)
    sys.stderr.flush()


class BridgeState:
    def __init__(self) -> None:
        self.session: DroneSession | None = None
        self.lock = threading.RLock()  # short critical sections only
        self.default_mode = "auto"
        self.visual = False
        self.tello_host: str | None = None
        self.connecting = False
        self.last_error: str | None = None

    def get_session(self) -> DroneSession | None:
        with self.lock:
            return self.session

    def ensure_session(self, mode: str | None = None) -> DroneSession:
        with self.lock:
            if self.session is not None and self.session.connected:
                return self.session
            if self.connecting:
                raise RuntimeError("Still connecting — retry in a moment")
            self.connecting = True
            use_mode = mode or self.default_mode
            visual = self.visual
            tello_host = self.tello_host
        try:
            session = DroneSession(
                mode=use_mode, visual=visual, tello_host=tello_host
            )
            session.connect()
            with self.lock:
                self.session = session
                self.last_error = None
                return session
        except Exception as exc:
            with self.lock:
                self.last_error = str(exc)
            raise
        finally:
            with self.lock:
                self.connecting = False


STATE = BridgeState()


def _json_response(handler: BaseHTTPRequestHandler, code: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler, code: int, html: str) -> None:
    body = html.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _dashboard_html() -> str:
    """Simple browser UI — / was blank JSON 404 before."""
    s = STATE.get_session()
    connected = bool(s and s.connected)
    backend = getattr(s, "backend", None) if s else None
    line = ""
    zones: list[str] = []
    try:
        if s and s.connected:
            line = s.status_line()
            zones = [z.id for z in s.zones]
    except Exception as exc:
        line = f"status error: {exc}"
    zone_opts = "\n".join(f'<option value="{z}">{z}</option>' for z in zones)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Tello Agent Bridge</title>
  <style>
    :root {{ font-family: system-ui, sans-serif; background:#12141a; color:#e8eaed; }}
    body {{ max-width: 640px; margin: 2rem auto; padding: 0 1rem; }}
    h1 {{ font-size: 1.35rem; color:#00d4b4; }}
    .card {{ background:#1c1f28; border-radius:12px; padding:1rem 1.2rem; margin:1rem 0; }}
    .ok {{ color:#6dffb0; }} .bad {{ color:#ff8a7a; }}
    button, select {{ font: inherit; padding:.55rem .9rem; margin:.25rem .25rem .25rem 0;
      border-radius:8px; border:0; cursor:pointer; }}
    button {{ background:#00d4b4; color:#06241e; font-weight:600; }}
    button.danger {{ background:#ff5c4d; color:#fff; }}
    button.secondary {{ background:#2a3140; color:#e8eaed; }}
    input[type=text] {{ width:100%; box-sizing:border-box; padding:.6rem .75rem;
      border-radius:8px; border:1px solid #333; background:#0e1016; color:#e8eaed; }}
    #log {{ white-space:pre-wrap; font-family:ui-monospace,monospace; font-size:.85rem;
      background:#0e1016; padding:.75rem; border-radius:8px; min-height:4rem; }}
    a {{ color:#7ad7ff; }}
  </style>
</head>
<body>
  <h1>Tello Agent Bridge</h1>
  <div class="card">
    <div>Status:
      <span class="{'ok' if connected else 'bad'}">
        {'CONNECTED' if connected else 'NOT CONNECTED'}
      </span>
      · backend=<b>{backend or '—'}</b>
    </div>
    <div id="line" style="margin-top:.5rem">{line or '—'}</div>
  </div>
  <div class="card">
    <button onclick="run('takeoff')">Takeoff</button>
    <button onclick="run('land')">Land</button>
    <button class="danger" onclick="abort()">ABORT / kill</button>
    <button class="secondary" onclick="refresh()">Refresh</button>
    <div style="margin-top:.8rem">
      <label>Go to zone
        <select id="zone">{zone_opts or '<option value="HUB">HUB</option>'}</select>
      </label>
      <button onclick="goZone()">Go</button>
    </div>
    <div style="margin-top:.8rem">
      <input id="nl" type="text" placeholder='e.g. go to area 7'/>
      <button onclick="nl()" style="margin-top:.4rem">Send NL</button>
    </div>
  </div>
  <div class="card">
    <div><b>For Cursor</b> — keep this bridge running, then talk in Cursor chat.
      API: <a href="/health">/health</a> · <a href="/status">/status</a> · <a href="/zones">/zones</a>
    </div>
    <div id="log" style="margin-top:.75rem"></div>
  </div>
  <script>
    const log = (x) => {{ document.getElementById('log').textContent = typeof x === 'string' ? x : JSON.stringify(x, null, 2); }};
    async function refresh() {{
      const s = await fetch('/status').then(r => r.json());
      document.getElementById('line').textContent = s.line || JSON.stringify(s);
      log(s);
    }}
    async function run(cmd) {{
      const r = await fetch('/exec', {{ method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{cmd}}) }}).then(r => r.json());
      log(r); refresh();
    }}
    async function goZone() {{
      const z = document.getElementById('zone').value;
      return run('go to ' + z);
    }}
    async function nl() {{
      const text = document.getElementById('nl').value;
      const r = await fetch('/nl', {{ method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{text}}) }}).then(r => r.json());
      log(r); refresh();
    }}
    async function abort() {{
      const r = await fetch('/abort', {{ method:'POST' }}).then(r => r.json());
      log(r); refresh();
    }}
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[bridge] " + (fmt % args) + "\n")
        sys.stderr.flush()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html", "/ui"):
                _html_response(self, 200, _dashboard_html())
                return
            if path == "/health":
                s = STATE.get_session()
                _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "connected": bool(s and s.connected),
                        "connecting": STATE.connecting,
                        "backend": getattr(s, "backend", None) if s else None,
                        "error": STATE.last_error,
                    },
                )
                return
            if path == "/status":
                s = STATE.ensure_session()
                st = s.status()
                st["line"] = s.status_line()
                _json_response(self, 200, st)
                return
            if path == "/zones":
                s = STATE.ensure_session()
                zones = [
                    {"id": z.id, "label": z.label, "hover": list(z.hover_point)}
                    for z in s.zones
                ]
                _json_response(self, 200, {"zones": zones})
                return
            _json_response(self, 404, {"error": f"unknown path {path}"})
        except Exception as exc:
            _json_response(self, 500, {"error": str(exc)})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            data = _read_json(self)
            if path == "/connect":
                mode = str(data.get("mode") or STATE.default_mode)
                old = STATE.get_session()
                if old is not None:
                    try:
                        old.close()
                    except Exception:
                        pass
                with STATE.lock:
                    STATE.session = None
                s = STATE.ensure_session(mode)
                _json_response(
                    self,
                    200,
                    {"ok": True, "backend": s.backend, "status": s.status_line()},
                )
                return

            if path == "/abort":
                s = STATE.get_session()
                if s is not None:
                    s.soft_abort()  # must NOT need STATE.lock (flight may hold work)
                _json_response(self, 200, {"ok": True, "msg": "soft abort"})
                return

            if path == "/exec":
                cmd = str(data.get("cmd") or data.get("command") or "").strip()
                if not cmd:
                    _json_response(self, 400, {"error": "missing cmd"})
                    return
                s = STATE.ensure_session()
                # Do not hold STATE.lock across the flight — abort must stay responsive
                result = s.execute(cmd)
                _json_response(
                    self,
                    200,
                    {"ok": True, "cmd": cmd, "result": result, "status": s.status()},
                )
                return

            if path == "/nl":
                text = str(data.get("text") or data.get("utterance") or "").strip()
                if not text:
                    _json_response(self, 400, {"error": "missing text"})
                    return
                s = STATE.ensure_session()
                cmd = interpret_nl(text, s.zones)
                result = s.execute(cmd)
                _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "utterance": text,
                        "cmd": cmd,
                        "result": result,
                        "status": s.status(),
                    },
                )
                return

            _json_response(self, 404, {"error": f"unknown path {path}"})
        except Exception as exc:
            _json_response(self, 500, {"error": str(exc)})


def run_bridge(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    mode: str = "auto",
    visual: bool = False,
    connect_now: bool = True,
    tello_host: str | None = None,
) -> int:
    """Start HTTP server first, then connect (so /health always works)."""
    STATE.default_mode = mode
    STATE.visual = visual
    STATE.tello_host = tello_host

    kill = threading.Event()

    def _abort_all(reason: str) -> None:
        _log(f"\n*** KILL SWITCH ({reason}) ***")
        s = STATE.get_session()
        if s is not None:
            try:
                s.soft_abort()
            except Exception as exc:
                _log(f"soft_abort error: {exc}")
        kill.set()

    def _sig(_s, _f) -> None:
        _abort_all("Ctrl+C / SIGTERM")

    signal.signal(signal.SIGINT, _sig)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _sig)

    if sys.stdin.isatty():
        def _stdin() -> None:
            while not kill.is_set():
                try:
                    line = sys.stdin.readline()
                except Exception:
                    break
                if not line:
                    break
                if line.strip().lower() in {"q", "quit", "kill", "stop", "abort", "x"}:
                    _abort_all(f"typed {line.strip()!r}")
                    break

        threading.Thread(target=_stdin, name="bridge-kill", daemon=True).start()
        _log("Kill switch: Ctrl+C  or  type kill / q + Enter")
    else:
        _log("Kill switch: Ctrl+C / SIGTERM (stdin not a TTY)")

    # Bind HTTP before connecting so agents can hit /health immediately
    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        _log(f"FATAL: cannot bind {host}:{port} — {exc}")
        return 1

    server.daemon_threads = True
    _log(f"Agent bridge on http://{host}:{port}  (mode={mode}, visual={visual})")

    def _serve() -> None:
        while not kill.is_set():
            server.timeout = 0.5
            try:
                server.handle_request()
            except Exception:
                if kill.is_set():
                    break

    srv_thread = threading.Thread(target=_serve, name="bridge-http", daemon=True)
    srv_thread.start()

    if connect_now:
        def _connect_bg() -> None:
            try:
                _log(f"Connecting drone backend ({mode})...")
                s = STATE.ensure_session(mode)
                _log(f"Bridge connected ({s.backend}): {s.status_line()}")
            except Exception as exc:
                _log(f"Connect deferred — will retry on first request ({exc})")

        threading.Thread(target=_connect_bg, name="bridge-connect", daemon=True).start()

    _log("Cursor/agent: POST /nl or /exec  — you stay as safety pilot")
    try:
        while not kill.is_set():
            time.sleep(0.2)
    finally:
        kill.set()
        s = STATE.get_session()
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
        try:
            server.server_close()
        except Exception:
            pass
        _log("Bridge stopped.")
    return 0
