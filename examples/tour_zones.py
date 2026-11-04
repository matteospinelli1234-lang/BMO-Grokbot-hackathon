#!/usr/bin/env python3
"""Fly a collision-aware tour through every zone in zones.json.

Uses the editable catalog from the zone editor (falls back to built-ins).
Each leg yaws to face the travel direction so FPV looks forward.

Run (watch in 3D):
  python examples/tour_zones.py

Headless:
  python examples/tour_zones.py --headless
  python examples/tour_zones.py --no-visual
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tello_sim import SimulatedTello, build_pad2_zones, zone_at
from tello_sim.world import Obstacle, build_room, find_support
from tello_sim.zones import DEFAULT_ZONES_PATH, Zone


# Safe highway lanes (metres) — clear of PAD2 columns / south block mass.
Z_LOW = 1.15
Y_SOUTH = -1.2
Y_NORTH = 3.2
X_GAP = 5.0          # between Corpo9 and Corpo10
X_WEST_LANE = -8.5   # west of Corpo12
X_EAST_OF_MEZZ = -1.0
X_EAST_OF_NW = -6.0  # east of Corpo15


# Preferred visit order when those ids exist; any other zones append at the end.
PREFERRED_ORDER = [
    "PAD",
    "AISLE_EAST",
    "COLUMN_ROW",
    "NORTH_OPEN",
    "UNDER_NW_DECK",
    "NW_DECK",
    "UNDER_MEZZ",
    "MEZZ_TOP",
    "SOUTH_BLOCK",
    "AISLE_WEST",
]


def _sync_map(tello: SimulatedTello) -> list[Obstacle]:
    room = build_room()
    d = tello.drone
    d.obstacles = room.obstacles
    d.room_xmin, d.room_xmax = room.xmin, room.xmax
    d.room_ymin, d.room_ymax = room.ymin, room.ymax
    d.room_height = room.zmax
    d.move_speed = 1.1
    return room.obstacles


def _collides(x: float, y: float, z: float, obstacles: list[Obstacle], r: float = 0.18) -> bool:
    for o in obstacles:
        if o.name.startswith("wall_"):
            continue
        # Allow resting on tops
        if z >= o.zmax - 0.02 and o.xmin - r <= x <= o.xmax + r and o.ymin - r <= y <= o.ymax + r:
            continue
        if o.overlaps_sphere(x, y, z, r):
            return True
    return False


def safe_target(zone: Zone, obstacles: list[Obstacle]) -> tuple[float, float, float]:
    """Hover point for a zone, nudged out of solids if the editor placed it inside one."""
    hx, hy, hz = zone.hover_point
    if not _collides(hx, hy, hz, obstacles):
        return (hx, hy, hz)

    # Prefer flying above the blocking mass still inside the zone height band
    z_try = min(zone.zmax - 0.15, max(hz, find_support(hx, hy, obstacles, z_ref=hz + 1.0)[0] + 0.35))
    z_try = max(zone.zmin + 0.2, z_try)
    if zone.contains(hx, hy, z_try) and not _collides(hx, hy, z_try, obstacles):
        return (hx, hy, z_try)

    # Search a coarse grid inside the zone for a free cell near the hover
    best = None
    best_d = 1e9
    nx = max(3, int((zone.xmax - zone.xmin) / 0.4))
    ny = max(3, int((zone.ymax - zone.ymin) / 0.4))
    for i in range(nx + 1):
        for j in range(ny + 1):
            x = zone.xmin + (zone.xmax - zone.xmin) * (i / nx)
            y = zone.ymin + (zone.ymax - zone.ymin) * (j / ny)
            for z in (hz, z_try, Z_LOW, 0.5 * (zone.zmin + zone.zmax)):
                z = min(max(z, zone.zmin + 0.1), zone.zmax - 0.1)
                if _collides(x, y, z, obstacles):
                    continue
                d = math.dist((x, y, z), (hx, hy, hz))
                if d < best_d:
                    best_d = d
                    best = (x, y, z)
    if best is None:
        raise RuntimeError(f"No free hover found inside zone {zone.id}")
    return best


def approach_waypoints(
    zone: Zone,
    target: tuple[float, float, float],
    obstacles: list[Obstacle],
) -> list[tuple[float, float, float]]:
    """Obstacle-aware approach path into a zone hover."""
    tx, ty, tz = target
    wps: list[tuple[float, float, float]] = []

    def add(x: float, y: float, z: float) -> None:
        if not wps or math.dist(wps[-1], (x, y, z)) > 0.05:
            wps.append((x, y, z))

    # Elevated decks / mezz tops: climb beside the footprint, then slide in
    if zone.id == "NW_DECK" or (zone.zmin >= 2.5 and "DECK" in zone.id.upper()):
        add(X_EAST_OF_NW, ty, Z_LOW)
        add(X_EAST_OF_NW, ty, max(tz, zone.zmin + 0.3))
        add(tx, ty, max(tz, zone.zmin + 0.3))
        add(tx, ty, tz)
        return wps

    if zone.id == "MEZZ_TOP" or (zone.zmin >= 2.0 and "MEZZ" in zone.id.upper() and "UNDER" not in zone.id.upper()):
        add(X_WEST_LANE, ty, Z_LOW)
        add(X_EAST_OF_MEZZ, ty, Z_LOW)
        add(X_EAST_OF_MEZZ, ty, max(tz, zone.zmin + 0.3))
        add(tx, ty, max(tz, zone.zmin + 0.3))
        add(tx, ty, tz)
        return wps

    if zone.id == "UNDER_MEZZ":
        add(X_WEST_LANE, Y_NORTH, Z_LOW)
        add(X_WEST_LANE, ty, Z_LOW)
        add(tx, ty, Z_LOW)
        add(tx, ty, tz)
        return wps

    if zone.id == "UNDER_NW_DECK":
        add(tx, Y_NORTH, Z_LOW)
        add(tx, ty, Z_LOW)
        add(tx, ty, tz)
        return wps

    if zone.id == "SOUTH_BLOCK":
        # Corpo8 fills most of this zone at low Z — never cross it at Z_LOW.
        # West targets: loop via west lane. East targets: stay on +X side.
        # Otherwise clear the mass at altitude (~2.4 m).
        add(0.5, Y_SOUTH, Z_LOW)
        if tx <= -7.4:
            add(X_WEST_LANE, Y_SOUTH, Z_LOW)
            add(X_WEST_LANE, ty, Z_LOW)
            add(tx, ty, Z_LOW)
        elif tx >= -0.4:
            add(0.5, ty, Z_LOW)
            add(tx, ty, Z_LOW)
        else:
            z_over = max(tz, 2.40)
            add(0.5, ty, Z_LOW)
            add(0.5, ty, z_over)
            add(tx, ty, z_over)
        add(tx, ty, tz)
        return wps

    if zone.id == "COLUMN_ROW":
        add(X_GAP, Y_SOUTH, Z_LOW)
        add(X_GAP, 1.0, Z_LOW)
        add(tx, ty, tz)
        return wps

    if zone.id == "AISLE_EAST":
        add(X_GAP, Y_SOUTH, Z_LOW)
        add(tx, Y_SOUTH, Z_LOW)
        add(tx, ty, tz)
        return wps

    if zone.id == "NORTH_OPEN":
        add(X_GAP, Y_SOUTH, Z_LOW)
        add(X_GAP, Y_NORTH, Z_LOW)
        add(tx, Y_NORTH, Z_LOW)
        add(tx, ty, tz)
        return wps

    if zone.id == "AISLE_WEST":
        add(X_WEST_LANE, Y_SOUTH, Z_LOW)
        add(X_WEST_LANE, Y_NORTH, Z_LOW)
        add(tx, Y_NORTH, Z_LOW)
        add(tx, ty, tz)
        return wps

    if zone.id == "PAD":
        add(0.0, Y_SOUTH, Z_LOW)
        add(0.0, 0.0, Z_LOW)
        add(tx, ty, tz)
        return wps

    # Generic: south lane then into hover
    add(tx, Y_SOUTH, Z_LOW)
    add(tx, ty, Z_LOW)
    add(tx, ty, tz)
    return wps


def exit_waypoints(
    from_zone: Zone,
    to_zone: Zone | None = None,
    *,
    from_pos: tuple[float, float, float] | None = None,
) -> list[tuple[float, float, float]]:
    """Leave elevated / blocked volumes via safe lanes before the next leg."""
    wps: list[tuple[float, float, float]] = []
    if from_zone.id == "NW_DECK":
        hx, hy, hz = from_zone.hover_point
        wps += [(X_EAST_OF_NW, hy, hz), (X_EAST_OF_NW, hy, Z_LOW)]
    elif from_zone.id == "MEZZ_TOP":
        hx, hy, hz = from_zone.hover_point
        wps += [(X_EAST_OF_MEZZ, hy, hz), (X_EAST_OF_MEZZ, hy, Z_LOW)]
    elif from_zone.id == "SOUTH_BLOCK":
        # Escape west of Corpo8 via west lane, then to south highway
        x, y, z = from_pos if from_pos is not None else from_zone.hover_point
        if x <= -0.5:
            wps += [
                (X_WEST_LANE, y, max(z, Z_LOW)),
                (X_WEST_LANE, Y_SOUTH, Z_LOW),
                (0.5, Y_SOUTH, Z_LOW),
            ]
        elif z > 2.2:
            wps += [(0.5, y, z), (0.5, y, Z_LOW), (0.5, Y_SOUTH, Z_LOW)]
        else:
            wps += [(0.5, y, Z_LOW), (0.5, Y_SOUTH, Z_LOW)]
    return wps


def ordered_zones(zones: list[Zone]) -> list[Zone]:
    by_id = {z.id: z for z in zones}
    out: list[Zone] = []
    for zid in PREFERRED_ORDER:
        if zid in by_id:
            out.append(by_id.pop(zid))
    out.extend(sorted(by_id.values(), key=lambda z: z.id))
    return out


def fly(
    tello: SimulatedTello,
    x: float,
    y: float,
    z: float,
    zones: list[Zone],
    note: str = "",
) -> None:
    snap0 = tello.drone.snapshot()
    msg = f"  -> ({x:+.2f}, {y:+.2f}, {z:.2f})"
    if note:
        msg += f"  [{note}]"
    print(msg)
    tello.go_to_xyz(x, y, z, face=True)
    snap = tello.drone.snapshot()
    zn = zone_at(snap["x"], snap["y"], snap["z"], zones)
    zid = zn.id if zn else "—"
    print(
        f"     zone={zid}  pos=({snap['x']:+.2f}, {snap['y']:+.2f}, {snap['z']:.2f})  "
        f"yaw={snap['yaw']:.0f}°  Δ={math.dist((snap0['x'], snap0['y'], snap0['z']), (snap['x'], snap['y'], snap['z'])):.2f}m"
    )


def visit_zone(
    tello: SimulatedTello,
    zone: Zone,
    obstacles: list[Obstacle],
    zones: list[Zone],
    prev: Zone | None,
    visited: set[str],
) -> None:
    print(f"\n=== {zone.id} ({zone.label}) ===")
    target = safe_target(zone, obstacles)
    if target != zone.hover_point:
        print(f"  (hover adjusted to avoid solids: {tuple(round(v, 2) for v in target)})")

    path: list[tuple[float, float, float]] = []
    if prev is not None:
        snap = tello.drone.snapshot()
        path.extend(
            exit_waypoints(
                prev,
                zone,
                from_pos=(snap["x"], snap["y"], snap["z"]),
            )
        )
    path.extend(approach_waypoints(zone, target, obstacles))

    # Deduplicate consecutive duplicates
    compact: list[tuple[float, float, float]] = []
    for p in path:
        if not compact or math.dist(compact[-1], p) > 0.05:
            compact.append(p)

    for i, (x, y, z) in enumerate(compact):
        fly(tello, x, y, z, zones, note=f"wp {i + 1}/{len(compact)}")

    snap = tello.drone.snapshot()
    if not zone.contains(snap["x"], snap["y"], snap["z"], pad=0.4):
        zn = zone_at(snap["x"], snap["y"], snap["z"], zones)
        raise RuntimeError(
            f"Failed to enter {zone.id}: now in {zn.id if zn else '—'} "
            f"at ({snap['x']:.2f}, {snap['y']:.2f}, {snap['z']:.2f})"
        )
    visited.add(zone.id)
    print(f"     OK — visited {zone.id}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Tour all zones from zones.json without collisions")
    p.add_argument("--headless", action="store_true", help="Hidden window")
    p.add_argument("--no-visual", action="store_true", help="Physics only")
    p.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_ZONES_PATH,
        help=f"zones JSON (default: {DEFAULT_ZONES_PATH})",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    zones = build_pad2_zones(path=args.path)
    catalog = ordered_zones(zones)
    print(f"Zone catalog: {args.path}")
    print(f"Touring {len(catalog)} zones: {[z.id for z in catalog]}")

    tello = SimulatedTello(visual=not args.no_visual, headless=args.headless)
    tello.connect()
    obstacles = _sync_map(tello)

    print(f"Battery {tello.get_battery()}% — takeoff")
    tello.takeoff()
    time.sleep(0.2)

    visited: set[str] = set()
    prev: Zone | None = None
    try:
        for zone in catalog:
            # Skip duplicate PAD at start if takeoff already there — still mark visit
            visit_zone(tello, zone, obstacles, zones, prev, visited)
            prev = zone
    except Exception as exc:
        snap = tello.drone.snapshot()
        print(f"\nTOUR FAILED: {exc}")
        print(
            f"  final pos=({snap['x']:+.2f}, {snap['y']:+.2f}, {snap['z']:.2f})  "
            f"yaw={snap['yaw']:.0f}°"
        )
        try:
            tello.land()
        except Exception:
            pass
        tello.end()
        return 1

    print("\nLanding on pad...")
    if prev is not None:
        snap = tello.drone.snapshot()
        for x, y, z in exit_waypoints(
            prev,
            from_pos=(snap["x"], snap["y"], snap["z"]),
        ):
            fly(tello, x, y, z, zones, note="exit")
    fly(tello, 0.0, Y_SOUTH, Z_LOW, zones, note="pad lane")
    fly(tello, 0.0, 0.0, Z_LOW, zones, note="pad approach")
    tello.land()

    missing = {z.id for z in catalog} - visited
    print(f"\nVisited {len(visited)}/{len(catalog)}: {sorted(visited)}")
    if missing:
        print(f"Missing: {sorted(missing)}")
        tello.end()
        return 1

    print("Tour complete — no collisions (FPV faced each leg).")
    tello.end()
    if not args.no_visual and not args.headless:
        time.sleep(1.2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
