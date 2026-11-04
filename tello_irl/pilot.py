"""Pygame focus window for WASD free-flight RC on a DroneSession."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tello_irl.controller import DroneSession


def start_pilot_window(session: DroneSession) -> threading.Thread:
    """Spawn a small always-on-top-ish window; hold focus to fly with WASD."""

    stop = threading.Event()

    def _run() -> None:
        import pygame

        pygame.init()
        screen = pygame.display.set_mode((420, 220))
        pygame.display.set_caption("Tello Pilot — focus here for WASD")
        font = pygame.font.SysFont("dejavusans", 16)
        font_sm = pygame.font.SysFont("dejavusans", 13)
        clock = pygame.time.Clock()
        was_active = False

        help_lines = [
            "FOCUS THIS WINDOW to free-fly",
            "W/S forward-back   A/D left-right",
            "R/F up-down   Q/E yaw",
            "Space takeoff / land   Esc hover",
            "Type commands in the terminal REPL",
        ]

        while not stop.is_set() and session.connected:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    stop.set()
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE:
                    if session.flying:
                        try:
                            session.land()
                        except Exception as exc:
                            session.log(f"Land: {exc}")
                    else:
                        try:
                            session.takeoff()
                        except Exception as exc:
                            session.log(f"Takeoff: {exc}")
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    session.set_rc(0, 0, 0, 0)

            focused = pygame.key.get_focused()
            keys = pygame.key.get_pressed()
            lr = fb = ud = yaw = 0
            if focused and not session.scripted_busy.is_set():
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
            if active:
                session.set_rc(lr, fb, ud, yaw)
                was_active = True
            elif was_active:
                session.set_rc(0, 0, 0, 0)
                was_active = False

            # HUD
            screen.fill((28, 30, 36) if focused else (18, 18, 22))
            st = session.status()
            title = font.render(
                f"{'PILOT ACTIVE' if focused else 'click to focus'}  |  "
                f"{'FLYING' if st['flying'] else 'GROUNDED'}  bat={st['battery']}",
                True,
                (0, 220, 180) if focused else (140, 140, 150),
            )
            screen.blit(title, (12, 10))
            y = 40
            for line in help_lines:
                img = font_sm.render(line, True, (200, 204, 210))
                screen.blit(img, (12, y))
                y += 18
            pos = font_sm.render(
                f"est ({st['x']:+.2f}, {st['y']:+.2f}, {st['z']:.2f})  "
                f"yaw {st['yaw']:.0f}°  zone {st['zone'] or '—'}",
                True,
                (255, 180, 80),
            )
            screen.blit(pos, (12, 170))
            if st["busy"]:
                busy = font_sm.render("SCRIPTED MOVE — sticks ignored", True, (255, 100, 80))
                screen.blit(busy, (12, 192))

            pygame.display.flip()
            clock.tick(30)

        try:
            session.set_rc(0, 0, 0, 0)
        except Exception:
            pass
        pygame.quit()

    # Expose stop so commander can shut the window
    thread = threading.Thread(target=_run, name="tello-pilot", daemon=True)
    thread.stop_event = stop  # type: ignore[attr-defined]
    thread.start()
    # Give pygame a beat to open
    time.sleep(0.2)
    return thread


def stop_pilot_window(thread: threading.Thread | None) -> None:
    if thread is None:
        return
    ev = getattr(thread, "stop_event", None)
    if ev is not None:
        ev.set()
    thread.join(timeout=2.0)
