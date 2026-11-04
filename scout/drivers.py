"""Drone drivers.

Room coordinates are meters. `x` points forward at yaw 0, `y` points right,
`z` is up, and yaw is degrees clockwise from +x. Tello commands are body-frame
centimeters on UDP port 8889, with the video stream on UDP 11111.
"""

from __future__ import annotations

import logging
import math
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

logger = logging.getLogger("scout.driver")

# djitellopy Tello.CONTROL_UDP_PORT. Video is VS_UDP_PORT 11111.
TELLO_COMMAND_PORT = 8889
TELLO_DISTANCE_MIN_CM = 20
TELLO_DISTANCE_MAX_CM = 500
# Height Tello reaches by itself on takeoff. Hardware pose starts here.
TELLO_TAKEOFF_ALT_M = 0.8
FRAME_SIZE = (640, 480)


@dataclass
class Pose:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    yaw: float = 0.0


@dataclass(frozen=True)
class BodyMove:
    forward_m: float
    right_m: float
    up_m: float
    yaw_delta_deg: float


def norm_yaw(yaw: float) -> float:
    return (float(yaw) + 180.0) % 360.0 - 180.0


def plan_move(pose: Pose, x: float, y: float, z: float, yaw: float) -> BodyMove:
    """Body-frame delta that carries `pose` to an absolute room pose."""
    dx = x - pose.x
    dy = y - pose.y
    rad = math.radians(pose.yaw)
    forward = dx * math.cos(rad) + dy * math.sin(rad)
    right = -dx * math.sin(rad) + dy * math.cos(rad)
    return BodyMove(forward, right, z - pose.z, norm_yaw(yaw - pose.yaw))


def cm_steps(meters: float) -> list[int]:
    """Split a signed distance into Tello commands of 20–500 cm.

    A leftover shorter than 20 cm is folded into the previous step when that
    keeps every command legal. Distances under 20 cm produce no command.
    """
    total = int(round(abs(float(meters)) * 100))
    if total < TELLO_DISTANCE_MIN_CM:
        return []
    sign = 1 if meters >= 0 else -1
    steps: list[int] = []
    while total >= TELLO_DISTANCE_MIN_CM:
        step = min(total, TELLO_DISTANCE_MAX_CM)
        leftover = total - step
        if leftover and leftover < TELLO_DISTANCE_MIN_CM:
            step = total - TELLO_DISTANCE_MIN_CM
        if step < TELLO_DISTANCE_MIN_CM:
            break
        steps.append(sign * step)
        total -= step
    return steps


def _apply_body(pose: Pose, forward_m: float, right_m: float, up_m: float, yaw_delta: float) -> None:
    rad = math.radians(pose.yaw)
    pose.x += forward_m * math.cos(rad) - right_m * math.sin(rad)
    pose.y += forward_m * math.sin(rad) + right_m * math.cos(rad)
    pose.z += up_m
    pose.yaw = norm_yaw(pose.yaw + yaw_delta)


class DroneDriver(ABC):
    kind: str
    pose: Pose
    airborne: bool
    connected: bool

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def takeoff(self) -> None: ...

    @abstractmethod
    def land(self) -> None: ...

    @abstractmethod
    def emergency(self) -> None: ...

    @abstractmethod
    def move_to(self, x: float, y: float, z: float, yaw: float) -> None: ...

    @abstractmethod
    def hover(self, seconds: float) -> None: ...

    @abstractmethod
    def capture_frame(self, label: str, ts: str = "") -> np.ndarray: ...

    @abstractmethod
    def get_battery(self) -> int: ...


