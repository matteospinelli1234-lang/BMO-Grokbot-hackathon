#!/usr/bin/env python3
"""Headless smoke test: takeoff → move → land, assert motion."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tello_sim import SimulatedTello


def main() -> int:
    tello = SimulatedTello(visual=False)
    tello.connect()

    assert tello.get_battery() > 0
    z0 = tello.drone.snapshot()["z"]
    tello.takeoff()
    z1 = tello.drone.snapshot()["z"]
    assert z1 > 0.5, f"expected airborne height, got {z1}"
    print(f"takeoff ok: z {z0:.2f} → {z1:.2f} m")

    y0 = tello.drone.snapshot()["y"]
    tello.move_forward(40)
    y1 = tello.drone.snapshot()["y"]
    assert y1 > y0 + 0.25, f"expected forward move, y {y0:.2f} → {y1:.2f}"
    print(f"move_forward ok: y {y0:.2f} → {y1:.2f} m")

    tello.rotate_clockwise(90)
    yaw = tello.drone.snapshot()["yaw"]
    assert 80 < yaw < 100, f"expected ~90° yaw, got {yaw}"
    print(f"rotate ok: yaw={yaw:.1f}°")

    tello.land()
    z2 = tello.drone.snapshot()["z"]
    assert z2 < 0.05, f"expected landed, z={z2}"
    print(f"land ok: z={z2:.2f} m")

    frame = tello.get_frame_read().frame
    assert frame is not None and frame.ndim == 3
    print(f"frame shape={frame.shape} (placeholder without visual)")

    tello.end()
    print("smoke_sim: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
