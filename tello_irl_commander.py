#!/usr/bin/env python3
"""IRL Tello Talent commander — connect, WASD free-flight, typed commands.

Real drone (PC on Tello AP Wi-Fi):
    python tello_irl_commander.py

Simulator (no Wi-Fi needed — use this when the drone won't join):
    python tello_irl_commander.py --sim

Auto (try real, fall back to sim — best default for agents):
    python tello_irl_commander.py --auto

Agent / batch (no interactive pilot):
    python tello_irl_commander.py --auto -c takeoff -c "go to NORTH" -c land

From Python (future agent):
    from tello_irl import DroneSession
    s = DroneSession(mode="auto")  # or "sim" if you know Wi-Fi is down
    s.connect()
    s.execute("go to NORTH")
    s.close()
"""

from tello_irl.commander import main

if __name__ == "__main__":
    raise SystemExit(main())
