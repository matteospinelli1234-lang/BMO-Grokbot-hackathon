"""Interactive text interface to fly the sim between named zones."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from tello_sim import SimulatedTello, build_pad2_zones, zone_at
from tello_sim.nav import go_to_zone, resolve_zone, sync_map
from tello_sim.zones import DEFAULT_ZONES_PATH, Zone


HELP = """
Commands
  go to <zone>                 Fly to a zone (auto takeoff)
  go from <a> to <b>           Fly from zone A to zone B
  go <a> -> <b>                Same as above
  takeoff / land               Manual takeoff or land
  status / where               Position, yaw, current zone
  zones                        List known areas
  help                         Show this help
  quit / exit                  Land (if flying) and quit

Zone names can be ids or labels, e.g. HUB, PAD, NORTH, "MEZZ TOP", UNDER_MEZZ.
Sim spawn is HUB.origin (edit the orange diamond in zone_editor.py).
""".strip()


def _parse_go(line: str) -> tuple[str | None, str]:
    """Parse a go command into (optional_from, destination)."""
    text = line.strip()
    # strip leading 'go'
    if text.lower().startswith("go"):
        text = text[2:].strip()
    if not text:
        raise ValueError("Usage: go to <zone>   or   go from <a> to <b>")

    # go from A to B
    m = re.match(
        r"(?i)^(?:from\s+)?(?:area\s+)?(.+?)\s+(?:->|to)\s+(?:area\s+)?(.+)$",
        text,
    )
    if m:
        a, b = m.group(1).strip(), m.group(2).strip()
        # "to NORTH" alone: first group might be "to NORTH" with weird match —
        # require that if starts with 'to ', it's destination-only
        if a.lower() == "to" or a.lower().startswith("to "):
            dest = re.sub(r"(?i)^to\s+(?:area\s+)?", "", text).strip()
            return None, dest
        return a, b

    # go to B
    m = re.match(r"(?i)^to\s+(?:area\s+)?(.+)$", text)
    if m:
        return None, m.group(1).strip()

    # bare zone name
    return None, text


class Commander:
    def __init__(
        self,
        *,
        visual: bool = True,
        headless: bool = False,
        zones_path: Path | None = None,
    ) -> None:
        self.zones_path = zones_path or DEFAULT_ZONES_PATH
        self.zones = build_pad2_zones(path=self.zones_path)
        self.tello = SimulatedTello(visual=visual, headless=headless)
        self.obstacles: list = []
        self.last_zone: Zone | None = None

    def start(self) -> None:
        self.tello.connect()
        self.obstacles = sync_map(self.tello)
        snap = self.tello.drone.snapshot()
        self.last_zone = zone_at(snap["x"], snap["y"], snap["z"], self.zones)
        print("Tello zone commander ready.")
        print(f"Zones file: {self.zones_path}")
        print(f"Areas: {', '.join(z.id for z in self.zones)}")
        print("Type 'help' for commands. Visual window follows the drone.\n")

    def status_line(self) -> str:
        snap = self.tello.drone.snapshot()
        zn = zone_at(snap["x"], snap["y"], snap["z"], self.zones)
        self.last_zone = zn or self.last_zone
        state = "FLYING" if snap["flying"] else ("PERCHED" if snap.get("perched") else "GROUNDED")
        zid = zn.id if zn else "—"
        return (
            f"{state}  zone={zid}  "
            f"pos=({snap['x']:+.2f}, {snap['y']:+.2f}, {snap['z']:.2f})  "
            f"yaw={snap['yaw']:.0f}°  bat={snap['battery']:.0f}%"
        )

    def cmd_zones(self) -> None:
        print("Known areas:")
        for z in self.zones:
            hx, hy, hz = z.hover_point
            print(
                f"  {z.id:16s}  label={z.label!r:16s}  "
                f"hover=({hx:+.1f}, {hy:+.1f}, {hz:.1f})"
            )

    def cmd_go(self, line: str) -> None:
        src_name, dest_name = _parse_go(line)
        dest = resolve_zone(dest_name, self.zones)
        src = resolve_zone(src_name, self.zones) if src_name else self.last_zone
        # If user said "from A to B", verify we're roughly at A (soft warning)
        if src_name and src is not None:
            snap = self.tello.drone.snapshot()
            here = zone_at(snap["x"], snap["y"], snap["z"], self.zones)
            if here is not None and here.id != src.id:
                print(
                    f"Note: you said from {src.id}, but drone is in {here.id} — "
                    f"routing from current position via {here.id} exit rules."
                )
                src = here
        arrived = go_to_zone(
            self.tello,
            dest,
            self.zones,
            self.obstacles,
            from_zone=src,
        )
        self.last_zone = arrived
        print(self.status_line())

    def handle(self, line: str) -> bool:
        """Handle one input line. Returns False to quit."""
        line = line.strip()
        if not line:
            return True
        low = line.lower()

        if low in ("quit", "exit", "q"):
            return False
        if low in ("help", "?"):
            print(HELP)
            return True
        if low in ("status", "where"):
            print(self.status_line())
            return True
        if low == "zones":
            self.cmd_zones()
            return True
        if low == "takeoff":
            if self.tello.drone.flying:
                print("Already flying.")
            else:
                self.tello.takeoff()
                print(self.status_line())
            return True
        if low == "land":
            if not self.tello.drone.flying:
                print("Already on the ground.")
            else:
                self.tello.land()
                snap = self.tello.drone.snapshot()
                self.last_zone = zone_at(snap["x"], snap["y"], snap["z"], self.zones)
                print(self.status_line())
            return True
        if low.startswith("go"):
            self.cmd_go(line)
            return True

        # Allow bare "from A to B" without go
        if re.search(r"(?i)\bto\b", line) or "->" in line:
            self.cmd_go("go " + line)
            return True

        print(f"Unknown command: {line!r}. Type 'help'.")
        return True

    def shutdown(self) -> None:
        try:
            if self.tello.drone.flying:
                print("Landing...")
                self.tello.land()
        except Exception as exc:
            print(f"Land warning: {exc}")
        self.tello.end()

    def repl(self) -> int:
        self.start()
        print(HELP)
        print()
        try:
            while True:
                try:
                    line = input("tello> ")
                except EOFError:
                    print()
                    break
                try:
                    if not self.handle(line):
                        break
                except Exception as exc:
                    print(f"Error: {exc}")
                    print(self.status_line())
        except KeyboardInterrupt:
            print("\nInterrupted.")
        finally:
            self.shutdown()
        return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Text interface to fly between PAD2 zones")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--no-visual", action="store_true")
    p.add_argument("--path", type=Path, default=DEFAULT_ZONES_PATH)
    p.add_argument(
        "-c",
        "--command",
        action="append",
        default=[],
        help="Run a command non-interactively (repeatable), then quit",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cmd = Commander(
        visual=not args.no_visual,
        headless=args.headless,
        zones_path=args.path,
    )
    if args.command:
        cmd.start()
        try:
            for c in args.command:
                print(f"tello> {c}")
                if not cmd.handle(c):
                    break
        except Exception as exc:
            print(f"Error: {exc}")
            cmd.shutdown()
            return 1
        cmd.shutdown()
        return 0
    return cmd.repl()


if __name__ == "__main__":
    raise SystemExit(main())
