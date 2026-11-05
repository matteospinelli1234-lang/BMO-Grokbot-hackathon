"""Shared-box mission directory: frames and status.json."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
from PIL import Image

DEFAULT_MISSIONS_ROOT = Path("/workspace/drone-patrol/missions")
DEFAULT_SLOT_FILE = Path("/workspace/drone-patrol/slot.json")
DEFAULT_INBOX = Path("data/inbox")


def missions_root(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit
    override = os.environ.get("SCOUT_MISSIONS_ROOT", "").strip()
    if override:
        return Path(override)
    return DEFAULT_MISSIONS_ROOT


def mission_dir(root: Path, mission_id: str) -> Path:
    path = root / mission_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_status(directory: Path, payload: dict) -> Path:
    """Atomically replace status.json with the latest outbound document."""
    target = directory / "status.json"
    temporary = directory / ".status.json.tmp"
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def save_frame(directory: Path, index: int, image: np.ndarray) -> Path:
    frames = directory / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    path = frames / f"frame_{index:03d}.jpg"
    Image.fromarray(image, mode="RGB").save(path, format="JPEG", quality=85)
    return path.resolve()
