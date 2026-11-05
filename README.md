# BMO Grokbot Hackathon — Scout (Track D, Drone Patrol)

Scout pilots a DJI Tello Talent for patrol and verification. Judge and Console send a mission. Scout flies, captures frames, and writes a status document those agents can read. Logger owns alerts and email. Scout does not send either.

The default flight mode is simulation. A sim patrol needs no aircraft and no network.

## Setup

From the repository root:

```bash
pip install -r requirements.txt
```

Run commands from the repository root so `python -m scout` can see the package. When `python` is not installed, use `python3` in the same commands.

## Simulation and hardware

Mission field `mode` is `sim` or `hardware`. When the field is omitted, Scout uses sim.

For a local run, `--mode` overrides the file. When `--mode` is absent, `SCOUT_MODE` does (`sim`, `hw`, or `hardware`). The demo command sets both:

```bash
SCOUT_MODE=sim python -m scout run --mission config/demo_patrol.json
```

That patrol visits the three waypoints in `config/demo_patrol.json` and writes one JPEG at each.

Hardware is a DJI Tello Talent reached through `djitellopy`:

- command channel UDP `192.168.10.1:8889`
- video stream UDP port `11111` (`streamon`, then `get_frame_read`)
- join the Tello Wi-Fi (AP mode) before a hardware mission; `SCOUT_TELLO_HOST` overrides the address

A hardware mission also needs a slot. Set `SCOUT_SLOT` to an id such as `tello-1`, or write `/workspace/drone-patrol/slot.json`:

```json
{"id": "tello-1"}
```

With `mode` hardware and no slot, Scout does not arm the motors. It writes `status: rejected` and `reason: no_slot`.

Takeoff is refused when the battery is under 20% (`SCOUT_MIN_BATTERY` to change it). A failure in the air triggers a controlled land, then a motor cut if that land fails.

## Coordinates

Waypoints and targets are meters in the room frame: `+x` forward at yaw 0, `+y` right, `+z` up. Yaw is degrees, clockwise from `+x`. Scout turns those poses into body-frame moves.

`config/room_demo.yaml` is the sala-2 map (three waypoints and `zona-anomalia`). The values match the sample missions. The wire format is the JSON mission, not the YAML file.

Tello distance commands are 20–500 cm. A move shorter than 20 cm is not sent, and a longer move is split into legal commands. In simulation the pose hits the waypoint exactly. After a real takeoff the driver treats altitude as 0.8 m, which is the Tello's automatic climb, and only commands the difference.

## Inbound mission

```json
{
  "mission_id": "uuid",
  "type": "patrol",
  "priority": 3,
  "room": "sala-2",
  "waypoints": [
    {"id": "wp1", "x": 0.0, "y": 0.0, "z": 1.0, "yaw": 0},
    {"id": "wp2", "x": 0.8, "y": 0.0, "z": 1.0, "yaw": 0},
    {"id": "wp3", "x": 0.8, "y": 0.6, "z": 1.0, "yaw": 90}
  ],
  "source_event_id": null,
  "capture": {"on_arrive": true, "n_frames": 1},
  "mode": "sim",
  "issued_by": "Console",
  "ts": "2026-11-04T11:00:00Z"
}
```

| Field | Meaning |
| --- | --- |
| `type` | `patrol` or `goto_anomaly` |
| `priority` | integer 1–5, 1 flies first when several files sit in the inbox |
| `waypoints` | patrol only, exactly 3 points |
| `target` | `goto_anomaly` only: `{x, y, z, label}` |
| `source_event_id` | string or null |
| `capture.on_arrive` | capture when the aircraft arrives |
| `capture.n_frames` | stills per arrival (1–30) |
| `mode` | `sim` or `hardware` |
| `issued_by` | `Judge` or `Console` |
| `ts` | ISO-8601 |

`patrol` takes off, flies the three waypoints in order, captures `n_frames` at each one when `on_arrive` is true, and lands at the last point.

`goto_anomaly` takes off, flies to `target`, hovers, captures `n_frames`, flies back to the start, and lands. Sample: `config/demo_goto_anomaly.json`.

## Outbound status and frames

Scout writes the same status document for BOSS, Judge, and Logger:

```json
{
  "mission_id": "patrol-demo-001",
  "status": "done",
  "reason": null,
  "frames": [
    {"path": "/workspace/drone-patrol/missions/patrol-demo-001/frames/frame_001.jpg", "ts": "2026-11-04T12:00:00Z"}
  ],
  "telemetry": {"battery": 85, "mode": "sim"}
}
```

`status` is one of `accepted`, `rejected`, `en_route`, `arrived`, `capturing`, `done`, `aborted`. `telemetry.mode` is `sim` or `hw`. `reason` is null when the flight finishes. A hardware mission with no slot sets `reason` to `no_slot`.

Paths on the shared box:

- frames: `/workspace/drone-patrol/missions/<mission_id>/frames/frame_001.jpg`
- status: `/workspace/drone-patrol/missions/<mission_id>/status.json`

`status.json` is replaced as the flight moves, and the final file lists every frame captured so far. `SCOUT_MISSIONS_ROOT` relocates that directory for tests.

## How Judge and Console hand a mission

**File drop.** Copy a mission JSON into `data/inbox/` (create the folder if needed). Scout flies priority 1 first:

```bash
cp config/demo_patrol.json data/inbox/
python -m scout run --inbox
```

Finished files move to `data/inbox/done/`. Broken JSON moves to `data/inbox/invalid/`.

**CLI.** One file:

```bash
python -m scout run --mission config/demo_patrol.json
python -m scout status
```

`run` prints the final status JSON on stdout and the flight log on stderr. Exit codes: `0` done, `2` rejected, `1` aborted or unreadable input.

**HTTP (optional, localhost only).**

```bash
python -m scout serve --host 127.0.0.1 --port 8765
```

`POST /missions` with the mission JSON returns the status document. `GET /status` returns the agent snapshot (slot, inbox, missions root) and does not arm the drone.

## Safety

- Fly indoors, with the room clear and the props free.
- Scout never raises a LEO alert and never sends email.
- Default mode is sim. Hardware stays on the ground until a slot is granted and the Tello answers.
- Battery under 20% rejects the mission before takeoff.
- A failed flight lands. If the landing command fails, Scout sends the Tello emergency motor cut.

## Tests

```bash
python -m unittest discover -s tests -v
```

The sim patrol test flies three waypoints with a stand-in clock, checks the JPEGs, and checks the status document. A separate case locks `reason=no_slot` when hardware is requested without a slot.

## Layout

```
scout/            package (drivers, missions, CLI)
config/           demo_patrol.json, demo_goto_anomaly.json, room_demo.yaml
data/inbox/       drop zone for missions from Judge or Console
/workspace/drone-patrol/missions/<mission_id>/   frames and status.json
```
