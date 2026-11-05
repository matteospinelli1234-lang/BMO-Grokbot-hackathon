"""Perspective 3D renderer: chase camera, FPV panel, HUD."""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np
import pygame

from tello_sim.drone import Drone
from tello_sim.world import Quad3, Segment3, build_room
from tello_sim.zones import Zone, zone_at


Vec3 = tuple[float, float, float]


class Camera:
    def __init__(
        self,
        eye: Vec3,
        target: Vec3,
        fov_deg: float = 60.0,
        near: float = 0.15,
    ) -> None:
        self.eye = eye
        self.target = target
        self.fov_deg = fov_deg
        self.near = near
        self.up: Vec3 = (0.0, 0.0, 1.0)

    def look_basis(self) -> tuple[Vec3, Vec3, Vec3]:
        ex, ey, ez = self.eye
        tx, ty, tz = self.target
        fx, fy, fz = tx - ex, ty - ey, tz - ez
        fl = math.sqrt(fx * fx + fy * fy + fz * fz) or 1.0
        fx, fy, fz = fx / fl, fy / fl, fz / fl
        ux, uy, uz = self.up
        # right = forward × up
        rx = fy * uz - fz * uy
        ry = fz * ux - fx * uz
        rz = fx * uy - fy * ux
        rl = math.sqrt(rx * rx + ry * ry + rz * rz) or 1.0
        rx, ry, rz = rx / rl, ry / rl, rz / rl
        # true up = right × forward
        ux = ry * fz - rz * fy
        uy = rz * fx - rx * fz
        uz = rx * fy - ry * fx
        return (rx, ry, rz), (ux, uy, uz), (fx, fy, fz)


def project_point(
    p: Vec3,
    cam: Camera,
    width: int,
    height: int,
) -> tuple[int, int, float] | None:
    """Return (sx, sy, depth) or None if behind camera."""
    right, up, forward = cam.look_basis()
    ex, ey, ez = cam.eye
    px, py, pz = p[0] - ex, p[1] - ey, p[2] - ez
    cx = px * right[0] + py * right[1] + pz * right[2]
    cy = px * up[0] + py * up[1] + pz * up[2]
    cz = px * forward[0] + py * forward[1] + pz * forward[2]
    if cz < cam.near:
        return None
    f = 0.5 * height / math.tan(math.radians(cam.fov_deg) * 0.5)
    sx = int(width * 0.5 + (cx / cz) * f)
    sy = int(height * 0.5 - (cy / cz) * f)
    return sx, sy, cz


