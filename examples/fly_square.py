#!/usr/bin/env python3
"""Fly a 50 cm square in the 3D simulator using the Tello-like API."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tello_sim import SimulatedTello


def main() -> int:
    tello = SimulatedTello(visual=True)
    tello.connect()
    print(f"Battery: {tello.get_battery()}%")

    tello.takeoff()
    print(f"Airborne at {tello.get_height()} cm")

    side = 50
    for i in range(4):
        print(f"Leg {i + 1}: forward {side} cm")
        tello.move_forward(side)
        print("Yaw 90°")
        tello.rotate_clockwise(90)

    tello.land()
    print("Landed.")
    tello.end()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
