"""Patrol and anomaly flights, plus the battery and emergency-land guards."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scout.drivers import DroneDriver, HardwareDriver, SimDriver
from scout.models import Mission, MissionRejected, make_status, normalize_mode, parse_mission, safe_mission_id
from scout.store import DEFAULT_SLOT_FILE, save_frame, write_status

logger = logging.getLogger("scout.mission")

DEFAULT_MIN_BATTERY = 20
DEFAULT_HOVER_S = 0.2


@dataclass
class BatteryReport:
    percent: int
    ok: bool
    minimum: int


def battery_check(driver: DroneDriver, minimum: int | None = None) -> BatteryReport:
    """Refuse takeoff when the pack is under the indoor minimum (default 20%)."""
    if minimum is None:
        raw = os.environ.get("SCOUT_MIN_BATTERY", str(DEFAULT_MIN_BATTERY))
        try:
            minimum = int(raw)
        except ValueError:
            minimum = DEFAULT_MIN_BATTERY
    percent = int(driver.get_battery())
    return BatteryReport(percent=percent, ok=percent >= minimum, minimum=minimum)


def emergency_land(driver: DroneDriver) -> None:
    """Land. If the controlled landing fails while the props are spinning, cut motors."""
    logger.warning("emergency land")
    try:
        driver.land()
        return
    except Exception:
        logger.exception("controlled land failed")
    try:
        driver.emergency()
    except Exception:
        logger.exception("motor cut failed")


def slot_id(slot_file: Path | None = None) -> str | None:
    """Return the hardware slot id, or None when this process has no slot.

    A slot is `SCOUT_SLOT` or a JSON file (default `/workspace/drone-patrol/slot.json`)
    shaped `{"id": "tello-1"}`. Hardware missions without a slot are rejected.
    """
    env = os.environ.get("SCOUT_SLOT", "").strip()
    if env:
        return env
    path = slot_file
    if path is None:
        override = os.environ.get("SCOUT_SLOT_FILE", "").strip()
        path = Path(override) if override else DEFAULT_SLOT_FILE
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(data, dict):
        slot = str(data.get("id") or data.get("slot") or "").strip()
        return slot or None
    if isinstance(data, str):
        return data.strip() or None
    return None


def run_document(
    data: Any,
    *,
    missions_root: Path,
    mode_override: str | None = None,
    driver: DroneDriver | None = None,
    sleeper=time.sleep,
    clock=None,
    hover_s: float = DEFAULT_HOVER_S,
) -> dict[str, Any]:
    """Validate a raw mission dict, fly it, and always return a status document."""
    if not isinstance(data, dict):
        return make_status("unknown", "rejected", reason="invalid_mission")
    try:
        mission = parse_mission(data)
    except MissionRejected as exc:
        return _reject_unparsed(data, exc.reason, missions_root)
    return run_mission(
        mission,
        missions_root=missions_root,
        mode_override=mode_override,
        driver=driver,
        sleeper=sleeper,
        clock=clock,
        hover_s=hover_s,
    )


def run_mission(
    mission: Mission,
    *,
    missions_root: Path,
    mode_override: str | None = None,
    driver: DroneDriver | None = None,
    sleeper=time.sleep,
    clock=None,
    hover_s: float = DEFAULT_HOVER_S,
) -> dict[str, Any]:
    """Fly one mission and write status.json under the shared missions root."""
    directory = ensure_mission_dir(missions_root, mission.mission_id)
    try:
        mode = normalize_mode(mode_override) if mode_override else mission.mode
    except MissionRejected:
        payload = make_status(mission.mission_id, "rejected", reason="invalid_mission")
        write_status(directory, payload)
        return payload

    telemetry_mode = "hw" if mode == "hardware" else "sim"
    if driver is None and mode == "hardware" and slot_id() is None:
        logger.info("reject %s reason=no_slot", mission.mission_id)
        payload = make_status(
            mission.mission_id,
            "rejected",
            reason="no_slot",
            mode=telemetry_mode,
        )
        write_status(directory, payload)
        return payload

    if driver is None:
        driver = HardwareDriver(sleeper=sleeper) if mode == "hardware" else SimDriver(sleeper=sleeper)
    if isinstance(driver, SimDriver):
        driver.set_room(mission.room)
        telemetry_mode = "sim"
    elif isinstance(driver, HardwareDriver):
        telemetry_mode = "hw"

    sink = _StatusSink(mission, directory, driver, telemetry_mode, clock)
    try:
        try:
            driver.connect()
        except Exception:
            logger.exception("connect failed for %s", mission.mission_id)
            return sink.publish("rejected", "hardware_unavailable")
        report = battery_check(driver)
        if not report.ok:
            logger.info(
                "reject %s reason=low_battery %s%% < %s%%",
                mission.mission_id,
                report.percent,
                report.minimum,
            )
            return sink.publish("rejected", "low_battery")
        sink.publish("accepted")
        try:
            if mission.type == "patrol":
                _patrol(driver, mission, sink)
            else:
                _goto_anomaly(driver, mission, sink, hover_s)
        except Exception:
            logger.exception("flight failed for %s", mission.mission_id)
            emergency_land(driver)
            return sink.publish("aborted", "flight_failed")
        logger.info(
            "mission %s done frames=%s",
            mission.mission_id,
            len(sink.frames),
        )
        return sink.publish("done")
    finally:
        try:
            driver.disconnect()
        except Exception:
            logger.exception("disconnect failed for %s", mission.mission_id)


def run_inbox(
    inbox: Path,
    *,
    missions_root: Path,
    mode_override: str | None = None,
) -> list[dict[str, Any]]:
    """Fly every `*.json` mission in the inbox, highest urgency (priority 1) first."""
    inbox.mkdir(parents=True, exist_ok=True)
    pending: list[tuple[int, str, Path, Any]] = []
    invalid = inbox / "invalid"
    for path in sorted(inbox.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.error("invalid JSON in %s", path)
            _archive(path, invalid)
            pending.append((99, path.name, path, None))
            continue
        priority = 99
        if isinstance(data, dict) and isinstance(data.get("priority"), int) and not isinstance(data.get("priority"), bool):
            priority = data["priority"]
        pending.append((priority, path.name, path, data))

    flown: list[dict[str, Any]] = []
    done = inbox / "done"
    for priority, name, path, data in sorted(pending, key=lambda item: (item[0], item[1])):
        if data is None:
            flown.append(make_status("unknown", "rejected", reason="invalid_mission"))
            continue
        if not path.is_file():
            continue
        logger.info("inbox %s priority=%s", name, priority)
        status = run_document(data, missions_root=missions_root, mode_override=mode_override)
        flown.append(status)
        _archive(path, done)
    return flown


def _patrol(driver: DroneDriver, mission: Mission, sink: "_StatusSink") -> None:
    driver.takeoff()
    for point in mission.waypoints:
        sink.publish("en_route")
        driver.move_to(point.x, point.y, point.z, point.yaw)
        sink.publish("arrived")
        _capture(driver, mission, sink, point.id)
    driver.land()


def _goto_anomaly(driver: DroneDriver, mission: Mission, sink: "_StatusSink", hover_s: float) -> None:
    home = (driver.pose.x, driver.pose.y, driver.pose.yaw)
    target = mission.target
    if target is None:
        raise RuntimeError("goto_anomaly is missing a target")
    driver.takeoff()
    sink.publish("en_route")
    driver.move_to(target.x, target.y, target.z, driver.pose.yaw)
    sink.publish("arrived")
    driver.hover(hover_s)
    _capture(driver, mission, sink, target.label)
    sink.publish("en_route")
    driver.move_to(home[0], home[1], driver.pose.z, home[2])
    driver.land()


def _capture(driver: DroneDriver, mission: Mission, sink: "_StatusSink", label: str) -> None:
    if not mission.capture.on_arrive:
        return
    sink.publish("capturing")
    for _ in range(mission.capture.n_frames):
        ts = _timestamp(sink.clock)
        image = driver.capture_frame(label, ts)
        sink.add_frame(image, ts)
        sink.publish("capturing")


def _reject_unparsed(data: dict, reason: str, missions_root: Path) -> dict[str, Any]:
    mission_id = data.get("mission_id")
    mode = "hw" if data.get("mode") == "hardware" else "sim"
    if not isinstance(mission_id, str) or not safe_mission_id(mission_id):
        return make_status("unknown", "rejected", reason=reason, mode=mode)
    payload = make_status(mission_id, "rejected", reason=reason, mode=mode)
    write_status(ensure_mission_dir(missions_root, mission_id), payload)
    return payload


def _timestamp(clock) -> str:
    moment = clock() if clock is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc).replace(microsecond=0)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _archive(path: Path, folder: Path) -> None:
    if not path.is_file():
        return
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / path.name
    if destination.exists():
        destination = folder / f"{path.stem}-{int(time.time())}{path.suffix}"
    path.replace(destination)


class _StatusSink:
    def __init__(self, mission: Mission, directory: Path, driver: DroneDriver, mode: str, clock):
        self.mission = mission
        self.directory = directory
        self.driver = driver
        self.mode = mode
        self.clock = clock
        self.frames: list[dict[str, str]] = []
        self._index = 0

    def add_frame(self, image, ts: str) -> dict[str, str]:
        self._index += 1
        path = save_frame(self.directory, self._index, image)
        record = {"path": str(path), "ts": ts}
        self.frames.append(record)
        return record

    def publish(self, status: str, reason: str | None = None) -> dict[str, Any]:
        payload = make_status(
            self.mission.mission_id,
            status,
            reason=reason,
            frames=self.frames,
            battery=self._battery(),
            mode=self.mode,
        )
        write_status(self.directory, payload)
        logger.info("%s status=%s", self.mission.mission_id, status)
        return payload

    def _battery(self) -> int | None:
        try:
            return int(self.driver.get_battery())
        except Exception:
            logger.debug("battery unavailable", exc_info=True)
            return None


def ensure_mission_dir(root: Path, mission_id: str) -> Path:
    from scout.store import mission_dir

    return mission_dir(root, mission_id)
