"""Agent-ready drone session: connect, RC, scripted moves, text commands.

Human REPL and a future LLM/agent should both call the same methods on
``DroneSession`` — do not talk to djitellopy directly from mission code.
"""

from __future__ import annotations

import math
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from tello_sim.nav import resolve_zone
from tello_sim.zones import DEFAULT_ZONES_PATH, Zone, build_pad2_zones, spawn_pose, zone_at


_MIN_CM = 20
_MAX_CM = 500


def chunk_cm(total_cm: int) -> list[int]:
    if abs(total_cm) < _MIN_CM:
        return []
    sign = 1 if total_cm > 0 else -1
    left = abs(total_cm)
    out: list[int] = []
    while left > 0:
        step = min(_MAX_CM, left)
        if 0 < left - step < _MIN_CM:
            step = left if left <= _MAX_CM else left - _MIN_CM
        out.append(sign * step)
        left -= abs(step)
    return out


def world_delta_to_body(dx: float, dy: float, yaw_deg: float) -> tuple[float, float]:
    """Convert world-frame metres delta into body (right, forward) at yaw."""
    rad = math.radians(yaw_deg)
    c, s = math.cos(rad), math.sin(rad)
    # Inverse of request_move_body mapping in tello_sim.drone
    bx = dx * c - dy * s
    by = dx * s + dy * c
    return bx, by


def parse_go(line: str) -> tuple[str | None, str]:
    text = line.strip()
    if text.lower().startswith("go"):
        text = text[2:].strip()
    if not text:
        raise ValueError("Usage: go to <zone>   or   go from <a> to <b>")
    m = re.match(
        r"(?i)^(?:from\s+)?(?:area\s+)?(.+?)\s+(?:->|to)\s+(?:area\s+)?(.+)$",
        text,
    )
    if m:
        a, b = m.group(1).strip(), m.group(2).strip()
        if a.lower() == "to" or a.lower().startswith("to "):
            dest = re.sub(r"(?i)^to\s+(?:area\s+)?", "", text).strip()
            return None, dest
        return a, b
    m = re.match(r"(?i)^to\s+(?:area\s+)?(.+)$", text)
    if m:
        return None, m.group(1).strip()
    return None, text


