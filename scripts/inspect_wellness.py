"""Inspect a date range of wellness data (sleep + recovery) the way the pipeline does.

Usage:
    python scripts/inspect_wellness.py 2026-06-09
    python scripts/inspect_wellness.py 2026-06-09 2026-06-16
"""

import sys
from collections import defaultdict

sys.path.insert(0, ".")

from training_log.config import load_config
from training_log import suunto
from training_log.process import process_wellness_sleep, merge_recovery


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/inspect_wellness.py <since-date> [until-date]")
        sys.exit(1)
    since = sys.argv[1]
    until = sys.argv[2] if len(sys.argv) > 2 else "9999-12-31"

    cfg = load_config()
    sleep = suunto.get_wellness_sleep(cfg, since)
    recovery = suunto.get_wellness_recovery(cfg, since)

    by_date = defaultdict(list)
    for r in sleep:
        ts = r.get("timestamp", "")
        if ts and since <= ts[:10] <= until:
            by_date[ts[:10]].append(r)

    print(f"\nProcessed wellness {since} .. {until}:\n")
    for date in sorted(by_date):
        records = by_date[date]
        w = process_wellness_sleep(records)
        if not w:
            print(f"{date}: (no usable sleep record from {len(records)} raw record(s))")
            continue
        merge_recovery(w, recovery.get(date))
        dur_h = w["sleep_duration_s"] // 3600
        dur_m = (w["sleep_duration_s"] % 3600) // 60
        print(
            f"{date}: sleep {dur_h}h{dur_m:02d} "
            f"| quality {w['sleep_quality_pct']}% "
            f"| recovery {w['recovery_pct']}% "
            f"| HR {w['sleep_hr_avg']} bpm "
            f"| HRV {w['hrv_rmssd']} "
            f"| deep {w['deep_pct']}% / rem {w['rem_pct']}% / light {w['light_pct']}% "
            f"| ({len(records)} raw record(s) merged)"
        )


if __name__ == "__main__":
    main()
