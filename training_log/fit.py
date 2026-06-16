"""Parse FIT files downloaded from Suunto for HR streams and workout description.

Suunto workout summaries carry aggregate HR (avg/max) but not the full time series
needed for an accurate HR-zone distribution. Suunto also stores the workout description
(the free-text notes field in the app) in the FIT file rather than in the JSON API.

Both are extracted in a single pass through the file.

``fitparse`` is an optional dependency: if it isn't installed, callers fall back to
summary HR and skip the zone split and description rather than crashing.
"""

import sys


def parse_fit(fit_path):
    """Parse a FIT file and return ``(hr_data, time_data, description)``.

    ``hr_data`` and ``time_data`` are parallel lists (bpm / seconds-elapsed), or
    ``(None, None)`` if no HR records are found.
    ``description`` is the workout's free-text notes string, or ``""`` if absent.

    Confirmed from real Suunto FIT output: the description is stored as a field
    named ``"description"`` on one of the FIT messages (visible as a developer
    data field in the binary export).
    """
    try:
        from fitparse import FitFile
    except ImportError:
        print(
            "  Warning: fitparse not installed; skipping FIT parsing. "
            "Install with 'pip install fitparse'.",
            file=sys.stderr,
        )
        return None, None, ""

    try:
        fitfile = FitFile(fit_path)
        timestamps = []
        heartrates = []
        description = ""

        for message in fitfile.get_messages():
            if message.name == "record":
                values = {d.name: d.value for d in message}
                hr = values.get("heart_rate")
                ts = values.get("timestamp")
                if hr is not None and ts is not None:
                    heartrates.append(hr)
                    timestamps.append(ts)

            # Description may appear on any message type as a developer data field.
            if not description:
                for field in message.fields:
                    if "description" in str(field.name).lower():
                        val = field.value
                        if val and isinstance(val, str) and val.strip():
                            description = val.strip()
                            break

    except Exception as e:
        print(f"  Warning: failed to parse FIT file {fit_path}: {e}", file=sys.stderr)
        return None, None, ""

    hr_data, time_data = None, None
    if heartrates and timestamps:
        t0 = timestamps[0]
        time_data = [int((t - t0).total_seconds()) for t in timestamps]
        hr_data = heartrates

    return hr_data, time_data, description