class DroneSession:
    """Unified control surface for real Tello or simulator.

    Estimated pose starts at HUB.origin and is updated after scripted moves.
    Free-flight RC does not update the estimate (open-loop drift).
    """

    def __init__(
        self,
        *,
        mode: str = "real",
        zones_path: Path | None = None,
        visual: bool = True,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        if mode not in ("real", "sim"):
            raise ValueError("mode must be 'real' or 'sim'")
        self.mode = mode
        self.zones_path = zones_path or DEFAULT_ZONES_PATH
        self.visual = visual
        self.log = on_log or print
        self.zones: list[Zone] = build_pad2_zones(path=self.zones_path)
        self.tello: Any = None
        self.connected = False
        self.flying = False
        self.scripted_busy = threading.Event()
        self._abort = threading.Event()
        self._rc_lock = threading.Lock()
        # Open-loop pose estimate (metres / degrees)
        ox, oy, oz = spawn_pose(self.zones)
        self.x, self.y, self.z = ox, oy, oz
        self.yaw = 0.0
        self.last_zone: Zone | None = zone_at(ox, oy, oz, self.zones)

    # ------------------------------------------------------------------ connect
    def connect(self) -> None:
        if self.connected:
            return
        if self.mode == "real":
            from djitellopy import Tello

            self.log("Connecting to Tello Talent (AP Wi-Fi)...")
            t = Tello()
            t.RESPONSE_TIMEOUT = 10
            t.connect()
            time.sleep(0.4)
            bat = t.get_battery()
            self.log(f"Connected. Battery: {bat}%")
            if bat < 15:
                raise RuntimeError(f"Battery {bat}% too low")
            self.tello = t
        else:
            from tello_sim import SimulatedTello

            self.log("Connecting to simulator...")
            t = SimulatedTello(visual=self.visual)
            t.connect()
            self.log(f"Connected (sim). Battery: {t.get_battery()}%")
            self.tello = t
            snap = t.drone.snapshot()
            self.x, self.y, self.z = snap["x"], snap["y"], snap["z"]
            self.yaw = snap["yaw"]
        self.connected = True
        self._abort.clear()
        self.last_zone = zone_at(self.x, self.y, self.z, self.zones)

    def close(self) -> None:
        self._abort.set()
        try:
            self.set_rc(0, 0, 0, 0)
        except Exception:
            pass
        if self.flying:
            try:
                self.land()
            except Exception:
                pass
        if self.tello is not None:
            try:
                self.tello.end()
            except Exception:
                pass
        self.connected = False
        self.tello = None

    # ------------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        bat = None
        try:
            if self.tello is not None:
                bat = int(self.tello.get_battery())
        except Exception:
            pass
        if self.mode == "sim" and self.tello is not None:
            snap = self.tello.drone.snapshot()
            self.x, self.y, self.z = snap["x"], snap["y"], snap["z"]
            self.yaw = snap["yaw"]
            self.flying = bool(snap["flying"])
        zn = zone_at(self.x, self.y, self.z, self.zones)
        self.last_zone = zn or self.last_zone
        return {
            "mode": self.mode,
            "flying": self.flying,
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "yaw": self.yaw,
            "zone": zn.id if zn else None,
            "battery": bat,
            "busy": self.scripted_busy.is_set(),
        }

    def status_line(self) -> str:
        s = self.status()
        state = "FLYING" if s["flying"] else "GROUNDED"
        return (
            f"{state}  zone={s['zone'] or '—'}  "
            f"est=({s['x']:+.2f}, {s['y']:+.2f}, {s['z']:.2f})  "
            f"yaw={s['yaw']:.0f}°  bat={s['battery'] if s['battery'] is not None else '—'}%"
        )

    # ------------------------------------------------------------------ low-level
    def set_rc(self, lr: int, fb: int, ud: int, yaw: int, *, force: bool = False) -> None:
        """Continuous stick input in [-100, 100]. Ignored while a scripted move runs."""
        if not self.connected or self.tello is None:
            return
        if self.scripted_busy.is_set() and not force:
            return
        with self._rc_lock:
            self.tello.send_rc_control(
                int(max(-100, min(100, lr))),
                int(max(-100, min(100, fb))),
                int(max(-100, min(100, ud))),
                int(max(-100, min(100, yaw))),
            )

    def stop(self) -> None:
        """Hover / cancel current translation — then keep altitude."""
        if self.tello is None:
            return
        try:
            self.tello.send_rc_control(0, 0, 0, 0)
        except Exception:
            pass
        drone = getattr(self.tello, "drone", None)
        if drone is not None and hasattr(drone, "cancel_motion"):
            try:
                drone.cancel_motion()
            except Exception:
                pass
        try:
            if hasattr(self.tello, "send_control_command"):
                self.tello.send_control_command("stop")
        except Exception:
            pass

    def soft_abort(self) -> None:
        """Kill-switch: stop movement and land in place (no motor cut)."""
        self._abort.set()
        self.stop()
        try:
            if self.flying or (
                self.mode == "sim"
                and self.tello is not None
                and self.tello.drone.flying
            ):
                self.tello.land()
                self.flying = False
                self.z = 0.0
                self.log("Soft abort: landed in place.")
        except Exception as exc:
            self.log(f"Soft abort land failed: {exc}")

    def takeoff(self) -> None:
        self._ensure()
        if self.flying:
            self.log("Already flying.")
            return
        self.scripted_busy.set()
        try:
            self.tello.takeoff()
            self.flying = True
            self.z = max(self.z, 0.9)
        finally:
            self.scripted_busy.clear()

    def land(self) -> None:
        self._ensure()
        self.scripted_busy.set()
        try:
            self.stop()
            self.tello.land()
            self.flying = False
            self.z = 0.0
        finally:
            self.scripted_busy.clear()

    # ------------------------------------------------------------------ scripted body moves
    def _run_sdk(self, name: str, arg: int = 0) -> None:
        if self._abort.is_set():
            raise RuntimeError("Aborted")
        t = self.tello
        if name == "takeoff":
            t.takeoff()
            self.flying = True
            self.z = max(self.z, 0.9)
        elif name == "land":
            t.land()
            self.flying = False
            self.z = 0.0
        elif name == "move_up":
            t.move_up(arg)
            self.z += arg / 100.0
        elif name == "move_down":
            t.move_down(arg)
            self.z = max(0.0, self.z - arg / 100.0)
        elif name == "move_left":
            t.move_left(arg)
            self._apply_body(-arg / 100.0, 0.0)
        elif name == "move_right":
            t.move_right(arg)
            self._apply_body(arg / 100.0, 0.0)
        elif name == "move_forward":
            t.move_forward(arg)
            self._apply_body(0.0, arg / 100.0)
        elif name == "move_back":
            t.move_back(arg)
            self._apply_body(0.0, -arg / 100.0)
        elif name == "rotate_cw":
            t.rotate_clockwise(arg)
            self.yaw = (self.yaw + arg) % 360.0
        elif name == "rotate_ccw":
            t.rotate_counter_clockwise(arg)
            self.yaw = (self.yaw - arg) % 360.0
        else:
            raise ValueError(name)
        if self.mode == "sim":
            snap = t.drone.snapshot()
            self.x, self.y, self.z = snap["x"], snap["y"], snap["z"]
            self.yaw = snap["yaw"]
            self.flying = bool(snap["flying"])

    def _apply_body(self, bx: float, by: float) -> None:
        rad = math.radians(self.yaw)
        c, s = math.cos(rad), math.sin(rad)
        self.x += bx * c + by * s
        self.y += -bx * s + by * c

    def run_legs(self, legs: list[tuple[str, int]]) -> None:
        self._ensure()
        self.set_rc(0, 0, 0, 0, force=True)
        self.scripted_busy.set()
        try:
            for cmd, arg in legs:
                if self._abort.is_set():
                    break
                label = cmd if arg == 0 else f"{cmd}({arg})"
                self.log(f"  → {label}")
                self._run_sdk(cmd, arg)
                time.sleep(0.05)
        finally:
            self.scripted_busy.clear()

    def plan_to_point(
        self,
        goal: tuple[float, float, float],
        *,
        include_takeoff: bool = True,
        include_land: bool = False,
    ) -> list[tuple[str, int]]:
        """Open-loop legs from estimated pose → goal (metres), body-aware."""
        legs: list[tuple[str, int]] = []
        if include_takeoff and not self.flying:
            legs.append(("takeoff", 0))
            z_after = max(self.z, 0.9)
        else:
            z_after = self.z if self.flying else 0.0

        dz_cm = int(round((goal[2] - z_after) * 100))
        for cm in chunk_cm(dz_cm):
            legs.append(("move_up" if cm > 0 else "move_down", abs(cm)))

        bx, by = world_delta_to_body(goal[0] - self.x, goal[1] - self.y, self.yaw)
        for cm in chunk_cm(int(round(bx * 100))):
            legs.append(("move_right" if cm > 0 else "move_left", abs(cm)))
        for cm in chunk_cm(int(round(by * 100))):
            legs.append(("move_forward" if cm > 0 else "move_back", abs(cm)))

        if include_land:
            legs.append(("land", 0))
        return legs

    def go_to_zone(self, dest_name: str, *, land: bool = False) -> Zone:
        dest = resolve_zone(dest_name, self.zones)
        goal = dest.hover_point
        legs = self.plan_to_point(goal, include_takeoff=True, include_land=land)
        self.log(f"Open-loop go → {dest.id}  ({len(legs)} leg(s))")
        self.run_legs(legs)
        self.last_zone = dest
        return dest

    # ------------------------------------------------------------------ text API (agent entrypoint)
    def execute(self, line: str) -> str:
        """Run one text command. Returns a short status string. Raises on hard errors."""
        line = line.strip()
        if not line:
            return self.status_line()
        low = line.lower()
        self._abort.clear()

        if low in ("help", "?"):
            return HELP_TEXT
        if low in ("status", "where"):
            return self.status_line()
        if low == "zones":
            rows = []
            for z in self.zones:
                hx, hy, hz = z.hover_point
                rows.append(f"  {z.id:16s}  hover=({hx:+.1f}, {hy:+.1f}, {hz:.1f})")
            return "Known areas:\n" + "\n".join(rows)
        if low == "takeoff":
            self.takeoff()
            return self.status_line()
        if low == "land":
            self.land()
            return self.status_line()
        if low in ("stop", "hover", "brake"):
            self.stop()
            return "Stopped / hover.\n" + self.status_line()
        if low in ("abort", "kill"):
            self.soft_abort()
            return "Soft abort complete.\n" + self.status_line()

        m = re.match(r"(?i)^move\s+(forward|back|left|right|up|down)\s+(\d+)\s*cm?$", low)
        if m:
            direction, cm_s = m.group(1), int(m.group(2))
            mapping = {
                "forward": "move_forward",
                "back": "move_back",
                "left": "move_left",
                "right": "move_right",
                "up": "move_up",
                "down": "move_down",
            }
            self.run_legs([(mapping[direction], cm_s)])
            return self.status_line()

        m = re.match(r"(?i)^(?:yaw|rotate|turn)\s+(cw|ccw|left|right)\s+(\d+)$", low)
        if m:
            which, deg = m.group(1), int(m.group(2))
            if which in ("cw", "right"):
                self.run_legs([("rotate_cw", deg)])
            else:
                self.run_legs([("rotate_ccw", deg)])
            return self.status_line()

        if low.startswith("go") or re.search(r"(?i)\bto\b", line) or "->" in line:
            cmd = line if low.startswith("go") else "go " + line
            _src, dest = parse_go(cmd)
            land = "land" in low.split()
            self.go_to_zone(dest, land=land)
            return self.status_line()

        raise ValueError(f"Unknown command: {line!r}. Type 'help'.")

    def _ensure(self) -> None:
        if not self.connected or self.tello is None:
            raise RuntimeError("Not connected — call connect() first")


HELP_TEXT = """
Commands (text — same API an agent can call via session.execute)
  takeoff / land / stop          Flight state
  abort / kill                   Stop movement + land in place
  go to <zone>                   Open-loop fly to zone hover
  go from <a> to <b>             Same (uses current estimate as start)
  move forward|back|left|right|up|down <cm>
  yaw cw|ccw <deg>               Or: rotate left|right <deg>
  status / where / zones
  help / quit

Pilot window (focus it for free flight)
  W/S forward-back   A/D strafe   R/F up-down   Q/E yaw
  Space takeoff/land   Esc zero sticks (hover)
""".strip()
