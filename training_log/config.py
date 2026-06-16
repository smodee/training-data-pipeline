"""Load and validate configuration from .env file.

Authentication is handled by suuntool itself (``suuntool login``), so there are no
required credentials in this codebase anymore. Everything here has a sensible default;
the .env file only needs to exist if you want to override the HR thresholds, output
directory, or the suuntool binary location.
"""

import math
import os

from dotenv import load_dotenv


# No required keys: suuntool owns authentication. Everything below is optional.
OPTIONAL_DEFAULTS = {
    # Path to the suuntool executable (or just "suuntool" if it's on PATH).
    "SUUNTOOL_PATH": "suuntool",
    # Rolling daily-TSS history used to seed CTL/ATL. "~" is expanded.
    "TSS_HISTORY_FILE": "~/.training_log_tss.json",
    # HR thresholds used for zone boundaries and (as a fallback) hrTSS estimation.
    "VT1_BPM": "145",
    "VT2_BPM": "171",
    "MAX_HR": "191",
    # Lactate / functional threshold HR used to anchor hrTSS when a workout has no
    # native TSS value. Defaults to VT2 if left unset.
    "THRESHOLD_HR": "",
    "OUTPUT_DIR": "./training_logs",
}


def load_config():
    """Load .env and return a config dict with all values resolved to defaults."""
    load_dotenv()

    cfg = {}
    for k, default in OPTIONAL_DEFAULTS.items():
        cfg[k] = os.getenv(k, default)

    # Cast numeric values
    cfg["VT1_BPM"] = int(cfg["VT1_BPM"])
    cfg["VT2_BPM"] = int(cfg["VT2_BPM"])
    cfg["MAX_HR"] = int(cfg["MAX_HR"])
    cfg["THRESHOLD_HR"] = int(cfg["THRESHOLD_HR"]) if cfg["THRESHOLD_HR"] else cfg["VT2_BPM"]

    # Expand ~ in file paths
    cfg["TSS_HISTORY_FILE"] = os.path.expanduser(cfg["TSS_HISTORY_FILE"])

    return cfg


def compute_zone_boundaries(cfg):
    """Return a list of (zone_name, lower, upper) tuples.

    upper is inclusive. Use None for unbounded ends.
    """
    vt1 = cfg["VT1_BPM"]
    vt2 = cfg["VT2_BPM"]

    z0_upper = math.floor(vt1 * 0.80) - 1
    z1_lower = math.floor(vt1 * 0.80)
    z1_upper = math.floor(vt1 * 0.90)
    z2_lower = math.floor(vt1 * 0.90) + 1
    z2_upper = vt1 - 1
    z3_lower = vt1
    z3_upper = vt2 - 1
    z4_lower = vt2

    return [
        ("Z0", None, z0_upper),
        ("Z1", z1_lower, z1_upper),
        ("Z2", z2_lower, z2_upper),
        ("Z3", z3_lower, z3_upper),
        ("Z4", z4_lower, None),
    ]