def drone_mesh(drone: Drone) -> list[Segment3]:
    """Body-frame segments transformed into world space."""
    s = drone.snapshot()
    yaw = math.radians(s["yaw"])
    c, sn = math.cos(yaw), math.sin(yaw)
    ox, oy, oz = s["x"], s["y"], s["z"]
    prop = math.radians(s["prop_angle"])
    safe_r = float(getattr(drone, "collision_radius", 0.20))

    def xf(lx: float, ly: float, lz: float) -> Vec3:
        wx = ox + lx * c + ly * sn
        wy = oy - lx * sn + ly * c
        return (wx, wy, oz + lz)

    body = (40, 44, 52)
    accent = (0, 180, 200)
    arm = (70, 74, 82)
    prop_c = (200, 200, 210) if s["flying"] else (120, 120, 128)
    segs: list[Segment3] = []

    # Body cross
    segs.append(Segment3(xf(-0.08, 0.0, 0.02), xf(0.08, 0.0, 0.02), body, 3))
    segs.append(Segment3(xf(0.0, -0.1, 0.02), xf(0.0, 0.12, 0.02), body, 3))
    # Nose
    segs.append(Segment3(xf(0.0, 0.12, 0.02), xf(0.0, 0.16, 0.02), accent, 3))

    arms = [(-0.12, -0.12), (0.12, -0.12), (-0.12, 0.12), (0.12, 0.12)]
    for ax, ay in arms:
        segs.append(Segment3(xf(0.0, 0.0, 0.02), xf(ax, ay, 0.02), arm, 2))
        # Prop as small rotating cross
        ca, sa = math.cos(prop), math.sin(prop)
        r = 0.07
        p1 = xf(ax + r * ca, ay + r * sa, 0.04)
        p2 = xf(ax - r * ca, ay - r * sa, 0.04)
        p3 = xf(ax - r * sa, ay + r * ca, 0.04)
        p4 = xf(ax + r * sa, ay - r * ca, 0.04)
        segs.append(Segment3(p1, p2, prop_c, 1))
        segs.append(Segment3(p3, p4, prop_c, 1))

    # Landing gear
    if not s["flying"] or s["z"] < 0.15:
        segs.append(Segment3(xf(-0.06, -0.06, 0.0), xf(-0.06, -0.06, 0.02), arm, 1))
        segs.append(Segment3(xf(0.06, -0.06, 0.0), xf(0.06, -0.06, 0.02), arm, 1))
        segs.append(Segment3(xf(-0.06, 0.06, 0.0), xf(-0.06, 0.06, 0.02), arm, 1))
        segs.append(Segment3(xf(0.06, 0.06, 0.0), xf(0.06, 0.06, 0.02), arm, 1))

    # Keep-out / death-zone bubble (horizontal ring + vertical meridians)
    if s["flying"] or s.get("collided"):
        bubble = (255, 90, 70) if s.get("collided") else (255, 170, 60)
        n = 16
        ring: list[Vec3] = []
        for i in range(n):
            a = 2.0 * math.pi * i / n
            ring.append((ox + safe_r * math.cos(a), oy + safe_r * math.sin(a), oz))
        for i in range(n):
            segs.append(Segment3(ring[i], ring[(i + 1) % n], bubble, 1))
        # Two vertical circles (XZ / YZ) so the sphere reads in 3D
        for axis in ("x", "y"):
            arc: list[Vec3] = []
            for i in range(n):
                a = 2.0 * math.pi * i / n
                if axis == "x":
                    arc.append((ox + safe_r * math.cos(a), oy, oz + safe_r * math.sin(a)))
                else:
                    arc.append((ox, oy + safe_r * math.cos(a), oz + safe_r * math.sin(a)))
            for i in range(n):
                segs.append(Segment3(arc[i], arc[(i + 1) % n], bubble, 1))

    return segs


def shadow_segment(drone: Drone) -> Segment3 | None:
    s = drone.snapshot()
    if s["z"] < 0.02:
        return None
    r = 0.12
    return Segment3(
        (s["x"] - r, s["y"], 0.01),
        (s["x"] + r, s["y"], 0.01),
        (30, 32, 36),
        4,
    )


