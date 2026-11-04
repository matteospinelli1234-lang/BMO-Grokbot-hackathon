#!/usr/bin/env python3
"""Launch the 3D navigation-zone editor.

Edit rectangular zones over the PAD2 map, then press S to save zones.json.
The flight sim and tour script load that file automatically.
"""

from tello_sim.zone_editor import main

if __name__ == "__main__":
    raise SystemExit(main())
