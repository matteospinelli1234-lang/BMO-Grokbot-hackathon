#!/usr/bin/env python3
"""HUB → NORTH on a real Tello Talent (AP / Wi-Fi mode), or in the simulator.

Real (PC on Tello Wi-Fi):
    python fly_hub_to_north_irl.py

Simulator:
    python fly_hub_to_north_irl.py --sim

Kill switch (any of these stops movement and lands in place — does NOT cut motors):
  • Ctrl+C in the terminal
  • type  q  then Enter
  • type  kill  then Enter

Place the drone on HUB with the nose pointing world +Y before starting (real).
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Repo root on sys.path when launched from Codium
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from examples.hub_to_north import plan_hub_to_north
from tello_sim.zones import DEFAULT_ZONES_PATH


def soft_abort(tello: Any) -> None:
    """Stop translation immediately, then land where you are. Never motor-cut."""
    if tello is None:
        return
    # 1) zero RC sticks
    try:
        tello.send_rc_control(0, 0, 0, 0)
    except Exception:
        pass
    # 2) sim: cancel scripted move
    try:
        drone = getattr(tello, "drone", None)
        if drone is not None and hasattr(drone, "cancel_motion"):
            drone.cancel_motion()
    except Exception:
        pass
    # 3) real Tello SDK "stop" = hover / abort current move_*
    try:
        if hasattr(tello, "send_control_command"):
            tello.send_control_command("stop")
        elif hasattr(tello, "send_command_without_return"):
            tello.send_command_without_return("stop")
    except Exception:
        pass
    # 4) land in place
    try:
        tello.land()
        print("Soft abort: landing in place.", flush=True)
    except Exception as exc:
        print(f"Soft abort land() failed: {exc}", flush=True)


class KillSwitch:
    """Shared flag: Ctrl+C / typed q immediately soft-aborts the bound drone."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._thread: threading.Thread | None = None
        self._tello: Any = None
        self._abort_lock = threading.Lock()
        self._abort_started = False

    def bind(self, tello: Any) -> None:
        self._tello = tello

    @property
    def tripped(self) -> bool:
        return self._event.is_set()

    def trip(self, reason: str = "", *, silent: bool = False) -> None:
        first = not self._event.is_set()
        self._event.set()
        if silent or not first:
            return
        msg = "KILL SWITCH"
        if reason:
            msg += f" ({reason})"
        print(f"\n*** {msg} — stop + land in place ***", flush=True)
        # Abort from a side thread so we don't deadlock inside a signal handler
        # while djitellopy is blocked on a move_*.
        with self._abort_lock:
            if self._abort_started:
                return
            self._abort_started = True
            threading.Thread(
                target=soft_abort,
                args=(self._tello,),
                name="soft-abort",
                daemon=True,
            ).start()

    def start_stdin_watcher(self) -> None:
        def _watch() -> None:
            while not self._event.is_set():
                try:
                    line = sys.stdin.readline()
                except Exception:
                    break
                if not line:
                    break
                token = line.strip().lower()
                if token in {"q", "quit", "kill", "stop", "x"}:
                    self.trip(f"typed {token!r}")
                    break

        self._thread = threading.Thread(target=_watch, name="kill-switch", daemon=True)
        self._thread.start()
        print(
            "Kill switch armed: Ctrl+C  or  type q / kill + Enter  → stop & land (no freefall)",
            flush=True,
        )


def connect_real(timeout_s: float = 12.0) -> Any:
    from djitellopy import Tello

    print("Connecting to Tello Talent (AP mode, usually 192.168.10.1)...")
    print("Make sure this PC is on the Tello Wi-Fi before continuing.")
    tello = Tello()
    tello.RESPONSE_TIMEOUT = max(8, int(timeout_s))
    tello.connect()
    time.sleep(0.5)
    bat = tello.get_battery()
    print(f"Connected. Battery: {bat}%")
    if bat < 20:
        raise RuntimeError(f"Battery {bat}% is too low — charge before flying")
    return tello


