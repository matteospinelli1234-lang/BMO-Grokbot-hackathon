#!/usr/bin/env python3
"""Text interface to tell the simulated drone where to go.

Examples at the prompt:
  go from PAD to NORTH
  go to MEZZ TOP
  go AISLE_EAST -> UNDER_MEZZ
  status
  land
  quit
"""

from tello_sim.commander import main

if __name__ == "__main__":
    raise SystemExit(main())
