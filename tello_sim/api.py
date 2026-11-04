"""Tello-like Python API backed by the in-process simulator."""

from __future__ import annotations

import math
import time
from typing import Any

import numpy as np

from tello_sim.app import (
    get_renderer,
    get_shared_drone,
    start_app_background,
    start_physics_background,
)
from tello_sim.drone import Drone


class FrameReader:
    """Minimal stand-in for djitellopy BackgroundFrameRead."""

    def __init__(self, drone: Drone) -> None:
        self._drone = drone

    @property
    def frame(self) -> np.ndarray:
        renderer = get_renderer()
        if renderer is not None and renderer.last_fpv_bgr is not None:
            return renderer.last_fpv_bgr.copy()
        # Placeholder until the visual loop has rendered
        return np.zeros((240, 320, 3), dtype=np.uint8)


class SimulatedTello:
    """Subset of djitellopy.Tello aimed at hackathon scripts.

    By default starts the 3D window in a background thread so you can
    watch scripted flights. Pass ``visual=False`` for headless physics only.
    """

    def __init__(
        self,
        *,
        visual: bool = True,
        headless: bool = False,
        duration: float = 0.0,
    ) -> None:
        self.drone = get_shared_drone()
        self._visual = visual
        self._headless = headless
        self._duration = duration
        self._connected = False
        self._frame_reader = FrameReader(self.drone)

    def connect(self) -> str:
        # Reset to takeoff pad so leftover state from prior runs cannot poison scripts
        d = self.drone
        d.x = d.y = d.z = 0.0
        d.yaw = 0.0
        d.vx = d.vy = d.vz = 0.0
        d.flying = False
        d.perched = False
        d.collided = False
        d._landing = False
        d.move_target = None
        d.yaw_target = None
        d.set_rc(0, 0, 0, 0)

        if self._visual:
            start_app_background(headless=self._headless, duration=self._duration)
            # Let a couple of frames render
            time.sleep(0.15)
        else:
            start_physics_background()
        self._connected = True
        return "ok"

    def end(self) -> None:
        self._connected = False
        self.drone.set_rc(0, 0, 0, 0)

    def takeoff(self) -> str:
        self._ensure()
        self.drone.takeoff()
        self.drone.wait_move(timeout=8.0)
        return "ok"

    def land(self) -> str:
        self._ensure()
        self.drone.land()
        self.drone.wait_move(timeout=8.0)
        return "ok"

    def move_forward(self, cm: int) -> str:
        return self._move_body(0.0, cm / 100.0, 0.0)

    def move_back(self, cm: int) -> str:
        return self._move_body(0.0, -cm / 100.0, 0.0)

    def move_left(self, cm: int) -> str:
        return self._move_body(-cm / 100.0, 0.0, 0.0)

    def move_right(self, cm: int) -> str:
        return self._move_body(cm / 100.0, 0.0, 0.0)

    def move_up(self, cm: int) -> str:
        return self._move_body(0.0, 0.0, cm / 100.0)

    def move_down(self, cm: int) -> str:
        return self._move_body(0.0, 0.0, -cm / 100.0)

    def rotate_clockwise(self, degrees: int) -> str:
        self._ensure_flying()
        self.drone.request_yaw(float(degrees))
        self.drone.wait_move(timeout=12.0)
        return "ok"

    def rotate_counter_clockwise(self, degrees: int) -> str:
        self._ensure_flying()
        self.drone.request_yaw(-float(degrees))
        self.drone.wait_move(timeout=12.0)
        return "ok"

    def send_rc_control(self, left_right: int, forward_back: int, up_down: int, yaw: int) -> None:
        self._ensure()
        self.drone.set_rc(left_right, forward_back, up_down, yaw)

    def get_battery(self) -> int:
        return int(self.drone.snapshot()["battery"])

    def get_height(self) -> int:
        """Height in centimetres (Tello SDK style)."""
        return int(self.drone.snapshot()["z"] * 100)

    def get_frame_read(self) -> FrameReader:
        return self._frame_reader

    def go_to_xyz(
        self,
        x: float,
        y: float,
        z: float,
        timeout: float | None = None,
        *,
        face: bool = True,
    ) -> str:
        """Fly to a world-frame point (metres). Faces the target first so FPV looks ahead."""
        self._ensure_flying()
        d = self.drone
        snap = d.snapshot()
        dist = math.dist((snap["x"], snap["y"], snap["z"]), (x, y, z))
        if timeout is None:
            timeout = max(25.0, dist / max(0.35, d.move_speed) + 8.0)

        # Turn toward the horizontal destination before translating
        if face and d.face_toward(x, y):
            if not d.wait_move(timeout=12.0):
                raise RuntimeError(f"Timeout while yawing toward ({x:.2f}, {y:.2f})")

        with d._lock:
            d._landing = False
            d.collided = False
            d.move_target = (float(x), float(y), float(z))
            d.set_rc(0, 0, 0, 0)
        ok = d.wait_move(timeout=timeout)
        snap = d.snapshot()
        if snap["collided"]:
            raise RuntimeError(
                f"Collision while flying to ({x:.2f}, {y:.2f}, {z:.2f}) "
                f"— stopped at ({snap['x']:.2f}, {snap['y']:.2f}, {snap['z']:.2f})"
            )
        if not ok:
            raise RuntimeError(
                f"Timeout while flying to ({x:.2f}, {y:.2f}, {z:.2f}) "
                f"— at ({snap['x']:.2f}, {snap['y']:.2f}, {snap['z']:.2f})"
            )
        return "ok"

    # --- helpers ---

    def _move_body(self, dx: float, dy: float, dz: float) -> str:
        self._ensure_flying()
        self.drone.request_move_body(dx, dy, dz)
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        self.drone.wait_move(timeout=max(15.0, dist / 0.35 + 5.0))
        return "ok"

    def _ensure(self) -> None:
        if not self._connected:
            raise RuntimeError("Call connect() first")

    def _ensure_flying(self) -> None:
        self._ensure()
        if not self.drone.flying:
            raise RuntimeError("Drone is not flying — call takeoff() first")

    def __enter__(self) -> SimulatedTello:
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            if self.drone.flying:
                self.land()
        finally:
            self.end()
