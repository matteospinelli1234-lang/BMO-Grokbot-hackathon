#!/usr/bin/env python3
"""Fly a collision-aware tour through every zone in zones.json.

Uses shortest free-space paths (A*) from tello_sim.nav.

Run:
  python examples/tour_zones.py
  python examples/tour_zones.py --no-visual
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tello_sim import SimulatedTello, build_pad2_zones
from tello_sim.nav import Z_LOW, go_to_zone, ordered_zones, shortest_path, sync_map
from tello_sim.zones import DEFAULT_ZONES_PATH


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Tour all zones from zones.json")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--no-visual", action="store_true")
    p.add_argument("--path", type=Path, default=DEFAULT_ZONES_PATH)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    zones = build_pad2_zones(path=args.path)
    catalog = ordered_zones(zones)
    print(f"Zone catalog: {args.path}")
    print(f"Touring {len(catalog)} zones: {[z.id for z in catalog]}")

    tello = SimulatedTello(visual=not args.no_visual, headless=args.headless)
    tello.connect()
    obstacles = sync_map(tello)

    print(f"Battery {tello.get_battery()}% — takeoff")
    tello.takeoff()
    time.sleep(0.2)

    visited: set[str] = set()
    prev = None
    try:
        for zone in catalog:
            print(f"\n=== {zone.id} ({zone.label}) ===")
            go_to_zone(tello, zone, zones, obstacles, from_zone=prev)
            visited.add(zone.id)
            prev = zone
    except Exception as exc:
        snap = tello.drone.snapshot()
        print(f"\nTOUR FAILED: {exc}")
        print(f"  final pos=({snap['x']:+.2f}, {snap['y']:+.2f}, {snap['z']:.2f})")
        try:
            tello.land()
        except Exception:
            pass
        tello.end()
        return 1

    print("\nLanding on pad...")
    snap = tello.drone.snapshot()
    start = (snap["x"], snap["y"], snap["z"])
    for i, (x, y, z) in enumerate(shortest_path(start, (0.0, 0.0, Z_LOW), obstacles)):
        print(f"  [{i + 1}] ({x:+.2f}, {y:+.2f}, {z:.2f})")
        tello.go_to_xyz(x, y, z, face=True)
    tello.land()

    missing = {z.id for z in catalog} - visited
    print(f"\nVisited {len(visited)}/{len(catalog)}: {sorted(visited)}")
    if missing:
        print(f"Missing: {sorted(missing)}")
        tello.end()
        return 1

    print("Tour complete.")
    tello.end()
    if not args.no_visual and not args.headless:
        time.sleep(1.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
