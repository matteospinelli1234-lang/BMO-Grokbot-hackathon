#!/usr/bin/env python3
"""Natural-language console that talks to the agent bridge (or starts a local session).

Prefer: leave ``tello_agent_bridge.py`` running, then either
  - talk to Cursor in chat (main controller), or
  - type here for quick local NL → bridge /nl

    python tello_nl_console.py
    nl> move the drone from the beginning position to area 7

Kill: Ctrl+C or type kill
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import urllib.error
import urllib.request


DEFAULT_URL = "http://127.0.0.1:8765"


def _post(base: str, path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    p = argparse.ArgumentParser(description="NL console → Tello agent bridge")
    p.add_argument("--url", default=DEFAULT_URL)
    args = p.parse_args()

    def abort(_s=None, _f=None) -> None:
        try:
            _post(args.url, "/abort", {})
        except Exception as exc:
            print(f"abort failed: {exc}")
        raise SystemExit(130)

    signal.signal(signal.SIGINT, abort)

    try:
        health = _get(args.url, "/health")
        print(f"Bridge OK: {health}")
    except Exception as exc:
        print(
            f"Cannot reach bridge at {args.url} ({exc}).\n"
            f"Start it first:  python tello_agent_bridge.py --auto",
            file=sys.stderr,
        )
        return 1

    print("Natural language → drone (via bridge). Cursor chat is the preferred controller.")
    print("Examples: 'fly from the beginning to area 7'  |  'land'  |  'status'")
    print("Kill: Ctrl+C or type kill\n")

    while True:
        try:
            line = input("nl> ")
        except EOFError:
            print()
            break
        low = line.strip().lower()
        if not low:
            continue
        if low in {"quit", "exit", "q"}:
            break
        if low in {"kill", "abort", "stop"}:
            abort()
        try:
            out = _post(args.url, "/nl", {"text": line})
            print(f"  cmd: {out.get('cmd')}")
            print(f"  {out.get('result')}")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            print(f"Error: {body}")
        except Exception as exc:
            print(f"Error: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
