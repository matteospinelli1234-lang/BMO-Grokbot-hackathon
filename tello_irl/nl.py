"""Lightweight NL → structured drone commands (Cursor does the heavy understanding).

This mapper covers common English/Italian phrases so the bridge can act even
before an LLM rewrites the request. Cursor should still prefer calling /exec
with an explicit command after interpreting the user.
"""

from __future__ import annotations

import re
from typing import Sequence

from tello_sim.nav import resolve_zone
from tello_sim.zones import Zone


def interpret_nl(text: str, zones: Sequence[Zone]) -> str:
    """Map natural language to a single structured command string.

    Raises ValueError if the intent is unclear — Cursor should then rewrite
    into an explicit ``go to …`` / ``takeoff`` / etc.
    """
    raw = text.strip()
    if not raw:
        raise ValueError("Empty request")
    low = raw.lower().strip()

    # Already structured SDK / commander lines — pass through
    if re.match(
        r"(?i)^(takeoff|land|stop|hover|abort|kill|status|where|zones|help)\s*$",
        low,
    ):
        return low.split()[0]
    # Exact structured go/move/yaw — but not prose like "go back to the start"
    if re.match(r"(?i)^go\s+to\s+\S+", low) and "beginning" not in low and "start" not in low:
        try:
            # Validate destination resolves; otherwise fall through to NL
            from tello_sim.nav import parse_go as _pg  # type: ignore

            _pg  # noqa: keep import optional
        except Exception:
            pass
        # Only pass through if it looks like an id, not English prose
        if re.match(
            r"(?i)^go\s+to\s+(HUB|PAD|ZONE_\d+|NORTH|SOUTH|EAST|WEST|[\w]+_[\w]+)\s*$",
            low,
        ):
            return raw
    if re.match(
        r"(?i)^move\s+(forward|back|left|right|up|down)\s+\d+\s*cm?$",
        low,
    ):
        return raw
    if re.match(r"(?i)^(?:yaw|rotate|turn)\s+(cw|ccw|left|right)\s+\d+$", low):
        return raw

    if re.search(r"(?i)\b(status|where|posizione|dove)\b", low) and not re.search(
        r"(?i)\b(go|move|fly|porta|sposta|vai)\b", low
    ):
        return "status"

    if re.search(r"(?i)\b(land|atterra|landing|atterraggio)\b", low) and not re.search(
        r"(?i)\b(take\s*off|decolla)\b", low
    ):
        return "land"

    if re.search(r"(?i)\b(take\s*off|decolla|takeoff)\b", low):
        return "takeoff"

    if re.search(r"(?i)\b(abort|kill|emergency|stop\s+now|ferma|arresta)\b", low):
        return "abort"

    # Home / start / beginning → HUB
    if re.search(
        r"(?i)\b(back\s+to\s+(the\s+)?(start|beginning|hub|home|pad|origin)|"
        r"return\s+home|go\s+home|torna\s+(all')?inizio)\b",
        low,
    ):
        return "go to HUB"

    # "from X to Y" / "to Y" / "toward Y" / "… to area 7"
    dest = _extract_destination(low, zones)
    if dest is not None:
        return f"go to {dest}"

    raise ValueError(
        "Could not map that to a drone command. "
        "Try: 'go to ZONE_7', 'takeoff', 'land', or name a known zone."
    )


def _extract_destination(low: str, zones: Sequence[Zone]) -> str | None:
    # Explicit to / toward / verso / fino a …
    m = re.search(
        r"(?i)\b(?:to|toward|towards|verso|fino\s+a|in)\s+"
        r"(?:the\s+)?(?:area|zona|zone|pad|hub|north|south|east|west|mezz)?\s*"
        r"([a-z0-9 _-]+?)(?:\s+and\s+land)?\s*$",
        low,
    )
    if m:
        cand = m.group(0)
        # Prefer the chunk after to/verso
        m2 = re.search(
            r"(?i)\b(?:to|toward|towards|verso|fino\s+a)\s+(?:the\s+)?(.+?)$",
            low,
        )
        phrase = (m2.group(1) if m2 else cand).strip()
        phrase = re.sub(r"(?i)^(area|zona|zone)\s+", r"\1 ", phrase)
        phrase = re.sub(r"(?i)\s+and\s+land$", "", phrase).strip()
        # beginning / start → HUB (resolve aliases)
        phrase = re.sub(
            r"(?i)^(the\s+)?(beginning|start|starting|initial|home|hub|inizio|partenza)(?:\s+position)?$",
            "HUB",
            phrase,
        )
        try:
            return resolve_zone(phrase, list(zones)).id
        except ValueError:
            pass

    # "area 7" / "zona 7" anywhere as destination of a move verb
    if re.search(r"(?i)\b(go|fly|move|bring|take|porta|sposta|vai|raggiungi)\b", low):
        m = re.search(r"(?i)\b(?:area|zona|zone)\s*[_#-]?\s*(\d+)\b", low)
        if m:
            try:
                return resolve_zone(f"ZONE_{m.group(1)}", list(zones)).id
            except ValueError:
                pass
        # named places
        for label in (
            "north",
            "south",
            "east",
            "west",
            "hub",
            "pad",
            "mezz",
            "under mezz",
            "zone 7",
            "area 7",
        ):
            if re.search(rf"(?i)\b{re.escape(label)}\b", low):
                try:
                    return resolve_zone(label, list(zones)).id
                except ValueError:
                    continue
    return None
