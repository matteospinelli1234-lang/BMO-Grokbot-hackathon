"""Shared zone-to-zone navigation: shortest collision-free paths (A*)."""

from __future__ import annotations

import heapq
import math
import re
from typing import Callable

from tello_sim.api import SimulatedTello
from tello_sim.world import Obstacle, build_room, find_support
from tello_sim.zones import Zone, zone_at


Vec3 = tuple[float, float, float]

# Default cruise height when a path stays near the floor
Z_LOW = 1.15

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

ALIASES: dict[str, str] = {
    "NORTH": "NORTH_OPEN",
    "SOUTH": "SOUTH_BLOCK",
    "EAST": "AISLE_EAST",
    "WEST": "AISLE_WEST",
    "MEZZ": "MEZZ_TOP",
    "MEZZTOP": "MEZZ_TOP",
    "MEZZ_TOP": "MEZZ_TOP",
    "UNDERMEZZ": "UNDER_MEZZ",
    "UNDER_MEZZ": "UNDER_MEZZ",
    "NW": "NW_DECK",
    "NWDECK": "NW_DECK",
    "UNDERNW": "UNDER_NW_DECK",
    "COLUMNS": "COLUMN_ROW",
    "COLUMN": "COLUMN_ROW",
    "PAD": "PAD",
}


def sync_map(tello: SimulatedTello, speed: float = 1.1) -> list[Obstacle]:
    room = build_room()
    d = tello.drone
    d.obstacles = room.obstacles
    d.room_xmin, d.room_xmax = room.xmin, room.xmax
    d.room_ymin, d.room_ymax = room.ymin, room.ymax
    d.room_height = room.zmax
    d.move_speed = speed
    return room.obstacles


def normalize_name(text: str) -> str:
    s = text.strip().upper()
    s = s.replace("-", " ").replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s.replace(" ", "_")


def resolve_zone(name: str, zones: list[Zone]) -> Zone:
    raw = name.strip()
    if not raw:
        raise ValueError("Empty zone name")
    key = normalize_name(raw)
    compact = key.replace("_", "")

    by_id = {z.id.upper(): z for z in zones}
    by_label = {normalize_name(z.label): z for z in zones}

    if key in ALIASES and ALIASES[key] in by_id:
        return by_id[ALIASES[key]]
    if compact in ALIASES and ALIASES[compact] in by_id:
        return by_id[ALIASES[compact]]
    if key in by_id:
        return by_id[key]
    if key in by_label:
        return by_label[key]
    for z in zones:
        if key in z.id.upper() or compact in z.id.upper().replace("_", ""):
            return z
        if key in normalize_name(z.label):
            return z
    known = ", ".join(z.id for z in zones)
    raise ValueError(f"Unknown zone '{name}'. Known: {known}")


def collides(
    x: float,
    y: float,
    z: float,
    obstacles: list[Obstacle],
    r: float = 0.18,
) -> bool:
    for o in obstacles:
        if o.name.startswith("wall_"):
            # Treat walls as solid (expanded already in AABB)
            if o.overlaps_sphere(x, y, z, r):
                return True
            continue
        # One-way tops: center at/above top is free for that obstacle
        if (
            z >= o.zmax - 0.02
            and o.xmin - r * 0.35 <= x <= o.xmax + r * 0.35
            and o.ymin - r * 0.35 <= y <= o.ymax + r * 0.35
        ):
            continue
        if o.overlaps_sphere(x, y, z, r):
            return True
    return False


def safe_target(zone: Zone, obstacles: list[Obstacle]) -> Vec3:
    hx, hy, hz = zone.hover_point
    if not collides(hx, hy, hz, obstacles):
        return (hx, hy, hz)

    z_try = min(
        zone.zmax - 0.15,
        max(hz, find_support(hx, hy, obstacles, z_ref=hz + 1.0)[0] + 0.35),
    )
    z_try = max(zone.zmin + 0.2, z_try)
    if zone.contains(hx, hy, z_try) and not collides(hx, hy, z_try, obstacles):
        return (hx, hy, z_try)

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
                if collides(x, y, z, obstacles):
                    continue
                d = math.dist((x, y, z), (hx, hy, hz))
                if d < best_d:
                    best_d = d
                    best = (x, y, z)
    if best is None:
        raise RuntimeError(f"No free hover found inside zone {zone.id}")
    return best


