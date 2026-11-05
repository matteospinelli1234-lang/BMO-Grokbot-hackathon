"""BOSS mission documents and the status documents Scout writes back."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

MISSION_TYPES = ("patrol", "goto_anomaly")
MISSION_MODES = ("sim", "hardware")
ISSUERS = ("Judge", "Console")
STATUS_VALUES = (
    "accepted",
    "rejected",
    "en_route",
    "arrived",
    "capturing",
    "done",
    "aborted",
)
_MISSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_MAX_FRAMES = 30


class MissionRejected(Exception):
    """The mission document cannot be flown. `reason` is a BOSS status reason."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class Waypoint:
    id: str
    x: float
    y: float
    z: float
    yaw: float


@dataclass
class Target:
    x: float
    y: float
    z: float
    label: str


@dataclass
class Capture:
    on_arrive: bool = True
    n_frames: int = 1


@dataclass
class Mission:
    mission_id: str
    type: str
    priority: int
    room: str
    waypoints: list[Waypoint]
    target: Target | None
    source_event_id: str | None
    capture: Capture
    mode: str
    issued_by: str
    ts: str


def safe_mission_id(mission_id: str) -> bool:
    return bool(_MISSION_ID.fullmatch(mission_id))


def parse_mission(data: Any) -> Mission:
    """Parse one inbound BOSS mission. Raises MissionRejected('invalid_mission')."""
    if not isinstance(data, dict):
        raise MissionRejected("invalid_mission")

    mission_id = data.get("mission_id")
    if not isinstance(mission_id, str) or not safe_mission_id(mission_id):
        raise MissionRejected("invalid_mission")

    mission_type = data.get("type")
    if mission_type not in MISSION_TYPES:
        raise MissionRejected("invalid_mission")

    priority = data.get("priority")
    if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 5:
        raise MissionRejected("invalid_mission")

    room = data.get("room")
    if not isinstance(room, str) or not room.strip():
        raise MissionRejected("invalid_mission")

    mode = data.get("mode", "sim")
    if mode not in MISSION_MODES:
        raise MissionRejected("invalid_mission")

    issued_by = data.get("issued_by")
    if issued_by not in ISSUERS:
        raise MissionRejected("invalid_mission")

    _require_timestamp(data.get("ts"))
    source_event_id = _source_event(data)
    capture = _capture(data.get("capture", {"on_arrive": True, "n_frames": 1}))

    waypoints: list[Waypoint] = []
    target: Target | None = None
    if mission_type == "patrol":
        if data.get("target") is not None:
            raise MissionRejected("invalid_mission")
        raw_points = data.get("waypoints")
        if not isinstance(raw_points, list) or len(raw_points) != 3:
            raise MissionRejected("invalid_mission")
        waypoints = [_waypoint(item) for item in raw_points]
    else:
        raw_target = data.get("target")
        if not isinstance(raw_target, dict):
            raise MissionRejected("invalid_mission")
        target = _target(raw_target)

    return Mission(
        mission_id=mission_id,
        type=mission_type,
        priority=priority,
        room=room,
        waypoints=waypoints,
        target=target,
        source_event_id=source_event_id,
        capture=capture,
        mode=mode,
        issued_by=issued_by,
        ts=data["ts"],
    )


def make_status(
    mission_id: str,
    status: str,
    *,
    reason: str | None = None,
    frames: list[dict[str, str]] | None = None,
    battery: int | None = None,
    mode: str = "sim",
) -> dict[str, Any]:
    """Outbound status for BOSS, Judge, and Logger."""
    if status not in STATUS_VALUES:
        raise ValueError(f"unknown status {status}")
    if mode not in ("sim", "hw"):
        raise ValueError(f"unknown telemetry mode {mode}")
    return {
        "mission_id": mission_id,
        "status": status,
        "reason": reason,
        "frames": list(frames or []),
        "telemetry": {"battery": battery, "mode": mode},
    }


def normalize_mode(raw: str) -> str:
    """Map CLI / env spellings onto the mission mode enum."""
    value = raw.strip().lower()
    if value == "hw":
        value = "hardware"
    if value not in MISSION_MODES:
        raise MissionRejected("invalid_mission")
    return value


def _require_timestamp(value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise MissionRejected("invalid_mission")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MissionRejected("invalid_mission") from exc


def _source_event(data: dict[str, Any]) -> str | None:
    if "source_event_id" not in data or data["source_event_id"] is None:
        return None
    value = data["source_event_id"]
    if not isinstance(value, str) or not value.strip():
        raise MissionRejected("invalid_mission")
    return value


def _capture(raw: Any) -> Capture:
    if not isinstance(raw, dict):
        raise MissionRejected("invalid_mission")
    on_arrive = raw.get("on_arrive", True)
    if not isinstance(on_arrive, bool):
        raise MissionRejected("invalid_mission")
    n_frames = raw.get("n_frames", 1)
    if isinstance(n_frames, bool) or not isinstance(n_frames, int):
        raise MissionRejected("invalid_mission")
    if not 1 <= n_frames <= _MAX_FRAMES:
        raise MissionRejected("invalid_mission")
    return Capture(on_arrive=on_arrive, n_frames=n_frames)


def _waypoint(raw: Any) -> Waypoint:
    if not isinstance(raw, dict):
        raise MissionRejected("invalid_mission")
    point_id = raw.get("id")
    if not isinstance(point_id, str) or not point_id.strip():
        raise MissionRejected("invalid_mission")
    return Waypoint(
        id=point_id,
        x=_number(raw.get("x")),
        y=_number(raw.get("y")),
        z=_number(raw.get("z")),
        yaw=_number(raw.get("yaw")),
    )


def _target(raw: dict[str, Any]) -> Target:
    label = raw.get("label")
    if not isinstance(label, str) or not label.strip():
        raise MissionRejected("invalid_mission")
    return Target(
        x=_number(raw.get("x")),
        y=_number(raw.get("y")),
        z=_number(raw.get("z")),
        label=label,
    )


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MissionRejected("invalid_mission")
    return float(value)
