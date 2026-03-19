"""Load and validate configuration from .env file."""

import math
import os
import sys

from dotenv import load_dotenv


REQUIRED_KEYS = [
    "STRAVA_CLIENT_ID",
    "STRAVA_CLIENT_SECRET",
]

OPTIONAL_DEFAULTS = {
    "STRAVA_TOKEN_FILE": ".strava_tokens.json",
    "VT1_BPM": "145",
    "VT2_BPM": "171",
    "MAX_HR": "191",
    "OUTPUT_DIR": "./training_logs",
}


def load_config():
    """Load .env and return a config dict. Exit if required values are missing."""
    load_dotenv()

    missing = [k for k in REQUIRED_KEYS if not os.getenv(k)]
    if missing:
        print(
            f"Error: missing required .env values: {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = {}
    for k in REQUIRED_KEYS:
        cfg[k] = os.getenv(k)

    for k, default in OPTIONAL_DEFAULTS.items():
        cfg[k] = os.getenv(k, default)

    # Cast numeric values
    cfg["VT1_BPM"] = int(cfg["VT1_BPM"])
    cfg["VT2_BPM"] = int(cfg["VT2_BPM"])
    cfg["MAX_HR"] = int(cfg["MAX_HR"])

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
