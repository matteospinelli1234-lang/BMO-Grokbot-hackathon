"""Sim patrol, BOSS schema, and the hardware slot gate."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import yaml
from PIL import Image

from scout.cli import make_server
from scout.drivers import (
    TELLO_TAKEOFF_ALT_M,
    HardwareDriver,
    Pose,
    SimDriver,
    cm_steps,
    plan_move,
)
from scout.missions import emergency_land, run_document, run_inbox, run_mission, slot_id
from scout.models import parse_mission
from scout.store import DEFAULT_MISSIONS_ROOT

ROOT = Path(__file__).resolve().parents[1]
DEMO_PATROL = ROOT / "config" / "demo_patrol.json"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        current = self.now
        self.now = current + timedelta(seconds=1)
        return current


def patrol_body(**overrides) -> dict:
    body = {
        "mission_id": "patrol-test-001",
        "type": "patrol",
        "priority": 2,
        "room": "sala-2",
        "waypoints": [
            {"id": "wp1", "x": 0.0, "y": 0.0, "z": 1.0, "yaw": 0},
            {"id": "wp2", "x": 0.8, "y": 0.0, "z": 1.0, "yaw": 0},
            {"id": "wp3", "x": 0.8, "y": 0.6, "z": 1.0, "yaw": 90},
        ],
        "source_event_id": None,
        "capture": {"on_arrive": True, "n_frames": 1},
        "mode": "sim",
        "issued_by": "Console",
        "ts": "2026-11-04T11:00:00Z",
    }
    body.update(overrides)
    return body


class SimPatrolTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_default_missions_root_is_the_shared_box(self) -> None:
        self.assertEqual(DEFAULT_MISSIONS_ROOT, Path("/workspace/drone-patrol/missions"))

    def test_sim_patrol_writes_three_frames_and_done_status(self) -> None:
        driver = SimDriver(sleeper=lambda _seconds: None)
        status = run_mission(
            parse_mission(patrol_body()),
            missions_root=self.root,
            driver=driver,
            clock=Clock(),
        )
        self.assertEqual(status["status"], "done")
        self.assertIsNone(status["reason"])
        self.assertEqual(status["telemetry"]["mode"], "sim")
        self.assertGreaterEqual(status["telemetry"]["battery"], 20)
        self.assertEqual(
            set(status),
            {"mission_id", "status", "reason", "frames", "telemetry"},
        )
        self.assertEqual(len(status["frames"]), 3)
        self.assertEqual(
            [frame["ts"] for frame in status["frames"]],
            [
                "2026-11-04T12:00:00Z",
                "2026-11-04T12:00:01Z",
                "2026-11-04T12:00:02Z",
            ],
        )
        for record in status["frames"]:
            self.assertEqual(set(record), {"path", "ts"})
            path = Path(record["path"])
            self.assertTrue(path.is_file(), path)
            self.assertEqual(path.parent.name, "frames")
            self.assertEqual(path.parent.parent.name, "patrol-test-001")
            with Image.open(path) as image:
                self.assertEqual(image.size, (640, 480))
                self.assertEqual(image.format, "JPEG")

        names = [event.split()[0] for event in driver.events]
        self.assertEqual(names.count("takeoff"), 1)
        self.assertEqual(names.count("land"), 1)
        self.assertEqual(names.count("capture"), 3)
        self.assertLess(names.index("takeoff"), names.index("capture"))
        self.assertLess(names.index("capture"), names.index("land"))
        self.assertAlmostEqual(driver.pose.x, 0.8)
        self.assertAlmostEqual(driver.pose.y, 0.6)
        self.assertAlmostEqual(driver.pose.z, 0.0)
        self.assertAlmostEqual(driver.pose.yaw, 90.0)
        self.assertFalse(driver.airborne)

        on_disk = json.loads((self.root / "patrol-test-001" / "status.json").read_text())
        self.assertEqual(on_disk["status"], "done")
        self.assertEqual(len(list((self.root / "patrol-test-001" / "frames").glob("*.jpg"))), 3)

    def test_patrol_captures_n_frames_at_each_waypoint(self) -> None:
        body = patrol_body(capture={"on_arrive": True, "n_frames": 2}, mission_id="patrol-burst")
        driver = SimDriver(sleeper=lambda _seconds: None)
        status = run_mission(parse_mission(body), missions_root=self.root, driver=driver, clock=Clock())
        self.assertEqual(status["status"], "done")
        self.assertEqual(len(status["frames"]), 6)

    def test_patrol_can_fly_without_stills(self) -> None:
        body = patrol_body(capture={"on_arrive": False, "n_frames": 1}, mission_id="patrol-quiet")
        driver = SimDriver(sleeper=lambda _seconds: None)
        status = run_mission(parse_mission(body), missions_root=self.root, driver=driver)
        self.assertEqual(status["status"], "done")
        self.assertEqual(status["frames"], [])
        self.assertNotIn("capture", [event.split()[0] for event in driver.events])

    def test_wrong_waypoint_count_is_rejected(self) -> None:
        body = patrol_body()
        body["waypoints"] = body["waypoints"][:2]
        status = run_document(body, missions_root=self.root)
        self.assertEqual(status["status"], "rejected")
        self.assertEqual(status["reason"], "invalid_mission")
        self.assertFalse((self.root / "patrol-test-001" / "frames").exists())

    def test_low_battery_does_not_take_off(self) -> None:
        driver = SimDriver(battery=15, sleeper=lambda _seconds: None)
        status = run_mission(parse_mission(patrol_body()), missions_root=self.root, driver=driver)
        self.assertEqual(status["status"], "rejected")
        self.assertEqual(status["reason"], "low_battery")
        self.assertEqual(status["telemetry"]["battery"], 15)
        self.assertNotIn("takeoff", driver.events)
        self.assertEqual(status["frames"], [])

    def test_flight_failure_aborts_and_lands(self) -> None:
        class Boom(SimDriver):
            def __init__(self) -> None:
                super().__init__(sleeper=lambda _seconds: None)
                self._moves = 0

            def move_to(self, x: float, y: float, z: float, yaw: float) -> None:
                self._moves += 1
                if self._moves == 2:
                    raise RuntimeError("sim fault")
                super().move_to(x, y, z, yaw)

        driver = Boom()
        status = run_mission(parse_mission(patrol_body()), missions_root=self.root, driver=driver)
        self.assertEqual(status["status"], "aborted")
        self.assertEqual(status["reason"], "flight_failed")
        self.assertEqual(len(status["frames"]), 1)
        self.assertIn("land", driver.events)
        self.assertFalse(driver.airborne)

    def test_emergency_land_cuts_motors_when_land_fails(self) -> None:
        driver = SimDriver(sleeper=lambda _seconds: None)
        driver.connect()
        driver.takeoff()

        def fail_land() -> None:
            raise RuntimeError("land failed")

        driver.land = fail_land  # type: ignore[method-assign]
        emergency_land(driver)
        self.assertIn("emergency", driver.events)
        self.assertFalse(driver.airborne)

    def test_goto_anomaly_captures_and_returns(self) -> None:
        body = {
            "mission_id": "goto-test-001",
            "type": "goto_anomaly",
            "priority": 1,
            "room": "sala-2",
            "target": {"x": 1.2, "y": 0.4, "z": 1.0, "label": "zona-anomalia"},
            "source_event_id": "evt-test",
            "capture": {"on_arrive": True, "n_frames": 3},
            "mode": "sim",
            "issued_by": "Judge",
            "ts": "2026-11-04T11:05:00Z",
        }
        driver = SimDriver(sleeper=lambda _seconds: None)
        status = run_mission(
            parse_mission(body),
            missions_root=self.root,
            driver=driver,
            hover_s=0.4,
            clock=Clock(),
        )
        self.assertEqual(status["status"], "done")
        self.assertEqual(len(status["frames"]), 3)
        self.assertTrue(any(event.startswith("hover") for event in driver.events))
        self.assertEqual(sum(event.startswith("capture") for event in driver.events), 3)
        self.assertAlmostEqual(driver.pose.x, 0.0)
        self.assertAlmostEqual(driver.pose.y, 0.0)
        self.assertAlmostEqual(driver.pose.z, 0.0)
        self.assertFalse(driver.airborne)

    def test_hardware_without_slot_rejects(self) -> None:
        body = patrol_body(mode="hardware", mission_id="patrol-hw-001", issued_by="Judge")
        env = {
            "SCOUT_SLOT_FILE": str(self.root / "missing-slot.json"),
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("SCOUT_SLOT", None)
            with patch("scout.missions.HardwareDriver", side_effect=AssertionError("no hardware")):
                status = run_mission(parse_mission(body), missions_root=self.root)
        self.assertEqual(status["status"], "rejected")
        self.assertEqual(status["reason"], "no_slot")
        self.assertEqual(status["telemetry"]["mode"], "hw")
        self.assertIsNone(status["telemetry"]["battery"])
        self.assertEqual(status["frames"], [])
        self.assertFalse((self.root / "patrol-hw-001" / "frames").exists())
        on_disk = json.loads((self.root / "patrol-hw-001" / "status.json").read_text())
        self.assertEqual(on_disk["reason"], "no_slot")

    def test_mode_override_sim_flies_a_hardware_mission(self) -> None:
        body = patrol_body(mode="hardware", mission_id="patrol-override")
        status = run_mission(
            parse_mission(body),
            missions_root=self.root,
            mode_override="sim",
        )
        self.assertEqual(status["status"], "done")
        self.assertEqual(status["telemetry"]["mode"], "sim")
        self.assertEqual(len(status["frames"]), 3)

    def test_slot_file_grants_hardware(self) -> None:
        path = self.root / "slot.json"
        path.write_text(json.dumps({"id": "tello-1"}), encoding="utf-8")
        with patch.dict(os.environ, {"SCOUT_SLOT_FILE": str(path)}, clear=False):
            os.environ.pop("SCOUT_SLOT", None)
            self.assertEqual(slot_id(), "tello-1")

    def test_demo_files_match_the_room(self) -> None:
        mission = parse_mission(json.loads(DEMO_PATROL.read_text(encoding="utf-8")))
        self.assertEqual(mission.type, "patrol")
        self.assertEqual(len(mission.waypoints), 3)
        self.assertEqual(mission.mode, "sim")
        room = yaml.safe_load((ROOT / "config" / "room_demo.yaml").read_text(encoding="utf-8"))
        self.assertEqual(len(room["waypoints"]), 3)
        for point, spec in zip(mission.waypoints, room["waypoints"]):
            self.assertEqual(point.id, spec["id"])
            self.assertEqual(point.x, spec["x"])
            self.assertEqual(point.y, spec["y"])

    def test_inbox_runs_priority_one_first(self) -> None:
        inbox = self.root / "inbox"
        inbox.mkdir()
        later = patrol_body(mission_id="patrol-later", priority=5)
        sooner = patrol_body(mission_id="patrol-sooner", priority=1)
        (inbox / "a-later.json").write_text(json.dumps(later), encoding="utf-8")
        (inbox / "b-sooner.json").write_text(json.dumps(sooner), encoding="utf-8")
        (inbox / "broken.json").write_text("{", encoding="utf-8")
        flown = run_inbox(inbox, missions_root=self.root)
        self.assertEqual(
            [item["mission_id"] for item in flown],
            ["patrol-sooner", "patrol-later", "unknown"],
        )
        self.assertTrue((inbox / "done" / "b-sooner.json").is_file())
        self.assertTrue((inbox / "invalid" / "broken.json").is_file())
        self.assertFalse((inbox / "a-later.json").exists())

    def test_plan_move_and_tello_steps(self) -> None:
        move = plan_move(Pose(), 0.8, 0.6, 1.0, 90)
        self.assertAlmostEqual(move.forward_m, 0.8)
        self.assertAlmostEqual(move.right_m, 0.6)
        self.assertAlmostEqual(move.up_m, 1.0)
        self.assertAlmostEqual(move.yaw_delta_deg, 90)
        facing = plan_move(Pose(yaw=90), 0.0, 1.0, 1.0, 90)
        self.assertAlmostEqual(facing.forward_m, 1.0)
        self.assertAlmostEqual(facing.right_m, 0.0)
        self.assertEqual(cm_steps(0.8), [80])
        self.assertEqual(cm_steps(-0.8), [-80])
        self.assertEqual(cm_steps(0.19), [])
        self.assertEqual(cm_steps(5.19), [499, 20])

    def test_hardware_driver_maps_meters_to_tello_commands(self) -> None:
        fake = _FakeTello()
        driver = HardwareDriver(tello_factory=lambda _host: fake, sleeper=lambda _seconds: None)
        driver.connect()
        driver.takeoff()
        self.assertEqual(driver.get_battery(), 77)
        driver.move_to(0.8, 0.0, 1.0, 90)
        frame = driver.capture_frame("wp", "2026-11-04T12:00:00Z")
        driver.land()
        driver.disconnect()
        self.assertIn("connect", fake.calls)
        self.assertIn("streamon", fake.calls)
        self.assertIn("takeoff", fake.calls)
        self.assertIn(("forward", 80), fake.calls)
        self.assertIn(("up", 20), fake.calls)
        self.assertIn(("cw", 90), fake.calls)
        self.assertIn("land", fake.calls)
        self.assertIn("end", fake.calls)
        self.assertEqual(fake.address[1], 8889)
        self.assertAlmostEqual(driver.pose.x, 0.8)
        self.assertAlmostEqual(driver.pose.z, 0.0)
        self.assertEqual(int(frame[0, 0, 0]), 255)
        self.assertAlmostEqual(TELLO_TAKEOFF_ALT_M, 0.8)

    def test_cli_demo_patrol(self) -> None:
        env = os.environ.copy()
        env["SCOUT_MODE"] = "sim"
        env["SCOUT_MISSIONS_ROOT"] = str(self.root)
        env.pop("SCOUT_SLOT", None)
        result = subprocess.run(
            [sys.executable, "-m", "scout", "run", "--mission", str(DEMO_PATROL)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        status = json.loads(result.stdout)
        self.assertEqual(status["mission_id"], "patrol-demo-001")
        self.assertEqual(status["status"], "done")
        self.assertEqual(len(status["frames"]), 3)
        self.assertTrue(Path(status["frames"][0]["path"]).is_file())

    def test_http_mission_post(self) -> None:
        server = make_server("127.0.0.1", 0, self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        port = server.server_address[1]
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/missions",
            data=json.dumps(patrol_body(mission_id="patrol-http")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
        self.assertEqual(body["status"], "done")
        self.assertEqual(len(body["frames"]), 3)


class _FakeTello:
    def __init__(self) -> None:
        self.calls: list[object] = []
        self.address = ("192.168.10.1", 8889)
        self.battery = 77
        self._frame = np.zeros((24, 32, 3), dtype=np.uint8)
        self._frame[..., 2] = 255

    def connect(self) -> None:
        self.calls.append("connect")

    def streamon(self) -> None:
        self.calls.append("streamon")

    def takeoff(self) -> None:
        self.calls.append("takeoff")

    def land(self) -> None:
        self.calls.append("land")

    def emergency(self) -> None:
        self.calls.append("emergency")

    def end(self) -> None:
        self.calls.append("end")

    def move_forward(self, cm: int) -> None:
        self.calls.append(("forward", cm))

    def move_back(self, cm: int) -> None:
        self.calls.append(("back", cm))

    def move_left(self, cm: int) -> None:
        self.calls.append(("left", cm))

    def move_right(self, cm: int) -> None:
        self.calls.append(("right", cm))

    def move_up(self, cm: int) -> None:
        self.calls.append(("up", cm))

    def move_down(self, cm: int) -> None:
        self.calls.append(("down", cm))

    def rotate_clockwise(self, deg: int) -> None:
        self.calls.append(("cw", deg))

    def rotate_counter_clockwise(self, deg: int) -> None:
        self.calls.append(("ccw", deg))

    def get_battery(self) -> int:
        return self.battery

    def get_frame_read(self) -> "_FakeTello":
        return self

    @property
    def frame(self) -> np.ndarray:
        return self._frame


if __name__ == "__main__":
    unittest.main()
