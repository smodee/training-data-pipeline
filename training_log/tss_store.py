"""Rolling daily-TSS history store and CTL / ATL / Form computation.

CTL and ATL are exponentially-weighted moving averages of daily TSS, so a single run's
date range is not enough to compute them correctly — the averages depend on weeks of
prior load. We persist a small ``{date: tss}`` JSON file (path from config) and update
it on every run. On first use the caller seeds it with a long look-back (see
``training_log.py``) so CTL starts from a realistic value rather than zero.
"""

import json
import math
import os
from datetime import date, timedelta

CTL_TIME_CONSTANT = 42  # days — "fitness"
ATL_TIME_CONSTANT = 7   # days — "fatigue"


def load_history(path):
    """Load the {date: tss} history file. Returns {} if it doesn't exist yet."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_history(path, history):
    """Write the {date: tss} history file (sorted by date for readable diffs)."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    ordered = {k: history[k] for k in sorted(history)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ordered, f, indent=2)


def update_history(history, daily_tss):
    """Merge freshly computed ``{date: tss}`` values into the stored history.

    Newly fetched values overwrite stored ones for the same date (a workout may have
    been edited since the last run). Returns the merged dict.
    """
    merged = dict(history)
    merged.update({k: round(v, 1) for k, v in daily_tss.items()})
    return merged


def _parse(d):
    return date.fromisoformat(d)


def compute_load_series(history):
    """Compute CTL / ATL / Form for every day from the first record to the last.

    Iterates day by day so gaps (days with no entry) correctly decay the averages with
    TSS = 0. Uses the spec formulas:

        CTL = prev_CTL * e^(-1/42) + tss * (1 - e^(-1/42))
        ATL = prev_ATL * e^(-1/7)  + tss * (1 - e^(-1/7))
        Form (TSB) = CTL - ATL

    Returns {date(str): {"ctl": float, "atl": float, "form": float}}.
    """
    if not history:
        return {}

    dates = sorted(history)
    cursor = _parse(dates[0])
    end = _parse(dates[-1])

    ctl_decay = math.exp(-1 / CTL_TIME_CONSTANT)
    atl_decay = math.exp(-1 / ATL_TIME_CONSTANT)

    ctl = 0.0
    atl = 0.0
    series = {}
    while cursor <= end:
        key = cursor.isoformat()
        tss = history.get(key, 0.0)
        ctl = ctl * ctl_decay + tss * (1 - ctl_decay)
        atl = atl * atl_decay + tss * (1 - atl_decay)
        series[key] = {
            "ctl": round(ctl, 1),
            "atl": round(atl, 1),
            "form": round(ctl - atl, 1),
        }
        cursor += timedelta(days=1)

    return series