class SimDriver(DroneDriver):
    """Software Tello. Logs the flight and draws a frame. No radio, no props."""

    kind = "sim"

    def __init__(self, battery: int = 88, sleeper=time.sleep):
        self.pose = Pose()
        self.airborne = False
        self.connected = False
        self.events: list[str] = []
        self._battery = int(battery)
        self._sleep = sleeper
        self._frame_index = 0
        self._room = ""

    def set_room(self, room: str) -> None:
        self._room = room

    def connect(self) -> None:
        self.connected = True
        self._event("connect")

    def disconnect(self) -> None:
        self.connected = False
        self.airborne = False
        self._event("disconnect")

    def takeoff(self) -> None:
        self._require_connected()
        self.airborne = True
        self._event("takeoff")

    def land(self) -> None:
        self.airborne = False
        self.pose.z = 0.0
        self._event("land")

    def emergency(self) -> None:
        self.airborne = False
        self.pose.z = 0.0
        self._event("emergency")

    def move_to(self, x: float, y: float, z: float, yaw: float) -> None:
        self._require_airborne()
        move = plan_move(self.pose, x, y, z, yaw)
        self._event(
            "move "
            f"fwd={move.forward_m:.2f} right={move.right_m:.2f} "
            f"up={move.up_m:.2f} yaw={move.yaw_delta_deg:.1f}"
        )
        self.pose = Pose(x, y, z, norm_yaw(yaw))
        self._drain(1)

    def hover(self, seconds: float) -> None:
        self._require_airborne()
        self._event(f"hover {seconds:.1f}s")
        if seconds > 0:
            self._sleep(seconds)

    def capture_frame(self, label: str, ts: str = "") -> np.ndarray:
        self._require_airborne()
        self._frame_index += 1
        self._event(f"capture {label}")
        self._drain(1)
        return render_sim_frame(self._room, label, self.pose, ts, self._frame_index)

    def get_battery(self) -> int:
        return max(0, min(100, self._battery))

    def _event(self, name: str) -> None:
        self.events.append(name)
        logger.info("%s", name)

    def _drain(self, points: int) -> None:
        self._battery = max(0, self._battery - points)

    def _require_connected(self) -> None:
        if not self.connected:
            raise RuntimeError("sim driver is not connected")

    def _require_airborne(self) -> None:
        self._require_connected()
        if not self.airborne:
            raise RuntimeError("sim driver is on the ground")


