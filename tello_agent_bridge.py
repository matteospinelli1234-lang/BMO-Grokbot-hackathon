#!/usr/bin/env python3
"""Start the drone agent bridge (Cursor controls the drone; you keep the kill switch).

    python tello_agent_bridge.py --sim           # recommended while testing
    python tello_agent_bridge.py --auto          # try real, else sim

Then in Cursor chat:
    "make it depart" / "fly to area 7" / "land"

Or curl:
    curl -s http://127.0.0.1:8765/health
    curl -s http://127.0.0.1:8765/nl -H 'Content-Type: application/json' \\
      -d '{"text":"takeoff"}'
"""

from __future__ import annotations

import argparse

from tello_irl.bridge import run_bridge


def main() -> int:
    p = argparse.ArgumentParser(description="Tello agent HTTP bridge + kill switch")
    p.add_argument("--sim", action="store_true", help="Force simulator")
    p.add_argument("--auto", action="store_true", help="Try real Tello, fall back to sim")
    p.add_argument("--real", action="store_true", help="Real Tello only")
    p.add_argument(
        "--visual",
        action="store_true",
        help="Show 3D sim window (off by default — was hanging the bridge)",
    )
    p.add_argument("--host", default="127.0.0.1", help="HTTP bind address")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument(
        "--tello-host",
        default=None,
        help="Drone IP (station/router mode DHCP address; default 192.168.10.1 AP mode)",
    )
    p.add_argument("--no-connect", action="store_true", help="Don't connect until first request")
    args = p.parse_args()
    if args.sim:
        mode = "sim"
    elif args.real:
        mode = "real"
    else:
        mode = "auto"
    return run_bridge(
        host=args.host,
        port=args.port,
        mode=mode,
        visual=args.visual,
        connect_now=not args.no_connect,
        tello_host=args.tello_host,
    )


if __name__ == "__main__":
    raise SystemExit(main())