def connect_sim(*, visual: bool = True) -> Any:
    from tello_sim import SimulatedTello

    print("Connecting to simulator...")
    tello = SimulatedTello(visual=visual)
    tello.connect()
    bat = tello.get_battery()
    print(f"Connected (sim). Battery: {bat}%")
    return tello


def countdown(seconds: int, kill: KillSwitch) -> bool:
    """Return False if kill tripped during countdown."""
    if seconds <= 0:
        return not kill.tripped
    print(f"Takeoff in {seconds}s — clear the area, or trip the kill switch to abort.")
    for i in range(seconds, 0, -1):
        if kill.tripped:
            return False
        print(f"  {i}...", flush=True)
        time.sleep(1.0)
    return not kill.tripped


def run_mission(tello: Any, legs: list[tuple[str, int]], kill: KillSwitch) -> None:
    for cmd, arg in legs:
        if kill.tripped:
            break
        label = cmd if arg == 0 else f"{cmd}({arg})"
        print(f"  → {label}", flush=True)
        if cmd == "takeoff":
            tello.takeoff()
        elif cmd == "land":
            tello.land()
        elif cmd == "move_up":
            tello.move_up(arg)
        elif cmd == "move_down":
            tello.move_down(arg)
        elif cmd == "move_left":
            tello.move_left(arg)
        elif cmd == "move_right":
            tello.move_right(arg)
        elif cmd == "move_forward":
            tello.move_forward(arg)
        elif cmd == "move_back":
            tello.move_back(arg)
        else:
            raise ValueError(f"Unknown command {cmd}")
        for _ in range(5):
            if kill.tripped:
                break
            time.sleep(0.05)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="HUB → NORTH on Tello Talent or simulator")
    p.add_argument("--sim", action="store_true", help="Fly in the 3D simulator instead of the real drone")
    p.add_argument("--no-visual", action="store_true", help="Sim without the 3D window")
    p.add_argument(
        "--countdown",
        type=int,
        default=None,
        help="Seconds before takeoff (default: 5 real, 1 sim)",
    )
    args = p.parse_args(argv)

    kill = KillSwitch()

    def _sigint(_signum, _frame) -> None:
        kill.trip("Ctrl+C")

    signal.signal(signal.SIGINT, _sigint)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _sigint)

    start, goal, legs = plan_hub_to_north(DEFAULT_ZONES_PATH)
    mode = "SIM" if args.sim else "REAL"
    print("=" * 60)
    print(f"Tello Talent — HUB → NORTH (open-loop)  [{mode}]")
    print(f"  zones: {DEFAULT_ZONES_PATH}")
    print(f"  start: ({start[0]:+.2f}, {start[1]:+.2f}, {start[2]:.2f}) m")
    print(f"  goal:  ({goal[0]:+.2f}, {goal[1]:+.2f}, {goal[2]:.2f}) m")
    print("  Legs:")
    for cmd, arg in legs:
        print(f"    {cmd}" + (f" {arg} cm" if arg else ""))
    print("=" * 60)

    kill.start_stdin_watcher()
    tello: Any = None
    cd = args.countdown if args.countdown is not None else (1 if args.sim else 5)
    try:
        tello = (
            connect_sim(visual=not args.no_visual)
            if args.sim
            else connect_real()
        )
        kill.bind(tello)
        if kill.tripped:
            print("Aborted before takeoff.")
            return 1

        if not countdown(cd, kill):
            print("Aborted during countdown.")
            return 1

        print("Flying...", flush=True)
        run_mission(tello, legs, kill)

        if kill.tripped:
            # soft_abort already kicked off from trip(); give it a moment
            time.sleep(1.5)
            print("Stopped by kill switch.")
            return 130

        print("Mission complete.")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", flush=True)
        soft_abort(tello)
        return 1
    finally:
        kill.trip("shutdown", silent=True)
        if tello is not None:
            try:
                tello.send_rc_control(0, 0, 0, 0)
            except Exception:
                pass
            try:
                tello.end()
            except Exception:
                pass
        print("Connection closed.")


if __name__ == "__main__":
    raise SystemExit(main())
