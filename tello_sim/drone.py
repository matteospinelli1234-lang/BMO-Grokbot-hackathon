"""Simulated Tello drone state and physics."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field

from tello_sim.world import (
    SAFE_RADIUS,
    Obstacle,
    build_room,
    find_support,
    resolve_sphere_collision,
)


@dataclass
class Drone:
    """Physics body for a Tello-sized craft in an indoor room.

    Coordinates (metres): x right, y forward, z up. Yaw in degrees,
    0 looks along +y; positive yaw is clockwise from above (Tello RC).

    Tops of tables, boxes, and shelves are landable platforms.
    """

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    yaw: float = 0.0

    vx: float = 0.0
    vy: float = 0.0
    vz: float = 0.0
    yaw_rate: float = 0.0

    flying: bool = False
    battery: float = 100.0

    # Room bounds (metres) — synced from PAD2 map when the app starts
    room_xmin: float = -12.0
    room_xmax: float = 12.0
    room_ymin: float = -6.0
    room_ymax: float = 6.0
    room_height: float = 4.35
    takeoff_height: float = 0.9

    # RC inputs in [-100, 100]
    rc_lr: float = 0.0
    rc_fb: float = 0.0
    rc_ud: float = 0.0
    rc_yaw: float = 0.0

    # Scripted move target (metres / degrees); None when idle
    move_target: tuple[float, float, float] | None = None
    yaw_target: float | None = None
    move_speed: float = 0.8  # m/s for SDK moves
    yaw_speed: float = 60.0  # deg/s

    prop_angle: float = 0.0
    # Keep-out bubble (death zone) — centre may not approach solids closer than this
    collision_radius: float = SAFE_RADIUS
    collided: bool = False
    perched: bool = False  # grounded on furniture (not floor)
    _landing: bool = False
    obstacles: list[Obstacle] = field(default_factory=lambda: build_room().obstacles)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def support_z(self, z_ref: float | None = None) -> float:
        ref = self.z if z_ref is None else z_ref
        z, _ = find_support(self.x, self.y, self.obstacles, z_ref=ref)
        return z

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "x": self.x,
                "y": self.y,
                "z": self.z,
                "yaw": self.yaw,
                "flying": self.flying,
                "battery": self.battery,
                "prop_angle": self.prop_angle,
                "collided": self.collided,
                "perched": self.perched,
            }

    def set_rc(self, lr: float, fb: float, ud: float, yaw: float) -> None:
        with self._lock:
            self.rc_lr = max(-100.0, min(100.0, float(lr)))
            self.rc_fb = max(-100.0, min(100.0, float(fb)))
            self.rc_ud = max(-100.0, min(100.0, float(ud)))
            self.rc_yaw = max(-100.0, min(100.0, float(yaw)))

    def takeoff(self) -> None:
        with self._lock:
            if self.flying:
                return
            self.flying = True
            self.perched = False
            self._landing = False
            # Rise relative to current surface (floor or furniture)
            target_z = min(self.z + self.takeoff_height, self.room_height - 0.2)
            self.move_target = (self.x, self.y, target_z)
            self.yaw_target = None
            self.set_rc(0, 0, 0, 0)

    def land(self) -> None:
        with self._lock:
            if not self.flying:
                return
            support = self.support_z()
            self._landing = True
            self.move_target = (self.x, self.y, support)
            self.yaw_target = None
            self.set_rc(0, 0, 0, 0)

    def request_move_body(self, dx: float, dy: float, dz: float) -> None:
        """Move by body-frame metres (x right, y forward, z up)."""
        with self._lock:
            if not self.flying:
                return
            rad = math.radians(self.yaw)
            # body +y forward, +x right → world
            wx = self.x + dx * math.cos(rad) + dy * math.sin(rad)
            wy = self.y - dx * math.sin(rad) + dy * math.cos(rad)
            wz = self.z + dz
            self._landing = False
            self.move_target = (wx, wy, wz)
            self.set_rc(0, 0, 0, 0)

    def request_yaw(self, delta_deg: float) -> None:
        with self._lock:
            if not self.flying:
                return
            self.yaw_target = (self.yaw + delta_deg) % 360.0
            self.set_rc(0, 0, 0, 0)

    def request_yaw_absolute(self, yaw_deg: float) -> None:
        """Turn to an absolute yaw (degrees, 0 = +Y, clockwise positive)."""
        with self._lock:
            if not self.flying:
                return
            self.yaw_target = yaw_deg % 360.0
            self.set_rc(0, 0, 0, 0)

    def face_toward(self, x: float, y: float, min_dist: float = 0.08) -> bool:
        """Yaw so the nose (+body Y / FPV) points toward (x, y). Returns False if too close."""
        with self._lock:
            if not self.flying:
                return False
            dx = x - self.x
            dy = y - self.y
            if math.hypot(dx, dy) < min_dist:
                return False
            # yaw 0 → +Y, clockwise positive → atan2(dx, dy)
            self.yaw_target = math.degrees(math.atan2(dx, dy)) % 360.0
            self.set_rc(0, 0, 0, 0)
            return True

    def cancel_motion(self) -> None:
        """Abort scripted move / yaw and kill residual velocity (for replanning)."""
        with self._lock:
            self.move_target = None
            self.yaw_target = None
            self.vx = self.vy = self.vz = 0.0
            self.yaw_rate = 0.0
            self.set_rc(0, 0, 0, 0)

    def is_move_done(self) -> bool:
        """True once the physics step has cleared any scripted target."""
        with self._lock:
            return self.move_target is None and self.yaw_target is None

    def wait_move(self, timeout: float = 15.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_move_done():
                return True
            time.sleep(0.02)
        with self._lock:
            self.move_target = None
            self.yaw_target = None
            self._landing = False
        return False

    def _finish_landing(self, support: float) -> None:
        self.flying = False
        self._landing = False
        self.move_target = None
        self.vx = self.vy = self.vz = 0.0
        self.z = support
        self.perched = support > 0.02

    def update(self, dt: float) -> None:
        with self._lock:
            if dt <= 0:
                return

            if self.flying:
                self.battery = max(0.0, self.battery - 0.15 * dt)
                self.prop_angle = (self.prop_angle + 720.0 * dt) % 360.0
            else:
                self.prop_angle = 0.0

            # Scripted position move takes priority over RC
            if self.move_target is not None:
                tx, ty, tz = self.move_target
                # While landing, retarget to support under current xy (in case of drift)
                if self._landing:
                    tz = find_support(tx, ty, self.obstacles, z_ref=self.z)[0]
                    self.move_target = (tx, ty, tz)
                dx, dy, dz = tx - self.x, ty - self.y, tz - self.z
                dist = math.sqrt(dx * dx + dy * dy + dz * dz)
                if dist < 0.04:
                    self.x, self.y, self.z = tx, ty, tz
                    self.vx = self.vy = self.vz = 0.0
                    self.move_target = None
                    if self._landing:
                        self._finish_landing(tz)
                else:
                    step = min(self.move_speed * dt, dist)
                    self.x += dx / dist * step
                    self.y += dy / dist * step
                    self.z += dz / dist * step
            elif self.flying:
                # Body-frame RC → world velocity
                scale_h = 1.2  # m/s at full stick
                scale_v = 1.0
                scale_yaw = 90.0  # deg/s
                rad = math.radians(self.yaw)
                body_vx = (self.rc_lr / 100.0) * scale_h
                body_vy = (self.rc_fb / 100.0) * scale_h
                world_vx = body_vx * math.cos(rad) + body_vy * math.sin(rad)
                world_vy = -body_vx * math.sin(rad) + body_vy * math.cos(rad)
                world_vz = (self.rc_ud / 100.0) * scale_v
                self.yaw_rate = (self.rc_yaw / 100.0) * scale_yaw

                # Light smoothing
                alpha = min(1.0, 8.0 * dt)
                self.vx += (world_vx - self.vx) * alpha
                self.vy += (world_vy - self.vy) * alpha
                self.vz += (world_vz - self.vz) * alpha

                self.x += self.vx * dt
                self.y += self.vy * dt
                self.z += self.vz * dt
            else:
                self.vx = self.vy = self.vz = 0.0
                self.yaw_rate = 0.0
                # Stay glued to floor or furniture top
                support = self.support_z()
                self.z = support
                self.perched = support > 0.02

            if self.yaw_target is not None:
                err = (self.yaw_target - self.yaw + 180.0) % 360.0 - 180.0
                step = math.copysign(min(abs(err), self.yaw_speed * dt), err)
                self.yaw = (self.yaw + step) % 360.0
                if abs(err) < 1.5:
                    self.yaw = self.yaw_target % 360.0
                    self.yaw_target = None
            elif self.flying:
                self.yaw = (self.yaw + self.yaw_rate * dt) % 360.0

            # Clamp to room — stay a full safe bubble clear of the outer walls
            margin = max(0.25, self.collision_radius + 0.05)
            self.x = max(self.room_xmin + margin, min(self.room_xmax - margin, self.x))
            self.y = max(self.room_ymin + margin, min(self.room_ymax - margin, self.y))
            self.z = max(0.0, min(self.room_height - self.collision_radius, self.z))

            support = self.support_z()

            # Soft landing: descending onto a surface (RC or residual velocity)
            if self.flying and self.move_target is None and self.z <= support + 0.03:
                descending = self.vz <= 0.05 and self.rc_ud <= 0
                if descending or self._landing:
                    self._finish_landing(support)

            # Furniture side collision (tops are one-way / landable)
            if self.flying:
                nx, ny, nz, hit = resolve_sphere_collision(
                    self.x,
                    self.y,
                    self.z,
                    self.collision_radius,
                    self.obstacles,
                )
                self.collided = hit
                if hit:
                    self.x, self.y, self.z = nx, ny, nz
                    self.vx = self.vy = self.vz = 0.0
                    support = find_support(
                        self.x, self.y, self.obstacles, z_ref=self.z, pad=0.2
                    )[0]
                    # Landing into a slab edge → settle on its top
                    if self._landing and self.z <= support + 0.45:
                        self._finish_landing(support)
                    elif self.z <= support + 0.05:
                        self.z = max(self.z, support)
                        self.move_target = None
                        self._landing = False
                    else:
                        self.move_target = None
                        self._landing = False
            else:
                self.collided = False
