"""Named navigation zones for the PAD2 hall (sim + future real flights)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Sequence

from tello_sim.step_map import StepMap, load_step_map, obstacle_solids


Color = tuple[int, int, int]

# Editable zone catalog (created/updated by the zone editor)
DEFAULT_ZONES_PATH = Path(__file__).resolve().parents[1] / "zones.json"


@dataclass(frozen=True)
class Zone:
    """Axis-aligned flight chunk with a preferred hover point.

    ``origin`` (HUB) is the sim spawn / takeoff-pad point — editable in the
    zone editor independently of ``hover``.
    """

    id: str
    label: str
    xmin: float
    xmax: float
    ymin: float
    ymax: float
    zmin: float
    zmax: float
    color: Color
    priority: int = 0  # higher wins when volumes overlap
    hover: tuple[float, float, float] | None = None
    origin: tuple[float, float, float] | None = None

    def contains(self, x: float, y: float, z: float, pad: float = 0.0) -> bool:
        return (
            self.xmin - pad <= x <= self.xmax + pad
            and self.ymin - pad <= y <= self.ymax + pad
            and self.zmin - pad <= z <= self.zmax + pad
        )

    def contains_xy(self, x: float, y: float, pad: float = 0.0) -> bool:
        return (
            self.xmin - pad <= x <= self.xmax + pad
            and self.ymin - pad <= y <= self.ymax + pad
        )

    @property
    def center(self) -> tuple[float, float, float]:
        return (
            0.5 * (self.xmin + self.xmax),
            0.5 * (self.ymin + self.ymax),
            0.5 * (self.zmin + self.zmax),
        )

    @property
    def hover_point(self) -> tuple[float, float, float]:
        if self.hover is not None:
            return self.hover
        cx, cy, _ = self.center
        hz = min(max(0.9, self.zmin + 0.9), self.zmax - 0.2)
        return (cx, cy, hz)

    @property
    def origin_point(self) -> tuple[float, float, float] | None:
        if self.origin is not None:
            return self.origin
        return None


def default_hub_zone() -> Zone:
    """Sim spawn zone — origin marks where the drone starts on the floor."""
    return Zone(
        id="HUB",
        label="HUB",
        xmin=-1.2,
        xmax=1.2,
        ymin=-1.2,
        ymax=1.2,
        zmin=0.0,
        zmax=2.0,
        color=(50, 220, 160),
        priority=80,
        hover=(0.0, 0.0, 0.9),
        origin=(0.0, 0.0, 0.0),
    )


def spawn_pose(zones: Sequence[Zone] | None = None) -> tuple[float, float, float]:
    """World-frame spawn (metres) from the HUB origin point."""
    zs = list(zones) if zones is not None else build_pad2_zones()
    for z in zs:
        if z.id.upper() == "HUB":
            if z.origin is not None:
                return z.origin
            hx, hy, _ = z.hover_point
            return (hx, hy, max(0.0, z.zmin))
    return (0.0, 0.0, 0.0)


def ensure_hub(zones: Sequence[Zone]) -> list[Zone]:
    """Guarantee a HUB zone with an origin point exists (migrate older catalogs)."""
    out = list(zones)
    for i, z in enumerate(out):
        if z.id.upper() != "HUB":
            continue
        if z.origin is None:
            hx, hy, _ = z.hover_point
            out[i] = replace(z, origin=(hx, hy, max(0.0, z.zmin)))
        return out
    return [default_hub_zone(), *out]


def _solid_by_name(step: StepMap, name: str):
    for s in step.solids:
        if s.name == name:
            return s
    return None


def default_pad2_zones(step: StepMap | None = None) -> list[Zone]:
    """Built-in operational zones from PAD2 extents / named solids."""
    step = step or load_step_map()
    solids = {s.name: s for s in obstacle_solids(step)}

    c14 = solids.get("Corpo14")
    c15 = solids.get("Corpo15")
    c8 = solids.get("Corpo8")

    zones: list[Zone] = []

    # 0) Sim spawn / takeoff hub (editable origin in the zone editor)
    zones.append(default_hub_zone())

    # 1) Takeoff pad — open center (legacy nav area around the hub)
    zones.append(
        Zone(
            id="PAD",
            label="PAD",
            xmin=-2.0,
            xmax=2.0,
            ymin=-2.0,
            ymax=2.0,
            zmin=0.0,
            zmax=2.0,
            color=(40, 180, 220),
            priority=30,
            hover=(0.0, 0.0, 0.9),
        )
    )

    # 2) Column row (caution corridor)
    zones.append(
        Zone(
            id="COLUMN_ROW",
            label="COLUMNS",
            xmin=-8.0,
            xmax=8.5,
            ymin=0.35,
            ymax=1.55,
            zmin=0.0,
            zmax=step.zmax,
            color=(220, 90, 70),
            priority=40,
            hover=(0.0, -0.2, 1.2),  # hover just south of columns
        )
    )

    # 3) Open east floor (past first columns toward +X)
    zones.append(
        Zone(
            id="AISLE_EAST",
            label="EAST",
            xmin=3.4,
            xmax=step.xmax - 0.4,
            ymin=step.ymin + 0.4,
            ymax=step.ymax - 0.4,
            zmin=0.0,
            zmax=step.zmax,
            color=(80, 200, 120),
            priority=10,
            hover=(5.0, -1.0, 1.2),
        )
    )

    # 4) Open west/north residual floor (low priority fill)
    zones.append(
        Zone(
            id="AISLE_WEST",
            label="WEST",
            xmin=step.xmin + 0.4,
            xmax=-1.6,
            ymin=step.ymin + 0.4,
            ymax=step.ymax - 0.4,
            zmin=0.0,
            zmax=2.1,
            color=(70, 140, 200),
            priority=5,
            hover=(-9.0, 3.0, 1.2),
        )
    )

    if c14 is not None:
        # 5) Under mezzanine Corpo14
        zones.append(
            Zone(
                id="UNDER_MEZZ",
                label="UNDER MEZZ",
                xmin=c14.xmin,
                xmax=c14.xmax,
                ymin=c14.ymin,
                ymax=c14.ymax,
                zmin=0.0,
                zmax=c14.zmin - 0.05,
                color=(180, 140, 60),
                priority=50,
                hover=(0.5 * (c14.xmin + c14.xmax), 0.5 * (c14.ymin + c14.ymax), 1.0),
            )
        )
        # 6) Mezzanine top
        zones.append(
            Zone(
                id="MEZZ_TOP",
                label="MEZZ TOP",
                xmin=c14.xmin,
                xmax=c14.xmax,
                ymin=c14.ymin,
                ymax=c14.ymax,
                zmin=c14.zmax,
                zmax=min(c14.zmax + 1.2, step.zmax),
                color=(230, 180, 70),
                priority=55,
                hover=(
                    0.5 * (c14.xmin + c14.xmax),
                    0.5 * (c14.ymin + c14.ymax),
                    c14.zmax + 0.6,
                ),
            )
        )

    if c15 is not None:
        zones.append(
            Zone(
                id="UNDER_NW_DECK",
                label="UNDER NW",
                xmin=c15.xmin,
                xmax=c15.xmax,
                ymin=c15.ymin,
                ymax=c15.ymax,
                zmin=0.0,
                zmax=c15.zmin - 0.05,
                color=(140, 100, 180),
                priority=45,
                hover=(0.5 * (c15.xmin + c15.xmax), 0.5 * (c15.ymin + c15.ymax), 1.2),
            )
        )
        zones.append(
            Zone(
                id="NW_DECK",
                label="NW DECK",
                xmin=c15.xmin,
                xmax=c15.xmax,
                ymin=c15.ymin,
                ymax=c15.ymax,
                zmin=c15.zmax,
                zmax=min(c15.zmax + 1.0, step.zmax),
                color=(190, 120, 220),
                priority=52,
                hover=(
                    0.5 * (c15.xmin + c15.xmax),
                    0.5 * (c15.ymin + c15.ymax),
                    c15.zmax + 0.5,
                ),
            )
        )

    if c8 is not None:
        # Keep-out band around the south-west ground block.
        # Hover sits in the east margin (inside the zone, outside the solid).
        zones.append(
            Zone(
                id="SOUTH_BLOCK",
                label="S BLOCK",
                xmin=c8.xmin - 0.3,
                xmax=c8.xmax + 0.35,
                ymin=c8.ymin - 0.3,
                ymax=c8.ymax + 0.35,
                zmin=0.0,
                zmax=c8.zmax + 0.4,
                color=(200, 70, 90),
                priority=60,
                hover=(c8.xmax + 0.18, 0.5 * (c8.ymin + c8.ymax), 1.4),
            )
        )

    # 7) North open (beyond column row)
    zones.append(
        Zone(
            id="NORTH_OPEN",
            label="NORTH",
            xmin=step.xmin + 0.4,
            xmax=step.xmax - 0.4,
            ymin=1.6,
            ymax=step.ymax - 0.4,
            zmin=0.0,
            zmax=step.zmax,
            color=(60, 170, 160),
            priority=8,
            hover=(0.0, 3.5, 1.2),
        )
    )

    return zones


def zone_to_dict(zone: Zone) -> dict:
    data = asdict(zone)
    data["color"] = list(zone.color)
    if zone.hover is not None:
        data["hover"] = list(zone.hover)
    else:
        data.pop("hover", None)
    if zone.origin is not None:
        data["origin"] = list(zone.origin)
    else:
        data.pop("origin", None)
    return data


def zone_from_dict(data: dict) -> Zone:
    hover = data.get("hover")
    origin = data.get("origin")
    return Zone(
        id=str(data["id"]),
        label=str(data.get("label", data["id"])),
        xmin=float(data["xmin"]),
        xmax=float(data["xmax"]),
        ymin=float(data["ymin"]),
        ymax=float(data["ymax"]),
        zmin=float(data["zmin"]),
        zmax=float(data["zmax"]),
        color=tuple(int(c) for c in data.get("color", (120, 120, 120))),  # type: ignore[arg-type]
        priority=int(data.get("priority", 0)),
        hover=tuple(float(v) for v in hover) if hover is not None else None,  # type: ignore[arg-type]
        origin=tuple(float(v) for v in origin) if origin is not None else None,  # type: ignore[arg-type]
    )


def save_zones(zones: Sequence[Zone], path: Path | str | None = None) -> Path:
    """Write zones to JSON for the sim / real robot to share."""
    out = Path(path) if path else DEFAULT_ZONES_PATH
    payload = {
        "version": 1,
        "zones": [zone_to_dict(z) for z in ensure_hub(zones)],
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out


def load_zones(path: Path | str | None = None) -> list[Zone] | None:
    """Load zones from JSON, or None if the file is missing."""
    src = Path(path) if path else DEFAULT_ZONES_PATH
    if not src.is_file():
        return None
    payload = json.loads(src.read_text(encoding="utf-8"))
    return ensure_hub([zone_from_dict(z) for z in payload.get("zones", [])])


def build_pad2_zones(
    step: StepMap | None = None,
    path: Path | str | None = None,
) -> list[Zone]:
    """Load editable zones.json if present, otherwise built-in PAD2 defaults."""
    loaded = load_zones(path)
    if loaded:
        return loaded
    return ensure_hub(default_pad2_zones(step))


def update_zone_bounds(
    zone: Zone,
    *,
    xmin: float | None = None,
    xmax: float | None = None,
    ymin: float | None = None,
    ymax: float | None = None,
    zmin: float | None = None,
    zmax: float | None = None,
    clamp_hover: bool = True,
) -> Zone:
    """Return a copy with updated AABB; optionally keep hover/origin inside the box."""
    x0 = zone.xmin if xmin is None else xmin
    x1 = zone.xmax if xmax is None else xmax
    y0 = zone.ymin if ymin is None else ymin
    y1 = zone.ymax if ymax is None else ymax
    z0 = zone.zmin if zmin is None else zmin
    z1 = zone.zmax if zmax is None else zmax
    if x0 > x1:
        x0, x1 = x1, x0
    if y0 > y1:
        y0, y1 = y1, y0
    if z0 > z1:
        z0, z1 = z1, z0
    hover = zone.hover
    origin = zone.origin
    if clamp_hover:
        hx = 0.5 * (x0 + x1)
        hy = 0.5 * (y0 + y1)
        hz = min(max(z0 + 0.5, (hover[2] if hover else z0 + 0.9)), z1 - 0.05)
        if hover is not None:
            hx = min(max(hover[0], x0 + 0.05), x1 - 0.05)
            hy = min(max(hover[1], y0 + 0.05), y1 - 0.05)
        hover = (hx, hy, hz)
        if origin is not None:
            ox = min(max(origin[0], x0 + 0.05), x1 - 0.05)
            oy = min(max(origin[1], y0 + 0.05), y1 - 0.05)
            oz = min(max(origin[2], z0), z1)
            origin = (ox, oy, oz)
    return replace(
        zone,
        xmin=x0,
        xmax=x1,
        ymin=y0,
        ymax=y1,
        zmin=z0,
        zmax=z1,
        hover=hover,
        origin=origin,
    )


def zone_at(
    x: float,
    y: float,
    z: float,
    zones: Sequence[Zone],
) -> Zone | None:
    """Return the highest-priority zone containing the point."""
    hits = [zn for zn in zones if zn.contains(x, y, z)]
    if not hits:
        # Fall back to XY-only for HUD when between height bands
        hits = [zn for zn in zones if zn.contains_xy(x, y) and zn.zmin <= z <= zn.zmax + 0.5]
    if not hits:
        return None
    return max(hits, key=lambda zn: zn.priority)


def zones_by_id(zones: Sequence[Zone]) -> dict[str, Zone]:
    return {z.id: z for z in zones}