def ordered_zones(zones: list[Zone]) -> list[Zone]:
    by_id = {z.id: z for z in zones}
    out: list[Zone] = []
    for zid in PREFERRED_ORDER:
        if zid in by_id:
            out.append(by_id.pop(zid))
    out.extend(sorted(by_id.values(), key=lambda z: z.id))
    return out


def _bounds(obstacles: list[Obstacle]) -> tuple[float, float, float, float, float]:
    room = build_room()
    return (room.xmin + 0.35, room.xmax - 0.35, room.ymin + 0.35, room.ymax - 0.35, room.zmax - 0.2)


def segment_clear(
    a: Vec3,
    b: Vec3,
    obstacles: list[Obstacle],
    *,
    step: float = 0.15,
) -> bool:
    dist = math.dist(a, b)
    n = max(1, int(dist / step))
    for i in range(n + 1):
        t = i / n
        x = a[0] + (b[0] - a[0]) * t
        y = a[1] + (b[1] - a[1]) * t
        z = a[2] + (b[2] - a[2]) * t
        if collides(x, y, z, obstacles):
            return False
    return True


def _grid_key(p: Vec3, res: float) -> tuple[int, int, int]:
    return (
        int(round(p[0] / res)),
        int(round(p[1] / res)),
        int(round(p[2] / res)),
    )


def _from_key(k: tuple[int, int, int], res: float) -> Vec3:
    return (k[0] * res, k[1] * res, k[2] * res)


def shortest_path(
    start: Vec3,
    goal: Vec3,
    obstacles: list[Obstacle],
    *,
    resolution: float = 0.5,
) -> list[Vec3]:
    """Shortest collision-free path from start to goal (metres).

    Tries a straight line first; otherwise A* on a coarse 3D grid with
    string-pull smoothing. Only detours when obstacles block the line.
    """
    if math.dist(start, goal) < 0.08:
        return [goal]
    if segment_clear(start, goal, obstacles):
        return [goal]

    xmin, xmax, ymin, ymax, zmax = _bounds(obstacles)
    zmin = 0.15
    res = resolution

    def in_bounds(p: Vec3) -> bool:
        return xmin <= p[0] <= xmax and ymin <= p[1] <= ymax and zmin <= p[2] <= zmax

    def free(p: Vec3) -> bool:
        return in_bounds(p) and not collides(p[0], p[1], p[2], obstacles)

    # Snap start/goal to nearest free grid cells if needed
    def snap(p: Vec3) -> tuple[int, int, int]:
        k = _grid_key(p, res)
        if free(_from_key(k, res)):
            return k
        # search expanding shell
        for rad in range(0, 6):
            for dx in range(-rad, rad + 1):
                for dy in range(-rad, rad + 1):
                    for dz in range(-rad, rad + 1):
                        kk = (k[0] + dx, k[1] + dy, k[2] + dz)
                        if free(_from_key(kk, res)):
                            return kk
        raise RuntimeError(f"No free grid cell near {p}")

    sk = snap(start)
    gk = snap(goal)
    if sk == gk:
        return [goal]

    # 6-connected + XY diagonals (10 neighbors) — good length vs. speed
    deltas = [
        (1, 0, 0),
        (-1, 0, 0),
        (0, 1, 0),
        (0, -1, 0),
        (0, 0, 1),
        (0, 0, -1),
        (1, 1, 0),
        (1, -1, 0),
        (-1, 1, 0),
        (-1, -1, 0),
    ]

    def h(k: tuple[int, int, int]) -> float:
        p = _from_key(k, res)
        g = _from_key(gk, res)
        return math.dist(p, g)

    open_heap: list[tuple[float, tuple[int, int, int]]] = []
    heapq.heappush(open_heap, (h(sk), sk))
    came: dict[tuple[int, int, int], tuple[int, int, int] | None] = {sk: None}
    gscore: dict[tuple[int, int, int], float] = {sk: 0.0}
    closed: set[tuple[int, int, int]] = set()

    found = False
    while open_heap:
        _, cur = heapq.heappop(open_heap)
        if cur in closed:
            continue
        if cur == gk:
            found = True
            break
        closed.add(cur)
        cx, cy, cz = cur
        cp = _from_key(cur, res)
        for dx, dy, dz in deltas:
            nxt = (cx + dx, cy + dy, cz + dz)
            if nxt in closed:
                continue
            np = _from_key(nxt, res)
            if not free(np):
                continue
            # Reject diagonal corner-cutting through solids
            if dx != 0 and dy != 0:
                if not free(_from_key((cx + dx, cy, cz), res)) or not free(
                    _from_key((cx, cy + dy, cz), res)
                ):
                    continue
            tentative = gscore[cur] + math.dist(cp, np)
            if tentative < gscore.get(nxt, 1e18):
                came[nxt] = cur
                gscore[nxt] = tentative
                heapq.heappush(open_heap, (tentative + h(nxt), nxt))

    if not found:
        raise RuntimeError(
            f"No collision-free path from ({start[0]:.2f},{start[1]:.2f},{start[2]:.2f}) "
            f"to ({goal[0]:.2f},{goal[1]:.2f},{goal[2]:.2f})"
        )

    # Reconstruct grid path
    rev: list[Vec3] = []
    cur: tuple[int, int, int] | None = gk
    while cur is not None:
        rev.append(_from_key(cur, res))
        cur = came[cur]
    rev.reverse()

    # Ensure exact endpoints
    path = [start] + rev[1:-1] + [goal] if len(rev) > 1 else [start, goal]

    # String-pull smoothing
    smooth: list[Vec3] = [path[0]]
    i = 0
    while i < len(path) - 1:
        j = len(path) - 1
        while j > i + 1:
            if segment_clear(path[i], path[j], obstacles):
                break
            j -= 1
        smooth.append(path[j])
        i = j
    # Drop near-duplicates
    out: list[Vec3] = []
    for p in smooth:
        if not out or math.dist(out[-1], p) > 0.08:
            out.append(p)
    if out[-1] != goal:
        out.append(goal)
    # Commander flies each waypoint; skip the starting point (already there)
    if len(out) >= 2 and math.dist(out[0], start) < 0.12:
        out = out[1:]
    return out