class HardwareDriver(DroneDriver):
    """DJI Tello Talent through djitellopy (command UDP 8889, video UDP 11111)."""

    kind = "hw"

    def __init__(self, host: str | None = None, sleeper=time.sleep, tello_factory=None):
        self.host = host or os.environ.get("SCOUT_TELLO_HOST", "192.168.10.1")
        self.pose = Pose()
        self.airborne = False
        self.connected = False
        self.events: list[str] = []
        self._sleep = sleeper
        self._tello = None
        self._tello_factory = tello_factory

    def connect(self) -> None:
        if self.connected:
            return
        factory = self._tello_factory or _default_tello
        tello = factory(self.host)
        self._tello = tello
        try:
            address = getattr(tello, "address", None)
            if not address or int(address[1]) != TELLO_COMMAND_PORT:
                raise RuntimeError(
                    f"Tello command channel must be UDP {TELLO_COMMAND_PORT}"
                )
            tello.connect()
            tello.streamon()
        except Exception:
            self._tello = None
            try:
                tello.end()
            except Exception:
                logger.exception("tello cleanup after failed connect")
            raise
        self.connected = True
        self.pose = Pose()
        self._event("connect")

    def disconnect(self) -> None:
        tello = self._tello
        self._tello = None
        self.connected = False
        if tello is None:
            return
        try:
            if self.airborne:
                tello.land()
            tello.end()
        except Exception:
            logger.exception("tello disconnect failed")
        self.airborne = False
        self._event("disconnect")

    def takeoff(self) -> None:
        self._require_connected()
        self._tello.takeoff()
        self.airborne = True
        self.pose.z = TELLO_TAKEOFF_ALT_M
        self._event("takeoff")

    def land(self) -> None:
        if self._tello is not None and self.airborne:
            self._tello.land()
        self.airborne = False
        self.pose.z = 0.0
        self._event("land")

    def emergency(self) -> None:
        if self._tello is not None:
            self._tello.emergency()
        self.airborne = False
        self.pose.z = 0.0
        self._event("emergency")

    def move_to(self, x: float, y: float, z: float, yaw: float) -> None:
        self._require_airborne()
        planned = plan_move(self.pose, x, y, z, yaw)
        forward = self._fly_axis(planned.forward_m, self._tello.move_forward, self._tello.move_back)
        right = self._fly_axis(planned.right_m, self._tello.move_right, self._tello.move_left)
        up = self._fly_axis(planned.up_m, self._tello.move_up, self._tello.move_down)
        yaw_cmd = int(round(planned.yaw_delta_deg))
        if abs(yaw_cmd) >= 1:
            yaw_cmd = max(-360, min(360, yaw_cmd))
            if yaw_cmd > 0:
                self._tello.rotate_clockwise(yaw_cmd)
            else:
                self._tello.rotate_counter_clockwise(-yaw_cmd)
        else:
            yaw_cmd = 0
        _apply_body(self.pose, forward, right, up, yaw_cmd)
        self._event(
            "move "
            f"fwd={forward:.2f} right={right:.2f} up={up:.2f} yaw={yaw_cmd}"
        )

    def hover(self, seconds: float) -> None:
        self._require_airborne()
        self._event(f"hover {seconds:.1f}s")
        if seconds <= 0:
            return
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            if remaining > 5:
                self._tello.send_keepalive()
            self._sleep(min(1.0, remaining))

    def capture_frame(self, label: str, ts: str = "") -> np.ndarray:
        self._require_airborne()
        import cv2

        reader = self._tello.get_frame_read()
        frame = None if reader is None else reader.frame
        image = np.asarray(frame) if frame is not None else None
        if image is None or image.size == 0:
            raise RuntimeError("tello video frame is empty")
        if image.ndim != 3 or image.shape[2] != 3:
            raise RuntimeError("tello video frame is not BGR")
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        self._event(f"capture {label}")
        return np.ascontiguousarray(rgb)

    def get_battery(self) -> int:
        self._require_connected()
        return int(self._tello.get_battery())

    def _fly_axis(self, meters: float, positive, negative) -> float:
        steps = cm_steps(meters)
        commanded_cm = 0
        for step in steps:
            if step > 0:
                positive(step)
            elif step < 0:
                negative(-step)
            commanded_cm += step
        requested_cm = int(round(abs(meters) * 100))
        skipped = requested_cm - abs(commanded_cm)
        if skipped:
            logger.warning(
                "skipped %s cm (Tello moves are %s–%s cm)",
                skipped,
                TELLO_DISTANCE_MIN_CM,
                TELLO_DISTANCE_MAX_CM,
            )
        return commanded_cm / 100.0

    def _event(self, name: str) -> None:
        self.events.append(name)
        logger.info("%s", name)

    def _require_connected(self) -> None:
        if not self.connected or self._tello is None:
            raise RuntimeError("tello is not connected")

    def _require_airborne(self) -> None:
        self._require_connected()
        if not self.airborne:
            raise RuntimeError("tello is on the ground")


def render_sim_frame(room: str, label: str, pose: Pose, ts: str, index: int) -> np.ndarray:
    """Fabricate a 640x480 patrol still. The label and pose change every shot."""
    width, height = FRAME_SIZE
    image = Image.new("RGB", (width, height), (16, 28, 40))
    draw = ImageDraw.Draw(image)
    for x in range(0, width, 40):
        draw.line([(x, 0), (x, height)], fill=(30, 52, 68))
    for y in range(0, height, 40):
        draw.line([(0, y), (width, y)], fill=(30, 52, 68))
    accent = _accent(label)
    draw.rectangle([160, 120, 480, 360], outline=accent, width=6)
    cx = 320 + int(max(-140, min(140, pose.x * 50)))
    cy = 240 + int(max(-100, min(100, pose.y * 50)))
    draw.ellipse([cx - 16, cy - 16, cx + 16, cy + 16], fill=accent)
    draw.text((24, 20), "SCOUT SIM", fill=(236, 242, 247))
    draw.text((24, 48), f"{room}  {label}  frame {index}", fill=(198, 220, 230))
    draw.text(
        (24, 76),
        f"x={pose.x:.2f} y={pose.y:.2f} z={pose.z:.2f} yaw={pose.yaw:.0f}",
        fill=(160, 186, 198),
    )
    draw.text((24, 440), ts or "sim", fill=(150, 164, 172))
    return np.asarray(image, dtype=np.uint8)


def _accent(label: str) -> tuple[int, int, int]:
    mix = sum(ord(char) for char in label) if label else 40
    return (40 + (mix * 3) % 180, 120 + (mix * 5) % 100, 180)


def _default_tello(host: str):
    from djitellopy import Tello

    return Tello(host)
