"""Parse FIT files downloaded from Suunto for HR streams and workout description.

Suunto workout summaries carry aggregate HR (avg/max) but not the full time series
needed for an accurate HR-zone distribution. Suunto also stores the workout description
(the free-text notes field in the app) in the FIT file rather than in the JSON API.

Both are extracted in a single pass through the file.

``fitparse`` is an optional dependency: if it isn't installed, callers fall back to
summary HR and skip the zone split and description rather than crashing.
"""

import sys


# FIT ``sub_sport`` values that carry no extra sport information — treated as absent
# so the token collapses to the bare ``sport`` name.
_GENERIC_SUB_SPORTS = {"generic", "all", "match", ""}


def _fit_sport_token(sport, sub_sport):
    """Combine a FIT session's ``sport``/``sub_sport`` enum into a lookup token.

    Returns ``"sport"`` or ``"sport/sub_sport"`` (lowercased), or ``None`` when the
    file carried no sport. The ANT/Garmin FIT sport vocabulary is a fixed standard,
    so callers can resolve this token without per-activity maintenance.
    """
    if sport is None:
        return None
    s = str(sport).strip().lower()
    if not s:
        return None
    sub = str(sub_sport).strip().lower() if sub_sport is not None else ""
    if sub in _GENERIC_SUB_SPORTS:
        return s
    return f"{s}/{sub}"


def _session_leg(values):
    """Build one multisport leg dict from a FIT ``session`` message's values."""
    tss = values.get("training_stress_score")
    try:
        tss = round(float(tss)) if tss is not None else None
    except (TypeError, ValueError):
        tss = None
    return {
        "sport": _fit_sport_token(values.get("sport"), values.get("sub_sport")),
        # FIT timestamps are UTC; callers place legs relative to the first one.
        "start_time": values.get("start_time"),
        # Timer time excludes pauses; the legs' timer times add up to Suunto's totalTime.
        "timer_time_s": values.get("total_timer_time"),
        "distance_m": values.get("total_distance"),
        "ascent_m": values.get("total_ascent"),
        "descent_m": values.get("total_descent"),
        "avg_hr": values.get("avg_heart_rate"),
        "max_hr": values.get("max_heart_rate"),
        "tss": tss,
    }


def parse_fit(fit_path):
    """Parse a FIT file and return ``(hr_data, time_data, description, tss, sport, legs)``.

    ``hr_data`` and ``time_data`` are parallel lists (bpm / seconds-elapsed), or
    ``(None, None)`` if no HR records are found.
    ``description`` is the workout's free-text notes string, or ``""`` if absent.
    ``tss`` is the training stress score from the FIT session message(s), summed
    across sessions for a multisport file, or ``None`` if no session carries one.
    ``sport`` is the standardised FIT sport token (``"sport"`` or
    ``"sport/sub_sport"``, see :func:`_fit_sport_token`) of the first session, or
    ``None`` if absent.
    ``legs`` is a list with one dict per ``session`` message (see
    :func:`_session_leg`). A normal workout has one; a Suunto multisport workout
    has one per sport segment (e.g. hike → climb → hike), in chronological order.

    Confirmed from real Suunto FIT output: the description is stored as a field
    named ``"description"`` on one of the FIT messages (visible as a developer
    data field in the binary export). TSS and the ``sport``/``sub_sport`` enum are
    stored on the session message. A multisport workout (Suunto ``activityId`` 68)
    exports a single file with one session message per segment and an ``activity``
    message whose ``num_sessions`` matches.
    """
    try:
        from fitparse import FitFile
    except ImportError:
        print(
            "  Warning: fitparse not installed; skipping FIT parsing. "
            "Install with 'pip install fitparse'.",
            file=sys.stderr,
        )
        return None, None, "", None, None, []

    try:
        fitfile = FitFile(fit_path)
        timestamps = []
        heartrates = []
        description = ""
        legs = []
        sport_raw = None
        sub_sport_raw = None

        for message in fitfile.get_messages():
            if message.name == "record":
                values = {d.name: d.value for d in message}
                hr = values.get("heart_rate")
                ts = values.get("timestamp")
                if hr is not None and ts is not None:
                    heartrates.append(hr)
                    timestamps.append(ts)

            # The standardised sport enum lives on the session message (and a
            # dedicated "sport" message); take it from whichever appears first.
            if message.name in ("session", "sport"):
                values = {d.name: d.value for d in message}
                if message.name == "session":
                    legs.append(_session_leg(values))
                if sport_raw is None and values.get("sport") is not None:
                    sport_raw = values.get("sport")
                    sub_sport_raw = values.get("sub_sport")

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
        return None, None, "", None, None, []

    leg_tss = [leg["tss"] for leg in legs if leg["tss"] is not None]
    tss = sum(leg_tss) if leg_tss else None

    hr_data, time_data = None, None
    if heartrates and timestamps:
        t0 = timestamps[0]
        time_data = [int((t - t0).total_seconds()) for t in timestamps]
        hr_data = heartrates

    sport = _fit_sport_token(sport_raw, sub_sport_raw)
    return hr_data, time_data, description, tss, sport, legs