def path_to_zone(
    destination: Zone,
    obstacles: list[Obstacle],
    *,
    from_zone: Zone | None = None,
    from_pos: Vec3 | None = None,
) -> tuple[Vec3, list[Vec3]]:
    """Shortest path from from_pos (or zone hover) to the destination hover."""
    target = safe_target(destination, obstacles)
    if from_pos is None:
        if from_zone is not None:
            from_pos = safe_target(from_zone, obstacles)
        else:
            from_pos = (0.0, 0.0, Z_LOW)
    path = shortest_path(from_pos, target, obstacles)
    return target, path


def go_to_zone(
    tello: SimulatedTello,
    destination: Zone,
    zones: list[Zone],
    obstacles: list[Obstacle],
    *,
    from_zone: Zone | None = None,
    log: Callable[[str], None] | None = None,
) -> Zone:
    """Fly to a zone hover along the shortest free path. Auto-takeoff if needed."""
    say = log or print
    if not tello.drone.flying:
        say("Taking off...")
        tello.takeoff()

    snap = tello.drone.snapshot()
    if from_zone is None:
        from_zone = zone_at(snap["x"], snap["y"], snap["z"], zones)

    start = (snap["x"], snap["y"], snap["z"])
    target, path = path_to_zone(
        destination,
        obstacles,
        from_zone=from_zone,
        from_pos=start,
    )
    if target != destination.hover_point:
        say(f"  (hover adjusted: {tuple(round(v, 2) for v in target)})")

    src = from_zone.id if from_zone else "?"
    direct = segment_clear(start, target, obstacles)
    mode = "direct" if direct else "shortest free-space"
    say(f"Going {src} → {destination.id}  [{mode}, {len(path)} waypoint(s)]")
    for i, (x, y, z) in enumerate(path):
        say(f"  [{i + 1}/{len(path)}] ({x:+.2f}, {y:+.2f}, {z:.2f})")
        tello.go_to_xyz(x, y, z, face=True)

    snap = tello.drone.snapshot()
    if not destination.contains(snap["x"], snap["y"], snap["z"], pad=0.45):
        here = zone_at(snap["x"], snap["y"], snap["z"], zones)
        raise RuntimeError(
            f"Did not reach {destination.id} (now in {here.id if here else '—'} "
            f"at ({snap['x']:.2f}, {snap['y']:.2f}, {snap['z']:.2f}))"
        )
    say(f"Arrived at {destination.id}.")
    return destination
