"""List workouts in a range with the fields useful for identifying activityId values.

Prints start time, duration, distance, ascent, and (by default) the FIT-file
description for each workout — so unknown numeric activityId values can be matched
to a real sport.

Usage:
    python scripts/inspect_workouts.py 2026-01-01 2026-06-16
    python scripts/inspect_workouts.py 2026-01-01 2026-06-16 --no-fit   # skip FIT (no description)
"""

import sys
from datetime import datetime

sys.path.insert(0, ".")

from training_log.config import load_config
from training_log import suunto
from training_log.fit import parse_fit


def _start_str(w):
    ts = suunto._first(w, "startTime", "start_time")
    if ts is None:
        return "?"
    ts_sec = ts / 1000 if ts > 1e12 else ts
    return datetime.fromtimestamp(ts_sec).strftime("%Y-%m-%d %H:%M")


def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/inspect_workouts.py <since> <until> [--no-fit]")
        sys.exit(1)
    since, until = sys.argv[1], sys.argv[2]
    no_fit = "--no-fit" in sys.argv

    cfg = load_config()
    workouts = suunto.list_workouts(cfg, since, until, quiet=True)
    workouts.sort(key=lambda w: suunto._first(w, "startTime", "start_time") or 0)

    print(f"\n{len(workouts)} workout(s) {since} .. {until}:\n")
    for w in workouts:
        aid = suunto._first(w, "activityId", "activityType", "sport")
        dist_m = suunto._first(w, "totalDistance", "distance", default=0) or 0
        dur_s = int(suunto._first(w, "totalTime", "duration", default=0) or 0)
        asc = suunto._first(w, "totalAscent", "ascent", default=0) or 0
        key = suunto._first(w, "key", "id", "workoutId")

        desc = ""
        if not no_fit and key:
            fit_path = suunto.download_fit(cfg, key, quiet=True)
            if fit_path:
                _, _, desc, _ = parse_fit(fit_path)

        print(
            f"activityId {str(aid):>4} | {_start_str(w)} "
            f"| {dur_s // 60:>4} min | {dist_m / 1000:>5.1f} km | +{int(asc):>4} m "
            f"| desc: {desc!r}"
        )


if __name__ == "__main__":
    main()
