"""Command line for Scout.

Run from the repository root:

    python -m scout run --mission config/demo_patrol.json
    python -m scout status
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from scout import __version__
from scout.missions import run_document, run_inbox, slot_id
from scout.models import normalize_mode
from scout.store import DEFAULT_INBOX, DEFAULT_MISSIONS_ROOT, missions_root

logger = logging.getLogger("scout.cli")


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(prog="scout", description="Scout patrol pilot")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Fly a mission JSON file or the inbox")
    run.add_argument("--mission", type=Path, help="BOSS mission JSON")
    run.add_argument("--inbox", action="store_true", help="Fly every JSON file in the inbox")
    run.add_argument("--inbox-dir", type=Path, default=DEFAULT_INBOX)
    run.add_argument(
        "--mode",
        choices=("sim", "hardware", "hw"),
        help="Override mission mode for this local run",
    )

    sub.add_parser("status", help="Print agent status without arming the drone")

    serve = sub.add_parser("serve", help="Accept BOSS missions on localhost")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    args = parser.parse_args(argv)
    if args.command == "status":
        _emit(agent_status())
        return 0
    if args.command == "serve":
        httpd = make_server(args.host, args.port, missions_root())
        logger.info("listening on http://%s:%s", args.host, args.port)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logger.info("server stopped")
        finally:
            httpd.server_close()
        return 0
    return _cmd_run(args)


def agent_status() -> dict:
    """Local agent snapshot. This is not a BOSS mission status document."""
    env_mode = os.environ.get("SCOUT_MODE", "").strip() or "sim"
    return {
        "agent": "scout",
        "version": __version__,
        "slot": slot_id(),
        "mode_env": env_mode,
        "missions_root": str(missions_root()),
        "default_missions_root": str(DEFAULT_MISSIONS_ROOT),
        "inbox": str(DEFAULT_INBOX),
    }


def make_server(host: str, port: int, root: Path) -> ThreadingHTTPServer:
    handler = _handler_class(root)
    return ThreadingHTTPServer((host, port), handler)


def _cmd_run(args) -> int:
    override = _mode_override(args.mode)
    root = missions_root()
    results: list[dict] = []
    if args.mission is None and not args.inbox:
        logger.error("pass --mission or --inbox")
        return 2
    if args.mission is not None:
        try:
            data = json.loads(args.mission.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("cannot read mission %s: %s", args.mission, exc)
            return 1
        results.append(run_document(data, missions_root=root, mode_override=override))
    if args.inbox:
        results.extend(run_inbox(args.inbox_dir, missions_root=root, mode_override=override))
    for status in results:
        _emit(status)
    return _exit_code(results)


def _mode_override(cli_mode: str | None) -> str | None:
    raw = cli_mode or os.environ.get("SCOUT_MODE", "").strip()
    if not raw:
        return None
    try:
        return normalize_mode(raw)
    except Exception:
        logger.error("mode must be sim or hardware")
        raise SystemExit(2) from None


def _exit_code(results: list[dict]) -> int:
    states = [status.get("status") for status in results]
    if any(state not in ("done", "rejected") for state in states):
        return 1
    if any(state == "rejected" for state in states):
        return 2
    return 0


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, indent=2) + "\n")


def _configure_logging() -> None:
    level = os.environ.get("SCOUT_LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _handler_class(root: Path):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            if self.path.rstrip("/") == "/status":
                self._send(200, agent_status())
                return
            self._send(404, {"error": "not_found"})

        def do_POST(self):
            if self.path.rstrip("/") != "/missions":
                self._send(404, {"error": "not_found"})
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b""
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send(400, {"error": "invalid_json"})
                return
            status = run_document(data, missions_root=root)
            http_code = 200 if status.get("status") == "done" else 422
            self._send(http_code, status)

        def log_message(self, fmt: str, *args) -> None:
            logger.info("%s %s", self.address_string(), fmt % args)

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler
