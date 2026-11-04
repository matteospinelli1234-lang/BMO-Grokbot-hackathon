"""Interactive IRL commander: connect → pilot window + text REPL.

An agent can skip the REPL and drive ``DroneSession.execute`` / ``set_rc`` directly.
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from tello_irl.controller import HELP_TEXT, DroneSession
from tello_irl.pilot import start_pilot_window, stop_pilot_window
from tello_sim.zones import DEFAULT_ZONES_PATH


def run_repl(session: DroneSession) -> int:
    pilot = start_pilot_window(session)
    print()
    print(HELP_TEXT)
    print()
    print(session.status_line())
    print("tello-irl>  (Ctrl+C = stop + land in place)")

    def _on_sig(_signum, _frame) -> None:
        session.soft_abort()

    signal.signal(signal.SIGINT, _on_sig)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_sig)

    try:
        while session.connected:
            try:
                line = input("tello-irl> ")
            except EOFError:
                print()
                break
            low = line.strip().lower()
            if low in ("quit", "exit", "q"):
                break
            try:
                out = session.execute(line)
                if out:
                    print(out)
            except Exception as exc:
                print(f"Error: {exc}")
                print(session.status_line())
    finally:
        stop_pilot_window(pilot)
        session.close()
        print("Disconnected.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="IRL Tello commander — WASD pilot + text commands (agent-ready)"
    )
    p.add_argument(
        "--sim",
        action="store_true",
        help="Use the 3D simulator instead of the real drone",
    )
    p.add_argument("--no-visual", action="store_true", help="Sim without 3D map window")
    p.add_argument("--path", type=Path, default=DEFAULT_ZONES_PATH)
    p.add_argument(
        "-c",
        "--command",
        action="append",
        default=[],
        help="Run a command non-interactively (repeatable), then quit — for agents/scripts",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    session = DroneSession(
        mode="sim" if args.sim else "real",
        zones_path=args.path,
        visual=not args.no_visual,
    )
    try:
        session.connect()
    except Exception as exc:
        print(f"Connection failed: {exc}", file=sys.stderr)
        return 1

    if args.command:
        # Batch / agent mode — no pilot window
        try:
            for c in args.command:
                print(f"tello-irl> {c}")
                print(session.execute(c))
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            session.soft_abort()
            session.close()
            return 1
        session.close()
        return 0

    return run_repl(session)


if __name__ == "__main__":
    raise SystemExit(main())
