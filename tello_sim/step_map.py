"""Load PAD2.step (or similar) and extract room bounds + obstacle AABBs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# Project root (parent of tello_sim/)
_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STEP = _ROOT / "PAD2.step"


@dataclass(frozen=True)
class SolidAABB:
    name: str
    xmin: float
    xmax: float
    ymin: float
    ymax: float
    zmin: float
    zmax: float

    @property
    def dx(self) -> float:
        return self.xmax - self.xmin

    @property
    def dy(self) -> float:
        return self.ymax - self.ymin

    @property
    def dz(self) -> float:
        return self.zmax - self.zmin

    @property
    def volume(self) -> float:
        return max(0.0, self.dx) * max(0.0, self.dy) * max(0.0, self.dz)

    @property
    def kind(self) -> str:
        if self.dz <= 0.4:
            return "slab"
        if self.dz >= 3.0 and self.dx <= 2.0 and self.dy <= 2.0:
            return "column"
        if self.dx >= 15.0 and self.dy >= 8.0 and self.dz >= 3.0:
            return "enclosure"
        if self.zmin >= 4.0:
            return "ceiling"
        return "block"


@dataclass(frozen=True)
class StepMap:
    path: Path
    solids: tuple[SolidAABB, ...]
    # Flyable room (metres), axis-aligned, roughly centered
    xmin: float
    xmax: float
    ymin: float
    ymax: float
    zmin: float
    zmax: float  # underside of primary ceiling / flyable height

    @property
    def half_x(self) -> float:
        return 0.5 * (self.xmax - self.xmin)

    @property
    def half_y(self) -> float:
        return 0.5 * (self.ymax - self.ymin)

    @property
    def center(self) -> tuple[float, float, float]:
        return (
            0.5 * (self.xmin + self.xmax),
            0.5 * (self.ymin + self.ymax),
            0.0,
        )


def _parse_entities(text: str) -> dict[int, str]:
    text = text.replace("\r\n", "\n")
    entities: dict[int, str] = {}
    for m in re.finditer(r"#(\d+)\s*=\s*([^;]+);", text, re.S):
        entities[int(m.group(1))] = m.group(2).strip()
    return entities


def _parse_points(entities: dict[int, str]) -> dict[int, tuple[float, float, float]]:
    points: dict[int, tuple[float, float, float]] = {}
    for eid, body in entities.items():
        if not body.startswith("CARTESIAN_POINT"):
            continue
        m = re.search(r"\(\s*([^,]*)\s*,\s*\(\s*([^)]+)\)\s*\)", body)
        if not m:
            continue
        coords = [float(x) for x in m.group(2).split(",")]
        if len(coords) >= 3:
            # STEP from CAD is in millimetres → metres
            points[eid] = (coords[0] / 1000.0, coords[1] / 1000.0, coords[2] / 1000.0)
    return points


def _collect_points(
    root: int,
    entities: dict[int, str],
    points: dict[int, tuple[float, float, float]],
) -> list[tuple[float, float, float]]:
    ref_re = re.compile(r"#(\d+)")
    seen: set[int] = set()
    stack = [root]
    out: list[tuple[float, float, float]] = []
    while stack:
        i = stack.pop()
        if i in seen:
            continue
        seen.add(i)
        if i in points:
            out.append(points[i])
            continue
        body = entities.get(i)
        if not body:
            continue
        for r in ref_re.findall(body):
            stack.append(int(r))
    return out


def _solid_aabbs(entities: dict[int, str], points: dict[int, tuple[float, float, float]]) -> list[SolidAABB]:
    solids: list[SolidAABB] = []
    for body in entities.values():
        if not body.startswith("MANIFOLD_SOLID_BREP"):
            continue
        m = re.match(r"MANIFOLD_SOLID_BREP\('([^']*)'\s*,\s*#(\d+)\)", body)
        if not m:
            continue
        name, shell = m.group(1), int(m.group(2))
        pts = _collect_points(shell, entities, points)
        if not pts:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        zs = [p[2] for p in pts]
        solids.append(
            SolidAABB(
                name=name or f"solid_{len(solids)}",
                xmin=min(xs),
                xmax=max(xs),
                ymin=min(ys),
                ymax=max(ys),
                zmin=min(zs),
                zmax=max(zs),
            )
        )
    return solids


@lru_cache(maxsize=4)
def load_step_map(path: str | None = None) -> StepMap:
    step_path = Path(path) if path else DEFAULT_STEP
    if not step_path.is_file():
        raise FileNotFoundError(f"STEP map not found: {step_path}")

    entities = _parse_entities(step_path.read_text(encoding="utf-8", errors="replace"))
    points = _parse_points(entities)
    solids = _solid_aabbs(entities, points)
    if not solids:
        raise RuntimeError(f"No solids found in {step_path}")

    # Outer enclosure = largest volume tagged as enclosure, else largest overall
    enclosure = max(
        (s for s in solids if s.kind == "enclosure"),
        key=lambda s: s.volume,
        default=max(solids, key=lambda s: s.volume),
    )

    # Primary ceiling = lowest large slab/ceiling above ~3 m
    ceiling_candidates = [
        s
        for s in solids
        if s is not enclosure and s.dx > 10.0 and s.dy > 5.0 and s.zmin >= 3.0
    ]
    ceiling_z = min((s.zmin for s in ceiling_candidates), default=enclosure.zmax)

    return StepMap(
        path=step_path,
        solids=tuple(solids),
        xmin=enclosure.xmin,
        xmax=enclosure.xmax,
        ymin=enclosure.ymin,
        ymax=enclosure.ymax,
        zmin=max(0.0, enclosure.zmin),
        zmax=ceiling_z,
    )


def obstacle_solids(step: StepMap) -> list[SolidAABB]:
    """Solids that become collision meshes (skip hollow enclosure fill)."""
    enclosure = max(
        (s for s in step.solids if s.kind == "enclosure"),
        key=lambda s: s.volume,
        default=max(step.solids, key=lambda s: s.volume),
    )
    return [s for s in step.solids if s.name != enclosure.name]
