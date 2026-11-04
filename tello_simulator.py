#!/usr/bin/env python3
"""Launch the Tello Talent 3D flight simulator.

Keyboard (window focused):
  Space  takeoff / land
  W/S    forward / back
  A/D    left / right
  R/F    up / down
  Q/E    yaw
  C      toggle chase / orbit camera
  Esc    quit

Scripted control:
  from tello_sim import SimulatedTello
  # see examples/fly_square.py
"""

from tello_sim.app import main

if __name__ == "__main__":
    raise SystemExit(main())
