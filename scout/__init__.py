"""Scout flies patrol and anomaly-check missions for a DJI Tello Talent.

Judge and Console issue BOSS missions. Scout flies, captures frames, and
writes a status document. Alerting and email belong to Logger.
"""

__version__ = "0.1.0"

from scout.missions import battery_check, emergency_land, run_document, run_mission
from scout.models import parse_mission

__all__ = [
    "__version__",
    "battery_check",
    "emergency_land",
    "parse_mission",
    "run_document",
    "run_mission",
]
