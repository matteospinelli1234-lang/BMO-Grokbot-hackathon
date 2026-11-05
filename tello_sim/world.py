"""Indoor room geometry from PAD2.step (AABB approximation of CAD solids)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tello_sim.step_map import SolidAABB, StepMap, load_step_map, obstacle_solids
from tello_sim.zones import Zone, build_pad2_zones, spawn_pose


Vec3 = tuple[float, float, float]
Color = tuple[int, int, int]

# Keep-out / "death zone" bubble around the drone centre (metres).
# Models real-flight position imprecision — nothing may enter this radius.
SAFE_RADIUS = 0.40


@dataclass(frozen=True)
class Segment3:
    a: Vec3
    b: Vec3
    color: Color
    width: int = 1


@dataclass(frozen=True)
class Quad3:
    corners: tuple[Vec3, Vec3, Vec3, Vec3]
    color: Color


@dataclass(frozen=True)
class Obstacle:
    """Axis-aligned collision box (metres)."""

    xmin: float
    xmax: float
    ymin: float
    ymax: float
    zmin: float
    zmax: float
    name: str = "obstacle"

    def contains_point(self, x: float, y: float, z: float, margin: float = 0.0) -> bool:
        return (
            self.xmin - margin <= x <= self.xmax + margin
            and self.ymin - margin <= y <= self.ymax + margin
            and self.zmin - margin <= z <= self.zmax + margin
        )

    def overlaps_sphere(self, x: float, y: float, z: float, radius: float) -> bool:
        cx = min(max(x, self.xmin), self.xmax)
        cy = min(max(y, self.ymin), self.ymax)
        cz = min(max(z, self.zmin), self.zmax)
        dx, dy, dz = x - cx, y - cy, z - cz
        return dx * dx + dy * dy + dz * dz < radius * radius

    @property
    def center(self) -> Vec3:
        return (
            0.5 * (self.xmin + self.xmax),
            0.5 * (self.ymin + self.ymax),
            0.5 * (self.zmin + self.zmax),
        )


@dataclass
class RoomGeometry:
    segments: list[Segment3]
    quads: list[Quad3]
    obstacles: list[Obstacle]
    zones: list[Zone]
    zone_quads: list[Quad3]
    zone_segments: list[Segment3]
    xmin: float = -4.0
    xmax: float = 4.0
    ymin: float = -4.0
    ymax: float = 4.0
    zmax: float = 3.0
    source: str = "builtin"

    @property
    def half_x(self) -> float:
        return 0.5 * (self.xmax - self.xmin)

    @property
    def half_y(self) -> float:
        return 0.5 * (self.ymax - self.ymin)


KIND_COLORS: dict[str, tuple[Color, Color]] = {
    # body, top
    "column": ((110, 112, 118), (130, 132, 138)),
    "slab": ((150, 120, 75), (175, 145, 95)),
    "block": ((95, 100, 110), (115, 120, 130)),
    "ceiling": ((70, 74, 82), (88, 92, 100)),
    "enclosure": ((80, 84, 90), (80, 84, 90)),
    "wall": ((85, 88, 96), (95, 98, 106)),
}


def _box_quads(o: Obstacle, color: Color, top_color: Color | None = None) -> list[Quad3]:
    x0, x1 = o.xmin, o.xmax
    y0, y1 = o.ymin, o.ymax
    z0, z1 = o.zmin, o.zmax
    tc = top_color or color
    c_bot = tuple(max(0, c - 25) for c in color)  # type: ignore[misc]
    c_xp = tuple(min(255, c + 12) for c in color)  # type: ignore[misc]
    c_xn = tuple(max(0, c - 12) for c in color)  # type: ignore[misc]
    c_yp = tuple(min(255, c + 6) for c in color)  # type: ignore[misc]
    c_yn = tuple(max(0, c - 18) for c in color)  # type: ignore[misc]
    return [
        Quad3(((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)), tc),
        Quad3(((x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)), c_bot),
        Quad3(((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)), c_xp),
        Quad3(((x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)), c_xn),
        Quad3(((x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)), c_yp),
        Quad3(((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)), c_yn),
    ]


def _box_edges(o: Obstacle, color: Color = (30, 32, 38), width: int = 1) -> list[Segment3]:
    x0, x1 = o.xmin, o.xmax
    y0, y1 = o.ymin, o.ymax
    z0, z1 = o.zmin, o.zmax
    corners = [
        (x0, y0, z0),
        (x1, y0, z0),
        (x1, y1, z0),
        (x0, y1, z0),
        (x0, y0, z1),
        (x1, y0, z1),
        (x1, y1, z1),
        (x0, y1, z1),
    ]
    edges = [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 0),
        (4, 5),
        (5, 6),
        (6, 7),
        (7, 4),
        (0, 4),
        (1, 5),
        (2, 6),
        (3, 7),
    ]
    return [Segment3(corners[a], corners[b], color, width) for a, b in edges]


def _add_box(
    obstacles: list[Obstacle],
    quads: list[Quad3],
    segments: list[Segment3],
    *,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    zmin: float,
    zmax: float,
    color: Color,
    top_color: Color | None = None,
    name: str,
) -> Obstacle:
    obs = Obstacle(xmin, xmax, ymin, ymax, zmin, zmax, name=name)
    obstacles.append(obs)
    quads.extend(_box_quads(obs, color, top_color))
    segments.extend(_box_edges(obs))
    return obs


def _add_solid(
    solid: SolidAABB,
    obstacles: list[Obstacle],
    quads: list[Quad3],
    segments: list[Segment3],
) -> None:
    body, top = KIND_COLORS.get(solid.kind, KIND_COLORS["block"])
    zmin = max(0.0, solid.zmin)  # ignore buried footings below floor
    if solid.zmax <= 0.02:
        return
    _add_box(
        obstacles,
        quads,
        segments,
        xmin=solid.xmin,
        xmax=solid.xmax,
        ymin=solid.ymin,
        ymax=solid.ymax,
        zmin=zmin,
        zmax=solid.zmax,
        color=body,
        top_color=top,
        name=solid.name,
    )


def _floor_and_shell(
    step: StepMap,
    quads: list[Quad3],
    segments: list[Segment3],
    obstacles: list[Obstacle],
) -> None:
    x0, x1 = step.xmin, step.xmax
    y0, y1 = step.ymin, step.ymax
    z_ceil = step.zmax
    floor_a = (48, 52, 58)
    floor_b = (58, 62, 70)
    wall_c = (90, 96, 108)
    accent = (210, 140, 60)

    # Floor grid (2 m tiles — room is ~24×12 m)
    step_sz = 2.0
    x = x0
    while x < x1 - 1e-6:
        y = y0
        x_end = min(x + step_sz, x1)
        while y < y1 - 1e-6:
            y_end = min(y + step_sz, y1)
            shade = floor_a if (int((x - x0) / step_sz) + int((y - y0) / step_sz)) % 2 == 0 else floor_b
            quads.append(
                Quad3(
                    ((x, y, 0.0), (x_end, y, 0.0), (x_end, y_end, 0.0), (x, y_end, 0.0)),
                    shade,
                )
            )
            y += step_sz
        x += step_sz

    # Perimeter outline
    for ya in (y0, y1):
        segments.append(Segment3((x0, ya, 0.0), (x1, ya, 0.0), wall_c, 2))
        segments.append(Segment3((x0, ya, z_ceil), (x1, ya, z_ceil), wall_c, 1))
    for xa in (x0, x1):
        segments.append(Segment3((xa, y0, 0.0), (xa, y1, 0.0), wall_c, 2))
        segments.append(Segment3((xa, y0, z_ceil), (xa, y1, z_ceil), wall_c, 1))
    for xa in (x0, x1):
        for ya in (y0, y1):
            segments.append(Segment3((xa, ya, 0.0), (xa, ya, z_ceil), wall_c, 2))

    # Thin physical walls (keep drone inside)
    t = 0.2
    wall_body, wall_top = KIND_COLORS["wall"]
    _add_box(
        obstacles, quads, segments,
        xmin=x0, xmax=x0 + t, ymin=y0, ymax=y1, zmin=0.0, zmax=z_ceil,
        color=wall_body, top_color=wall_top, name="wall_west",
    )
    _add_box(
        obstacles, quads, segments,
        xmin=x1 - t, xmax=x1, ymin=y0, ymax=y1, zmin=0.0, zmax=z_ceil,
        color=wall_body, top_color=wall_top, name="wall_east",
    )
    _add_box(
        obstacles, quads, segments,
        xmin=x0 + t, xmax=x1 - t, ymin=y0, ymax=y0 + t, zmin=0.0, zmax=z_ceil,
        color=wall_body, top_color=wall_top, name="wall_south",
    )
    _add_box(
        obstacles, quads, segments,
        xmin=x0 + t, xmax=x1 - t, ymin=y1 - t, ymax=y1, zmin=0.0, zmax=z_ceil,
        color=wall_body, top_color=wall_top, name="wall_north",
    )

    # Compass mark on the north wall (pad marker is drawn at HUB.origin in build_room)
    segments.append(Segment3((-0.7, y1, 1.5), (0.7, y1, 1.5), (70, 200, 120), 3))
    segments.append(Segment3((0.0, y1, 1.1), (0.0, y1, 1.9), (70, 200, 120), 3))
    segments.append(Segment3((x0 + 0.5, y0 + 0.5, 0.05), (x0 + 1.5, y0 + 0.5, 0.05), accent, 2))


def _zone_viz(zones: list[Zone]) -> tuple[list[Quad3], list[Segment3]]:
    """Floor tints, borders, and short corner posts for each zone."""
    quads: list[Quad3] = []
    segments: list[Segment3] = []
    for zn in sorted(zones, key=lambda z: z.priority):
        # Tint at zone floor band (slightly above surface so it reads on platforms too)
        z_floor = zn.zmin + 0.04
        # Dim the fill so the map stays readable
        fill = tuple(max(20, int(c * 0.45)) for c in zn.color)  # type: ignore[misc]
        quads.append(
            Quad3(
                (
                    (zn.xmin, zn.ymin, z_floor),
                    (zn.xmax, zn.ymin, z_floor),
                    (zn.xmax, zn.ymax, z_floor),
                    (zn.xmin, zn.ymax, z_floor),
                ),
                fill,  # type: ignore[arg-type]
            )
        )
        # Perimeter at floor + top of height band
        for z in (z_floor, min(zn.zmax, zn.zmin + 1.8)):
            segments.append(Segment3((zn.xmin, zn.ymin, z), (zn.xmax, zn.ymin, z), zn.color, 2))
            segments.append(Segment3((zn.xmax, zn.ymin, z), (zn.xmax, zn.ymax, z), zn.color, 2))
            segments.append(Segment3((zn.xmax, zn.ymax, z), (zn.xmin, zn.ymax, z), zn.color, 2))
            segments.append(Segment3((zn.xmin, zn.ymax, z), (zn.xmin, zn.ymin, z), zn.color, 2))
        # Corner posts
        post_h = min(1.2, max(0.4, zn.zmax - zn.zmin))
        for x, y in (
            (zn.xmin, zn.ymin),
            (zn.xmax, zn.ymin),
            (zn.xmax, zn.ymax),
            (zn.xmin, zn.ymax),
        ):
            segments.append(
                Segment3((x, y, z_floor), (x, y, z_floor + post_h), zn.color, 2)
            )
        # Hover marker (small cross)
        hx, hy, hz = zn.hover_point
        segments.append(Segment3((hx - 0.25, hy, hz), (hx + 0.25, hy, hz), zn.color, 3))
        segments.append(Segment3((hx, hy - 0.25, hz), (hx, hy + 0.25, hz), zn.color, 3))
        # Origin / spawn marker (HUB) — diamond on the floor
        if zn.origin is not None:
            ox, oy, oz = zn.origin
            z = max(0.03, oz + 0.03)
            c = (255, 120, 40)
            segments.append(Segment3((ox, oy - 0.35, z), (ox + 0.35, oy, z), c, 3))
            segments.append(Segment3((ox + 0.35, oy, z), (ox, oy + 0.35, z), c, 3))
            segments.append(Segment3((ox, oy + 0.35, z), (ox - 0.35, oy, z), c, 3))
            segments.append(Segment3((ox - 0.35, oy, z), (ox, oy - 0.35, z), c, 3))
    return quads, segments


def _add_pad_mezz_props(
    zones: list[Zone],
    z_ceil: float,
    obstacles: list[Obstacle],
    quads: list[Quad3],
    segments: list[Segment3],
) -> None:
    """Extra scenario solids: wall on PAD↔UNDER_MEZZ + 1×1 column into ZONE_7."""
    by_id = {z.id.upper(): z for z in zones}
    pad = by_id.get("PAD")
    mezz = by_id.get("UNDER_MEZZ")
    hub = by_id.get("HUB")
    zone7 = by_id.get("ZONE_7")
    wall_body, wall_top = KIND_COLORS["wall"]
    block_body, block_top = KIND_COLORS["block"]

    if pad is not None and mezz is not None:
        # Shared vertical border (PAD is east of UNDER_MEZZ in the catalog)
        wall_x = 0.5 * (mezz.xmax + pad.xmin)
        y0 = max(pad.ymin, mezz.ymin)
        y1 = min(pad.ymax, mezz.ymax)
        if y1 - y0 < 0.4:
            y0 = min(pad.ymin, mezz.ymin)
            y1 = max(pad.ymax, mezz.ymax)
        thickness = 0.15
        _add_box(
            obstacles,
            quads,
            segments,
            xmin=wall_x - 0.5 * thickness,
            xmax=wall_x + 0.5 * thickness,
            ymin=y0,
            ymax=y1,
            zmin=0.0,
            zmax=z_ceil,
            color=wall_body,
            top_color=wall_top,
            name="wall_pad_under_mezz",
        )

    # 1×1 m column to ceiling at the ZONE_7 ↔ HUB ↔ UNDER_MEZZ corner, inside ZONE_7
    half = 0.5
    if zone7 is None:
        return
    # SE corner of ZONE_7 is where it meets HUB (east) and UNDER_MEZZ (south)
    cx = zone7.xmax - half
    cy = zone7.ymin + half
    # Nudge fully inside if HUB/UNDER_MEZZ edges sit slightly inside ZONE_7
    if hub is not None:
        cx = min(cx, hub.xmin - half)
    if mezz is not None:
        cy = max(cy, mezz.ymax + half)
    # Keep the whole 1×1 footprint inside ZONE_7
    cx = min(max(cx, zone7.xmin + half), zone7.xmax - half)
    cy = min(max(cy, zone7.ymin + half), zone7.ymax - half)
    _add_box(
        obstacles,
        quads,
        segments,
        xmin=cx - half,
        xmax=cx + half,
        ymin=cy - half,
        ymax=cy + half,
        zmin=0.0,
        zmax=z_ceil,
        color=block_body,
        top_color=block_top,
        name="pillar_1x1_ceil",
    )


def build_room(step_path: str | Path | None = None) -> RoomGeometry:
    """Build the simulator map from PAD2.step (AABB solids + walls + zones)."""
    step = load_step_map(str(step_path) if step_path else None)
    segments: list[Segment3] = []
    quads: list[Quad3] = []
    obstacles: list[Obstacle] = []

    _floor_and_shell(step, quads, segments, obstacles)

    for solid in obstacle_solids(step):
        # Skip roof volumes far above the primary ceiling (keep primary ceiling slab)
        if solid.zmin > step.zmax + 0.5:
            continue
        _add_solid(solid, obstacles, quads, segments)

    zones = build_pad2_zones(step)
    _add_pad_mezz_props(zones, step.zmax, obstacles, quads, segments)
    # Takeoff pad crosshair follows HUB.origin
    ox, oy, oz = spawn_pose(zones)
    pad_c = (80, 180, 220)
    zpad = max(0.02, oz + 0.02)
    segments.append(Segment3((ox - 0.5, oy, zpad), (ox + 0.5, oy, zpad), pad_c, 3))
    segments.append(Segment3((ox, oy - 0.5, zpad), (ox, oy + 0.5, zpad), pad_c, 3))
    zone_quads, zone_segments = _zone_viz(zones)

    return RoomGeometry(
        segments=segments,
        quads=quads,
        obstacles=obstacles,
        zones=zones,
        zone_quads=zone_quads,
        zone_segments=zone_segments,
        xmin=step.xmin,
        xmax=step.xmax,
        ymin=step.ymin,
        ymax=step.ymax,
        zmax=step.zmax,
        source=str(step.path.name),
    )


def find_support(
    x: float,
    y: float,
    obstacles: list[Obstacle],
    z_ref: float | None = None,
    pad: float = 0.05,
) -> tuple[float, Obstacle | None]:
    """Highest landable top under (x, y) at or below z_ref (default: floor).

    Ceilings / upper slabs above the craft are ignored so they are not treated
    as the ground. Pass the drone's current z as z_ref when flying or perched.
    """
    best_z = 0.0
    best: Obstacle | None = None
    z_cap = (z_ref + 0.08) if z_ref is not None else 0.08
    for o in obstacles:
        # Walls are not landable tops for perching in the middle of the room
        if o.name.startswith("wall_"):
            continue
        if o.zmax > z_cap:
            continue
        if o.xmin - pad <= x <= o.xmax + pad and o.ymin - pad <= y <= o.ymax + pad:
            if o.zmax >= best_z:
                best_z = o.zmax
                best = o
    return best_z, best


def _on_top_of(o: Obstacle, x: float, y: float, z: float, radius: float) -> bool:
    if o.name.startswith("wall_"):
        return False
    pad = radius * 0.35
    if not (o.xmin - pad <= x <= o.xmax + pad and o.ymin - pad <= y <= o.ymax + pad):
        return False
    return z >= o.zmax - 0.02


def resolve_sphere_collision(
    x: float,
    y: float,
    z: float,
    radius: float,
    obstacles: list[Obstacle],
) -> tuple[float, float, float, bool]:
    """Push a sphere out of overlapping AABBs. Tops are landable (skipped from above)."""
    hit = False
    for _ in range(4):
        moved = False
        for o in obstacles:
            if _on_top_of(o, x, y, z, radius):
                continue
            if not o.overlaps_sphere(x, y, z, radius):
                continue
            hit = True
            cx = min(max(x, o.xmin), o.xmax)
            cy = min(max(y, o.ymin), o.ymax)
            cz = min(max(z, o.zmin), o.zmax)
            dx, dy, dz = x - cx, y - cy, z - cz
            dist2 = dx * dx + dy * dy + dz * dz

            if dist2 < 1e-10:
                px_l = x - o.xmin + radius
                px_r = o.xmax - x + radius
                py_l = y - o.ymin + radius
                py_r = o.ymax - y + radius
                pz_l = z - o.zmin + radius
                pz_r = o.zmax - z + radius
                options = [
                    (px_l, (-1, 0, 0)),
                    (px_r, (1, 0, 0)),
                    (py_l, (0, -1, 0)),
                    (py_r, (0, 1, 0)),
                    (pz_l, (0, 0, -1)),
                    (pz_r, (0, 0, 1)),
                ]
                pen, normal = min(options, key=lambda t: t[0])
                x += normal[0] * pen
                y += normal[1] * pen
                z += normal[2] * pen
            else:
                dist = dist2**0.5
                push = (radius - dist) / dist
                x += dx * push
                y += dy * push
                z += dz * push
            moved = True
        if not moved:
            break
    return x, y, z, hit
