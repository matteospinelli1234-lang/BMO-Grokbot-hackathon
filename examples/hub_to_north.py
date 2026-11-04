#!/usr/bin/env python3
"""Fly HUB → NORTH on a real Tello (or rehearse in the sim).

Open-loop body-frame mission derived from zones.json:
  start  = HUB.origin  (place the craft there on the floor)
  finish = NORTH hover
  yaw 0  = nose along world +Y (same as the sim)

Real Tello has no world xyz, so this expands the straight HUB→NORTH
vector into takeoff / up / left-right / forward-back SDK moves.

Usage:
  python examples/hub_to_north.py              # sim rehearsal (default)
  python examples/hub_to_north.py --real       # actual drone on Wi-Fi
  python examples/hub_to_north.py --real --dry-run
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tello_sim.nav import resolve_zone
from tello_sim.zones import DEFAULT_ZONES_PATH, build_pad2_zones, spawn_pose


# Tello SDK move_* limits (centimetres)
_MIN_CM = 20
_MAX_CM = 500


def _chunk_cm(total_cm: int) -> list[int]:
    """Split a signed centimetre distance into legal Tello segments."""
    if abs(total_cm) < _MIN_CM:
        return []
    sign = 1 if total_cm > 0 else -1
    left = abs(total_cm)
    out: list[int] = []
    while left > 0:
        step = min(_MAX_CM, left)
        if left - step > 0 and left - step < _MIN_CM:
            # avoid a leftover < 20 cm: pull from previous or overshoot slightly
            step = left
            if step > _MAX_CM:
                step = left - _MIN_CM
        out.append(sign * step)
        left -= abs(step)
    return out


def plan_hub_to_north(zones_path: Path | None = None) -> tuple[tuple[float, float, float], tuple[float, float, float], list[tuple[str, int]]]:
    """Return (start_xyz_m, goal_xyz_m, [(command, cm_or_deg), ...])."""
    zones = build_pad2_zones(path=zones_path)
    start = spawn_pose(zones)
    north = resolve_zone("NORTH", zones)
    goal = north.hover_point

    dx = goal[0] - start[0]  # world +X = body right at yaw 0
    dy = goal[1] - start[1]  # world +Y = body forward at yaw 0
    target_z_cm = int(round(goal[2] * 100))
    # Tello takeoff typically ends near 80–100 cm; climb the rest after takeoff
    takeoff_z_cm = 90
    up_cm = max(0, target_z_cm - takeoff_z_cm)

    legs: list[tuple[str, int]] = [("takeoff", 0)]
    for cm in _chunk_cm(up_cm):
        legs.append(("move_up", abs(cm)))

    for cm in _chunk_cm(int(round(dx * 100))):
        legs.append(("move_right" if cm > 0 else "move_left", abs(cm)))

    for cm in _chunk_cm(int(round(dy * 100))):
        legs.append(("move_forward" if cm > 0 else "move_back", abs(cm)))

    legs.append(("land", 0))
    return start, goal, legs


def make_tello(real: bool, *, visual: bool = True):
    if real:
        from djitellopy import Tello

        return Tello()
    from tello_sim import SimulatedTello

    return SimulatedTello(visual=visual)


def run_legs(tello, legs: list[tuple[str, int]], *, dry_run: bool = False) -> None:
    for cmd, arg in legs:
        label = cmd if arg == 0 else f"{cmd}({arg})"
        print(f"  → {label}")
        if dry_run:
            continue
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
        time.sleep(0.2)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="HUB → NORTH open-loop flight (real Tello or sim)")
    p.add_argument(
        "--real",
        action="store_true",
        help="Use djitellopy on the Wi-Fi Tello (default: simulator)",
    )
    p.add_argument("--dry-run", action="store_true", help="Print legs only, do not fly")
    p.add_argument("--no-visual", action="store_true", help="Sim without 3D window")
    p.add_argument("--path", type=Path, default=DEFAULT_ZONES_PATH, help="zones.json path")
    args = p.parse_args(argv)

    start, goal, legs = plan_hub_to_north(args.path)
    dist = math.dist(start, goal)
    print("HUB → NORTH  (open-loop body moves, yaw 0 = +Y)")
    print(f"  zones: {args.path}")
    print(f"  start HUB.origin  ({start[0]:+.2f}, {start[1]:+.2f}, {start[2]:.2f}) m")
    print(f"  goal  NORTH hover ({goal[0]:+.2f}, {goal[1]:+.2f}, {goal[2]:.2f}) m")
    print(f"  straight-line {dist:.2f} m   mode={'REAL' if args.real else 'SIM'}")
    print("  Place the drone on HUB with nose toward world +Y before arming.")
    print("  Legs:")
    for cmd, arg in legs:
        print(f"    {cmd}" + (f" {arg} cm" if arg else ""))

    if args.dry_run:
        print("Dry-run complete (no connect).")
        return 0

    tello = make_tello(args.real, visual=not args.no_visual)
    try:
        print("Connecting...")
        tello.connect()
        try:
            bat = tello.get_battery()
            print(f"Battery: {bat}%")
            if args.real and bat < 20:
                print("Battery too low (<20%). Aborting.")
                return 1
        except Exception:
            pass

        run_legs(tello, legs, dry_run=False)
        print("Done.")
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted — landing...")
        try:
            tello.land()
        except Exception:
            pass
        return 130
    finally:
        try:
            tello.end()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
