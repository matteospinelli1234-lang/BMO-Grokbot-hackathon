"""Interactive 3D zone editor for PAD2 navigation rectangles."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import pygame

from tello_sim.render import Camera, Renderer, project_point
from tello_sim.world import Segment3, build_room, _zone_viz
from tello_sim.zones import (
    DEFAULT_ZONES_PATH,
    Zone,
    build_pad2_zones,
    default_pad2_zones,
    save_zones,
    update_zone_bounds,
)


Handle = str  # "c0".."c3" corners, or "hover"


def _ray_hit_z_plane(
    mx: int,
    my: int,
    cam: Camera,
    width: int,
    height: int,
    plane_z: float,
) -> tuple[float, float] | None:
    """Intersect a camera ray through a screen pixel with z = plane_z."""
    right, up, forward = cam.look_basis()
    f = 0.5 * height / math.tan(math.radians(cam.fov_deg) * 0.5)
    ndc_x = (mx - width * 0.5) / f
    ndc_y = -(my - height * 0.5) / f
    dx = ndc_x * right[0] + ndc_y * up[0] + forward[0]
    dy = ndc_x * right[1] + ndc_y * up[1] + forward[1]
    dz = ndc_x * right[2] + ndc_y * up[2] + forward[2]
    if abs(dz) < 1e-6:
        return None
    ex, ey, ez = cam.eye
    t = (plane_z - ez) / dz
    if t < cam.near:
        return None
    return (ex + t * dx, ey + t * dy)


def _corner_world(zone: Zone, idx: int) -> tuple[float, float, float]:
    z = zone.zmin + 0.05
    corners = (
        (zone.xmin, zone.ymin, z),
        (zone.xmax, zone.ymin, z),
        (zone.xmax, zone.ymax, z),
        (zone.xmin, zone.ymax, z),
    )
    return corners[idx]


def _replace_zone(zones: list[Zone], index: int, zone: Zone) -> None:
    zones[index] = zone


class ZoneEditor:
    def __init__(self, width: int = 1400, height: int = 860, path: Path | None = None) -> None:
        self.width = width
        self.height = height
        self.path = path or DEFAULT_ZONES_PATH
        self.room = build_room()
        self.zones: list[Zone] = list(build_pad2_zones())
        self.selected = 0
        self.show_map = True
        self.dirty = False
        self.status = f"Loaded {len(self.zones)} zones — drag corners, S save"
        self.orbit = 40.0
        self.orbit_pitch = 55.0  # degrees from horizontal-ish via eye height
        self.orbit_dist = 18.0
        self.look_at = (0.0, 0.0, 1.0)
        self.dragging: Handle | None = None
        self._font: pygame.font.Font | None = None
        self._font_sm: pygame.font.Font | None = None
        self._rebuild_viz()

    def _rebuild_viz(self) -> None:
        self.zone_quads, self.zone_segments = _zone_viz(self.zones)

    def _camera(self) -> Camera:
        a = math.radians(self.orbit)
        # Elevated orbit around look_at
        eye = (
            self.look_at[0] + self.orbit_dist * math.sin(a),
            self.look_at[1] - self.orbit_dist * math.cos(a),
            self.look_at[2] + self.orbit_dist * 0.55,
        )
        return Camera(eye, self.look_at, fov_deg=55.0)

    def _ensure_fonts(self) -> None:
        if self._font is None:
            self._font = pygame.font.SysFont("dejavusans", 20)
            self._font_sm = pygame.font.SysFont("dejavusans", 15)

    def _sel(self) -> Zone | None:
        if not self.zones:
            return None
        self.selected %= len(self.zones)
        return self.zones[self.selected]

    def _pick_zone(self, mx: int, my: int, cam: Camera) -> int | None:
        best_i = None
        best_d = 40.0
        for i, zn in enumerate(self.zones):
            hx, hy, hz = zn.hover_point
            pr = project_point((hx, hy, hz), cam, self.width, self.height)
            if pr is None:
                continue
            d = math.hypot(pr[0] - mx, pr[1] - my)
            if d < best_d:
                best_d = d
                best_i = i
        return best_i

    def _pick_handle(self, mx: int, my: int, cam: Camera) -> Handle | None:
        zn = self._sel()
        if zn is None:
            return None
        best: Handle | None = None
        best_d = 16.0
        for i in range(4):
            pr = project_point(_corner_world(zn, i), cam, self.width, self.height)
            if pr is None:
                continue
            d = math.hypot(pr[0] - mx, pr[1] - my)
            if d < best_d:
                best_d = d
                best = f"c{i}"
        hx, hy, hz = zn.hover_point
        pr = project_point((hx, hy, hz), cam, self.width, self.height)
        if pr is not None:
            d = math.hypot(pr[0] - mx, pr[1] - my)
            if d < best_d:
                best = "hover"
        return best

    def _apply_corner_drag(self, idx: int, wx: float, wy: float) -> None:
        zn = self._sel()
        if zn is None:
            return
        kwargs: dict = {}
        if idx == 0:
            kwargs = {"xmin": wx, "ymin": wy}
        elif idx == 1:
            kwargs = {"xmax": wx, "ymin": wy}
        elif idx == 2:
            kwargs = {"xmax": wx, "ymax": wy}
        else:
            kwargs = {"xmin": wx, "ymax": wy}
        _replace_zone(self.zones, self.selected, update_zone_bounds(zn, **kwargs))
        self.dirty = True
        self._rebuild_viz()

    def _apply_hover_drag(self, wx: float, wy: float) -> None:
        zn = self._sel()
        if zn is None:
            return
        hx = min(max(wx, zn.xmin + 0.05), zn.xmax - 0.05)
        hy = min(max(wy, zn.ymin + 0.05), zn.ymax - 0.05)
        hz = zn.hover_point[2]
        _replace_zone(
            self.zones,
            self.selected,
            Zone(
                id=zn.id,
                label=zn.label,
                xmin=zn.xmin,
                xmax=zn.xmax,
                ymin=zn.ymin,
                ymax=zn.ymax,
                zmin=zn.zmin,
                zmax=zn.zmax,
                color=zn.color,
                priority=zn.priority,
                hover=(hx, hy, hz),
            ),
        )
        self.dirty = True
        self._rebuild_viz()

    def _nudge_z(self, which: str, delta: float) -> None:
        zn = self._sel()
        if zn is None:
            return
        if which == "zmin":
            new = update_zone_bounds(zn, zmin=zn.zmin + delta)
        else:
            new = update_zone_bounds(zn, zmax=zn.zmax + delta)
        _replace_zone(self.zones, self.selected, new)
        self.dirty = True
        self._rebuild_viz()
        self.status = f"{new.id}: z=[{new.zmin:.2f}, {new.zmax:.2f}]"

    def _draw(
        self,
        screen: pygame.Surface,
        cam: Camera,
        renderer: Renderer,
    ) -> None:
        self._ensure_fonts()
        assert self._font and self._font_sm
        main = pygame.Surface((self.width, self.height))
        main.fill((24, 26, 32))
        if self.show_map:
            renderer._draw_quads(main, cam, self.room.quads)
            renderer._draw_segments(main, cam, self.room.segments)
        renderer._draw_quads(main, cam, self.zone_quads)
        renderer._draw_segments(main, cam, self.zone_segments)

        # Emphasize selected zone + handles
        zn = self._sel()
        if zn is not None:
            bright = [
                Segment3((zn.xmin, zn.ymin, zn.zmin + 0.06), (zn.xmax, zn.ymin, zn.zmin + 0.06), (255, 255, 255), 3),
                Segment3((zn.xmax, zn.ymin, zn.zmin + 0.06), (zn.xmax, zn.ymax, zn.zmin + 0.06), (255, 255, 255), 3),
                Segment3((zn.xmax, zn.ymax, zn.zmin + 0.06), (zn.xmin, zn.ymax, zn.zmin + 0.06), (255, 255, 255), 3),
                Segment3((zn.xmin, zn.ymax, zn.zmin + 0.06), (zn.xmin, zn.ymin, zn.zmin + 0.06), (255, 255, 255), 3),
                Segment3((zn.xmin, zn.ymin, zn.zmax), (zn.xmax, zn.ymin, zn.zmax), zn.color, 2),
                Segment3((zn.xmax, zn.ymin, zn.zmax), (zn.xmax, zn.ymax, zn.zmax), zn.color, 2),
                Segment3((zn.xmax, zn.ymax, zn.zmax), (zn.xmin, zn.ymax, zn.zmax), zn.color, 2),
                Segment3((zn.xmin, zn.ymax, zn.zmax), (zn.xmin, zn.ymin, zn.zmax), zn.color, 2),
            ]
            renderer._draw_segments(main, cam, bright)
            for i in range(4):
                pr = project_point(_corner_world(zn, i), cam, self.width, self.height)
                if pr:
                    pygame.draw.circle(main, (255, 255, 255), (pr[0], pr[1]), 7)
                    pygame.draw.circle(main, zn.color, (pr[0], pr[1]), 5)
            hx, hy, hz = zn.hover_point
            pr = project_point((hx, hy, hz), cam, self.width, self.height)
            if pr:
                pygame.draw.circle(main, (255, 220, 80), (pr[0], pr[1]), 8)
                pygame.draw.circle(main, (20, 20, 20), (pr[0], pr[1]), 3)

        # Labels
        for i, z in enumerate(self.zones):
            hx, hy, hz = z.hover_point
            pr = project_point((hx, hy, hz + 0.4), cam, self.width, self.height)
            if pr is None:
                continue
            mark = f"[{z.id}]" if i == self.selected else z.label
            img = self._font_sm.render(mark, True, (255, 255, 255) if i == self.selected else z.color)
            main.blit(img, (pr[0] - img.get_width() // 2, pr[1] - img.get_height() // 2))

        # HUD panel
        panel = [
            "ZONE EDITOR  —  PAD2",
            f"file: {self.path.name}{' *' if self.dirty else ''}",
            f"zones: {len(self.zones)}   selected: {zn.id if zn else '—'}",
        ]
        if zn:
            panel += [
                f"  XY [{zn.xmin:.2f},{zn.xmax:.2f}] × [{zn.ymin:.2f},{zn.ymax:.2f}]",
                f"  Z  [{zn.zmin:.2f},{zn.zmax:.2f}]   pri={zn.priority}",
                f"  hover ({zn.hover_point[0]:.2f}, {zn.hover_point[1]:.2f}, {zn.hover_point[2]:.2f})",
            ]
        panel += [
            "",
            self.status,
            "",
            "LMB select / drag corner or hover",
            "RMB drag orbit   Wheel zoom",
            "Tab cycle   [ ] zmin   - = zmax",
            "S save   O load defaults   M map mesh",
            "N new zone   Del delete   Esc quit",
        ]
        y = 12
        for i, line in enumerate(panel):
            col = (0, 220, 180) if i == 0 else (210, 214, 220)
            if line.startswith("file:") and self.dirty:
                col = (255, 180, 80)
            img = (self._font if i == 0 else self._font_sm).render(line, True, col)
            main.blit(img, (14, y))
            y += img.get_height() + 3

        screen.blit(main, (0, 0))

    def run(self) -> int:
        pygame.init()
        screen = pygame.display.set_mode((self.width, self.height))
        pygame.display.set_caption("Tello zones — 3D editor")
        clock = pygame.time.Clock()
        # Reuse draw helpers; map geometry only
        renderer = Renderer(self.width, self.height)
        renderer.show_zones = False

        orbiting = False
        last_mouse = (0, 0)
        running = True
        while running:
            cam = self._camera()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    running = self._on_key(event.key) and running
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    mx, my = event.pos
                    if event.button == 1:
                        handle = self._pick_handle(mx, my, cam)
                        if handle:
                            self.dragging = handle
                        else:
                            picked = self._pick_zone(mx, my, cam)
                            if picked is not None:
                                self.selected = picked
                                zn = self._sel()
                                self.status = f"Selected {zn.id}" if zn else ""
                                if zn:
                                    self.look_at = (
                                        0.5 * (zn.xmin + zn.xmax),
                                        0.5 * (zn.ymin + zn.ymax),
                                        0.5 * (zn.zmin + zn.zmax),
                                    )
                    elif event.button == 3:
                        orbiting = True
                        last_mouse = event.pos
                    elif event.button == 4:
                        self.orbit_dist = max(6.0, self.orbit_dist - 1.2)
                    elif event.button == 5:
                        self.orbit_dist = min(40.0, self.orbit_dist + 1.2)
                elif event.type == pygame.MOUSEBUTTONUP:
                    if event.button == 1:
                        self.dragging = None
                    elif event.button == 3:
                        orbiting = False
                elif event.type == pygame.MOUSEMOTION:
                    mx, my = event.pos
                    if orbiting:
                        dx = mx - last_mouse[0]
                        self.orbit = (self.orbit + dx * 0.35) % 360.0
                        last_mouse = (mx, my)
                    elif self.dragging is not None:
                        zn = self._sel()
                        if zn is not None:
                            hit = _ray_hit_z_plane(
                                mx, my, cam, self.width, self.height, zn.zmin + 0.05
                            )
                            if hit is not None:
                                wx, wy = hit
                                if self.dragging == "hover":
                                    self._apply_hover_drag(wx, wy)
                                elif self.dragging.startswith("c"):
                                    self._apply_corner_drag(int(self.dragging[1]), wx, wy)

            self._draw(screen, cam, renderer)
            pygame.display.flip()
            clock.tick(60)

        pygame.quit()
        if self.dirty:
            print("Warning: unsaved zone changes discarded.", file=sys.stderr)
        return 0

    def _on_key(self, key: int) -> bool:
        if key == pygame.K_ESCAPE:
            return False
        if key == pygame.K_TAB:
            if self.zones:
                mods = pygame.key.get_mods()
                self.selected = (self.selected - 1) % len(self.zones) if mods & pygame.KMOD_SHIFT else (self.selected + 1) % len(self.zones)
                zn = self._sel()
                if zn:
                    self.status = f"Selected {zn.id}"
                    self.look_at = (
                        0.5 * (zn.xmin + zn.xmax),
                        0.5 * (zn.ymin + zn.ymax),
                        max(0.5, 0.5 * (zn.zmin + zn.zmax)),
                    )
        elif key == pygame.K_LEFTBRACKET:
            self._nudge_z("zmin", -0.1)
        elif key == pygame.K_RIGHTBRACKET:
            self._nudge_z("zmin", 0.1)
        elif key == pygame.K_MINUS:
            self._nudge_z("zmax", -0.1)
        elif key in (pygame.K_EQUALS, pygame.K_PLUS):
            self._nudge_z("zmax", 0.1)
        elif key == pygame.K_s:
            save_zones(self.zones, self.path)
            self.dirty = False
            self.status = f"Saved {len(self.zones)} zones → {self.path}"
            print(self.status)
        elif key == pygame.K_o:
            self.zones = list(default_pad2_zones())
            self.selected = 0
            self.dirty = True
            self._rebuild_viz()
            self.status = "Loaded built-in defaults (not saved yet — press S)"
        elif key == pygame.K_m:
            self.show_map = not self.show_map
            self.status = f"Map mesh {'ON' if self.show_map else 'OFF'}"
        elif key == pygame.K_n:
            base = self._sel()
            n = len(self.zones) + 1
            new_id = f"ZONE_{n}"
            if base:
                z = update_zone_bounds(
                    Zone(
                        id=new_id,
                        label=new_id,
                        xmin=base.xmin + 0.5,
                        xmax=base.xmax + 0.5,
                        ymin=base.ymin + 0.5,
                        ymax=base.ymax + 0.5,
                        zmin=base.zmin,
                        zmax=base.zmax,
                        color=(100, 200, 255),
                        priority=base.priority,
                        hover=None,
                    )
                )
            else:
                z = Zone(
                    id=new_id,
                    label=new_id,
                    xmin=-1,
                    xmax=1,
                    ymin=-1,
                    ymax=1,
                    zmin=0,
                    zmax=2,
                    color=(100, 200, 255),
                    priority=20,
                    hover=(0, 0, 0.9),
                )
            self.zones.append(z)
            self.selected = len(self.zones) - 1
            self.dirty = True
            self._rebuild_viz()
            self.status = f"Created {new_id}"
        elif key == pygame.K_DELETE:
            if self.zones:
                gone = self.zones.pop(self.selected)
                self.selected = max(0, self.selected - 1)
                self.dirty = True
                self._rebuild_viz()
                self.status = f"Deleted {gone.id}"
        return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="3D editor for PAD2 navigation zones")
    p.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_ZONES_PATH,
        help=f"zones JSON path (default: {DEFAULT_ZONES_PATH})",
    )
    p.add_argument("--width", type=int, default=1400)
    p.add_argument("--height", type=int, default=860)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Ensure file exists so the sim and editor share one catalog
    if not args.path.is_file():
        zones = default_pad2_zones()
        save_zones(zones, args.path)
        print(f"Wrote default zones → {args.path}")
    editor = ZoneEditor(width=args.width, height=args.height, path=args.path)
    return editor.run()


if __name__ == "__main__":
    raise SystemExit(main())