class Renderer:
    def __init__(self, width: int = 1280, height: int = 720) -> None:
        self.width = width
        self.height = height
        room = build_room()
        self.room = room
        self.segments = room.segments
        self.quads = room.quads
        self.obstacles = room.obstacles
        self.zones = room.zones
        self.zone_quads = room.zone_quads
        self.zone_segments = room.zone_segments
        self.show_zones = True
        self.orbit_mode = False
        self.orbit_angle = 35.0
        self.fpv_w = 320
        self.fpv_h = 240
        self._font: pygame.font.Font | None = None
        self._font_sm: pygame.font.Font | None = None
        self.last_fpv_bgr: np.ndarray | None = None

    def ensure_fonts(self) -> None:
        if self._font is None:
            self._font = pygame.font.SysFont("dejavusans", 20)
            self._font_sm = pygame.font.SysFont("dejavusans", 16)

    def chase_camera(self, drone: Drone) -> Camera:
        s = drone.snapshot()
        yaw = math.radians(s["yaw"])
        if self.orbit_mode:
            a = math.radians(self.orbit_angle)
            dist = 5.0
            eye = (
                s["x"] + dist * math.sin(a),
                s["y"] - dist * math.cos(a),
                s["z"] + 2.2,
            )
        else:
            # Behind and above, looking along yaw
            back = 3.2
            eye = (
                s["x"] - back * math.sin(yaw),
                s["y"] - back * math.cos(yaw),
                s["z"] + 1.6,
            )
        target = (s["x"], s["y"], s["z"] + 0.1)
        return Camera(eye, target, fov_deg=58.0)

    def fpv_camera(self, drone: Drone) -> Camera:
        s = drone.snapshot()
        yaw = math.radians(s["yaw"])
        eye = (
            s["x"] + 0.05 * math.sin(yaw),
            s["y"] + 0.05 * math.cos(yaw),
            s["z"] + 0.05,
        )
        target = (
            s["x"] + 2.0 * math.sin(yaw),
            s["y"] + 2.0 * math.cos(yaw),
            s["z"] + 0.0,
        )
        return Camera(eye, target, fov_deg=70.0)

    def _draw_quads(
        self,
        surface: pygame.Surface,
        cam: Camera,
        quads: Sequence[Quad3],
    ) -> None:
        w, h = surface.get_size()
        # Sort back-to-front by centroid depth
        items: list[tuple[float, Quad3]] = []
        for q in quads:
            cx = sum(p[0] for p in q.corners) / 4
            cy = sum(p[1] for p in q.corners) / 4
            cz = sum(p[2] for p in q.corners) / 4
            pr = project_point((cx, cy, cz), cam, w, h)
            if pr is None:
                continue
            items.append((pr[2], q))
        items.sort(key=lambda t: -t[0])
        for _, q in items:
            pts = []
            ok = True
            for corner in q.corners:
                pr = project_point(corner, cam, w, h)
                if pr is None:
                    ok = False
                    break
                pts.append((pr[0], pr[1]))
            if ok and len(pts) == 4:
                pygame.draw.polygon(surface, q.color, pts)

    def _draw_segments(
        self,
        surface: pygame.Surface,
        cam: Camera,
        segments: Sequence[Segment3],
    ) -> None:
        w, h = surface.get_size()
        drawn: list[tuple[float, Segment3, tuple[int, int], tuple[int, int]]] = []
        for seg in segments:
            pa = project_point(seg.a, cam, w, h)
            pb = project_point(seg.b, cam, w, h)
            if pa is None or pb is None:
                continue
            depth = 0.5 * (pa[2] + pb[2])
            drawn.append((depth, seg, (pa[0], pa[1]), (pb[0], pb[1])))
        drawn.sort(key=lambda t: -t[0])
        for _, seg, a, b in drawn:
            pygame.draw.line(surface, seg.color, a, b, seg.width)

    def _draw_zone_labels(self, surface: pygame.Surface, cam: Camera) -> None:
        self.ensure_fonts()
        assert self._font_sm
        w, h = surface.get_size()
        for zn in self.zones:
            hx, hy, hz = zn.hover_point
            pr = project_point((hx, hy, hz + 0.35), cam, w, h)
            if pr is None:
                continue
            sx, sy, depth = pr
            if depth < 0.5 or sx < -50 or sy < -50 or sx > w + 50 or sy > h + 50:
                continue
            # Scale label size down for far labels
            text = zn.label
            img = self._font_sm.render(text, True, zn.color)
            bg = pygame.Surface((img.get_width() + 8, img.get_height() + 4), pygame.SRCALPHA)
            bg.fill((10, 12, 16, 160))
            surface.blit(bg, (sx - bg.get_width() // 2, sy - bg.get_height() // 2))
            surface.blit(img, (sx - img.get_width() // 2, sy - img.get_height() // 2))

    def render_view(
        self,
        surface: pygame.Surface,
        cam: Camera,
        drone: Drone,
        *,
        draw_drone: bool = True,
        draw_zone_labels: bool = True,
    ) -> None:
        surface.fill((28, 30, 36))
        self._draw_quads(surface, cam, self.quads)
        if self.show_zones:
            self._draw_quads(surface, cam, self.zone_quads)
            self._draw_segments(surface, cam, self.zone_segments)
        self._draw_segments(surface, cam, self.segments)
        if self.show_zones and draw_zone_labels:
            self._draw_zone_labels(surface, cam)
        if draw_drone:
            shadow = shadow_segment(drone)
            extra = list(drone_mesh(drone))
            if shadow is not None:
                extra.insert(0, shadow)
                # also a short perpendicular for a + shadow
                s = drone.snapshot()
                r = 0.12
                extra.insert(
                    1,
                    Segment3(
                        (s["x"], s["y"] - r, 0.01),
                        (s["x"], s["y"] + r, 0.01),
                        (30, 32, 36),
                        4,
                    ),
                )
            self._draw_segments(surface, cam, extra)

    def render_fpv_surface(self, drone: Drone) -> pygame.Surface:
        surf = pygame.Surface((self.fpv_w, self.fpv_h))
        self.render_view(
            surf,
            self.fpv_camera(drone),
            drone,
            draw_drone=False,
            draw_zone_labels=False,
        )
        # Crosshair
        cx, cy = self.fpv_w // 2, self.fpv_h // 2
        pygame.draw.line(surf, (0, 220, 180), (cx - 12, cy), (cx + 12, cy), 1)
        pygame.draw.line(surf, (0, 220, 180), (cx, cy - 12), (cx, cy + 12), 1)
        pygame.draw.rect(surf, (0, 180, 200), surf.get_rect(), 2)

        # Cache BGR frame for SimulatedTello.get_frame_read()
        rgb = pygame.surfarray.array3d(surf).swapaxes(0, 1)
        self.last_fpv_bgr = rgb[:, :, ::-1].copy()
        return surf

    def draw_hud(self, surface: pygame.Surface, drone: Drone, fps: float) -> None:
        self.ensure_fonts()
        assert self._font and self._font_sm
        s = drone.snapshot()
        if s.get("collided"):
            status = "CONTACT"
        elif s["flying"]:
            status = "FLYING"
        elif s.get("perched"):
            status = "PERCHED"
        else:
            status = "GROUNDED"
        current = zone_at(s["x"], s["y"], s["z"], self.zones)
        zone_txt = current.id if current else "—"
        safe_cm = int(round(float(getattr(drone, "collision_radius", 0.20)) * 100))
        lines = [
            f"{status}  |  bat {s['battery']:.0f}%  |  map {self.room.source}",
            f"zone {zone_txt}   zones {'ON' if self.show_zones else 'OFF'} ({len(self.zones)})",
            f"pos  x={s['x']:+.2f}  y={s['y']:+.2f}  z={s['z']:.2f} m",
            f"yaw  {s['yaw']:5.1f}°   height {int(s['z'] * 100)} cm   safe {safe_cm} cm",
            f"cam  {'orbit' if self.orbit_mode else 'chase'}   {fps:.0f} FPS",
        ]
        y = 12
        for i, text in enumerate(lines):
            font = self._font if i == 0 else self._font_sm
            if i == 0 and s.get("collided"):
                color = (255, 120, 70)
            elif i == 0 and s.get("perched"):
                color = (240, 190, 80)
            elif i == 0:
                color = (0, 220, 180)
            elif i == 1 and current is not None:
                color = current.color
            else:
                color = (210, 214, 220)
            img = font.render(text, True, color)
            surface.blit(img, (14, y))
            y += img.get_height() + 4

        help_lines = [
            "Space takeoff/land   WASD move   R/F up/down   Q/E yaw",
            "C camera   Z zones   Esc quit",
        ]
        y = self.height - 48
        for text in help_lines:
            img = self._font_sm.render(text, True, (140, 148, 160))
            surface.blit(img, (14, y))
            y += 18

    def compose(
        self,
        screen: pygame.Surface,
        drone: Drone,
        fps: float,
    ) -> None:
        main = pygame.Surface((self.width, self.height))
        self.render_view(main, self.chase_camera(drone), drone, draw_drone=True)
        fpv = self.render_fpv_surface(drone)
        main.blit(fpv, (self.width - self.fpv_w - 16, 16))
        label = None
        self.ensure_fonts()
        if self._font_sm:
            label = self._font_sm.render("FPV", True, (0, 200, 180))
            main.blit(label, (self.width - self.fpv_w - 16, 16 + self.fpv_h + 4))
        self.draw_hud(main, drone, fps)
        screen.blit(main, (0, 0))
