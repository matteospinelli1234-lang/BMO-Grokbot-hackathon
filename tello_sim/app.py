"""Main simulator loop: physics, keyboard RC, rendering."""

from __future__ import annotations

import argparse
import sys
import threading
import time
from typing import Callable

import pygame

from tello_sim.drone import Drone
from tello_sim.render import Renderer

# Shared drone used by the visual app and SimulatedTello
_SHARED_DRONE: Drone | None = None
_SHARED_LOCK = threading.Lock()
_APP_THREAD: threading.Thread | None = None
_APP_RUNNING = threading.Event()
_RENDERER: Renderer | None = None


def get_shared_drone() -> Drone:
    global _SHARED_DRONE
    with _SHARED_LOCK:
        if _SHARED_DRONE is None:
            _SHARED_DRONE = Drone()
        return _SHARED_DRONE


def get_renderer() -> Renderer | None:
    return _RENDERER


_keyboard_rc_active = False


def _apply_keyboard_rc(drone: Drone, keys: pygame.key.ScancodeWrapper) -> None:
    """Keyboard RC wins while keys are held; otherwise leave API RC untouched."""
    global _keyboard_rc_active
    lr = fb = ud = yaw = 0.0
    if keys[pygame.K_a]:
        lr -= 80
    if keys[pygame.K_d]:
        lr += 80
    if keys[pygame.K_w]:
        fb += 80
    if keys[pygame.K_s]:
        fb -= 80
    if keys[pygame.K_r]:
        ud += 80
    if keys[pygame.K_f]:
        ud -= 80
    if keys[pygame.K_q]:
        yaw -= 70
    if keys[pygame.K_e]:
        yaw += 70

    active = any((lr, fb, ud, yaw))
    if drone.move_target is not None or drone.yaw_target is not None:
        return
    if active:
        drone.set_rc(lr, fb, ud, yaw)
        _keyboard_rc_active = True
    elif _keyboard_rc_active:
        drone.set_rc(0, 0, 0, 0)
        _keyboard_rc_active = False


def run_app(
    *,
    duration: float = 0.0,
    width: int = 1280,
    height: int = 720,
    headless: bool = False,
    on_frame: Callable[[Drone, Renderer], None] | None = None,
) -> int:
    """Run the visual simulator. Blocks until quit or duration elapsed."""
    global _RENDERER

    drone = get_shared_drone()
    renderer = Renderer(width, height)
    # Keep physics and visuals on the same PAD2 map
    drone.obstacles = renderer.obstacles
    drone.room_xmin = renderer.room.xmin
    drone.room_xmax = renderer.room.xmax
    drone.room_ymin = renderer.room.ymin
    drone.room_ymax = renderer.room.ymax
    drone.room_height = renderer.room.zmax
    _RENDERER = renderer
    pygame_caption = f"Tello Talent — {renderer.room.source}"

    if headless:
        # Offscreen: still tick physics + FPV frames for API smoke tests
        pygame.init()
        pygame.display.set_mode((1, 1), pygame.HIDDEN)
    else:
        pygame.init()
        screen = pygame.display.set_mode((width, height))
        pygame.display.set_caption(pygame_caption)

    clock = pygame.time.Clock()
    _APP_RUNNING.set()
    start = time.perf_counter()
    running = True
    space_was_down = False
    c_was_down = False
    z_was_down = False

    try:
        while running:
            dt = clock.tick(60) / 1000.0
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False

            keys = pygame.key.get_pressed()
            space_down = bool(keys[pygame.K_SPACE])
            if space_down and not space_was_down:
                if drone.flying:
                    drone.land()
                else:
                    drone.takeoff()
            space_was_down = space_down

            c_down = bool(keys[pygame.K_c])
            if c_down and not c_was_down:
                renderer.orbit_mode = not renderer.orbit_mode
            c_was_down = c_down

            z_down = bool(keys[pygame.K_z])
            if z_down and not z_was_down:
                renderer.show_zones = not renderer.show_zones
            z_was_down = z_down

            if renderer.orbit_mode:
                renderer.orbit_angle = (renderer.orbit_angle + 25.0 * dt) % 360.0

            _apply_keyboard_rc(drone, keys)
            drone.update(dt)

            fps = clock.get_fps()
            if headless:
                # Still produce FPV frames
                dummy = pygame.Surface((width, height))
                renderer.compose(dummy, drone, fps)
            else:
                renderer.compose(screen, drone, fps)
                pygame.display.flip()

            if on_frame is not None:
                on_frame(drone, renderer)

            if duration > 0 and (time.perf_counter() - start) >= duration:
                running = False
    finally:
        _APP_RUNNING.clear()
        pygame.quit()
        _RENDERER = None

    return 0


def start_app_background(**kwargs) -> threading.Thread:
    """Start the visual loop on a daemon thread (for SimulatedTello scripts)."""
    global _APP_THREAD
    if _APP_THREAD is not None and _APP_THREAD.is_alive():
        return _APP_THREAD

    def _target() -> None:
        run_app(**kwargs)

    _APP_THREAD = threading.Thread(target=_target, name="tello-sim-app", daemon=True)
    _APP_THREAD.start()
    # Wait until the loop is up
    for _ in range(100):
        if _APP_RUNNING.is_set():
            break
        time.sleep(0.02)
    return _APP_THREAD


_PHYSICS_THREAD: threading.Thread | None = None
_PHYSICS_STOP = threading.Event()


def start_physics_background(hz: float = 60.0) -> threading.Thread:
    """Tick drone physics without a window (API-only / headless scripts)."""
    global _PHYSICS_THREAD
    if _APP_RUNNING.is_set():
        # Visual app already advances physics
        return _APP_THREAD  # type: ignore[return-value]
    if _PHYSICS_THREAD is not None and _PHYSICS_THREAD.is_alive():
        return _PHYSICS_THREAD

    drone = get_shared_drone()
    _PHYSICS_STOP.clear()
    dt = 1.0 / hz

    def _target() -> None:
        while not _PHYSICS_STOP.is_set():
            t0 = time.perf_counter()
            drone.update(dt)
            sleep = dt - (time.perf_counter() - t0)
            if sleep > 0:
                time.sleep(sleep)

    _PHYSICS_THREAD = threading.Thread(
        target=_target, name="tello-sim-physics", daemon=True
    )
    _PHYSICS_THREAD.start()
    return _PHYSICS_THREAD


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Tello Talent 3D flight simulator")
    p.add_argument("--duration", type=float, default=0.0, help="Auto-quit after N seconds")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument(
        "--headless",
        action="store_true",
        help="Hidden window; useful for automated smoke tests",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run_app(
        duration=args.duration,
        width=args.width,
        height=args.height,
        headless=args.headless,
    )


if __name__ == "__main__":
    raise SystemExit(main())
