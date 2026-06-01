"""Parse FIT files downloaded from Suunto for detailed per-second streams.

Suunto workout summaries carry aggregate HR (avg/max) but not the full time series we
need for an accurate HR-zone distribution. The FIT file does, so when one is available
we parse its ``record`` messages here.

``fitparse`` is an optional dependency: if it isn't installed, callers fall back to the
summary HR and skip the zone split rather than crashing.
"""

import sys


def parse_hr_stream(fit_path):
    """Parse a FIT file into parallel (heartrate, time) arrays.

    ``time`` is seconds elapsed from the first record. Returns (None, None) if the file
    can't be parsed or has no HR data, so the caller can degrade gracefully.
    """
    try:
        from fitparse import FitFile
    except ImportError:
        print(
            "  Warning: fitparse not installed; skipping FIT stream parsing. "
            "Install with 'pip install fitparse'.",
            file=sys.stderr,
        )
        return None, None

    try:
        fitfile = FitFile(fit_path)
        timestamps = []
        heartrates = []

        for record in fitfile.get_messages("record"):
            values = {d.name: d.value for d in record}
            hr = values.get("heart_rate")
            ts = values.get("timestamp")
            if hr is None or ts is None:
                continue
            heartrates.append(hr)
            timestamps.append(ts)
    except Exception as e:  # fitparse raises a variety of parse errors
        print(f"  Warning: failed to parse FIT file {fit_path}: {e}", file=sys.stderr)
        return None, None

    if not heartrates:
        return None, None

    # Convert absolute timestamps to seconds elapsed from the first record.
    t0 = timestamps[0]
    time_data = [int((t - t0).total_seconds()) for t in timestamps]
    return heartrates, time_data
