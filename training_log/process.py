"""Processing: zone calculations, pace/speed, VAM, TSS/EPOC, wellness, day grouping.

This module turns the raw dicts returned by :mod:`training_log.suunto` into the enriched
day-centric structures the renderer consumes. Field extraction from Suunto objects goes
through the ``_w`` / ``_first`` helpers so the (unverified) candidate key names are easy
to correct in one place — see SPEC.md "Open Questions".
"""

from collections import defaultdict
from datetime import date, datetime, timedelta

from dateutil.parser import parse as parse_date

from .config import compute_zone_boundaries
from .suunto import _first


# --------------------------------------------------------------------------------------
# HR zones (unchanged from the Strava implementation — physiology, not data source)
# --------------------------------------------------------------------------------------

def compute_zone_distribution(hr_data, time_data, cfg):
    """Compute seconds spent in each zone from HR and time arrays."""
    zones = compute_zone_boundaries(cfg)
    zone_seconds = {z[0]: 0 for z in zones}

    for i in range(1, len(hr_data)):
        bpm = hr_data[i]
        dt = time_data[i] - time_data[i - 1]
        if dt <= 0:
            continue

        for name, lower, upper in zones:
            in_zone = True
            if lower is not None and bpm < lower:
                in_zone = False
            if upper is not None and bpm > upper:
                in_zone = False
            if in_zone:
                zone_seconds[name] += dt
                break

    return zone_seconds


def zone_seconds_to_pct(zone_seconds):
    """Convert zone seconds dict to percentage dict."""
    total = sum(zone_seconds.values())
    if total == 0:
        return {k: 0.0 for k in zone_seconds}
    return {k: round(v / total * 100, 1) for k, v in zone_seconds.items()}


def compute_pace(distance_m, moving_time_s):
    """Compute pace in min/km. Returns (minutes, seconds) tuple or None."""
    if not distance_m or distance_m == 0:
        return None
    pace_s_per_km = moving_time_s / (distance_m / 1000)
    minutes = int(pace_s_per_km // 60)
    seconds = int(pace_s_per_km % 60)
    return minutes, seconds


def compute_speed_kmh(distance_m, moving_time_s):
    """Compute average speed in km/h."""
    if not distance_m or not moving_time_s or moving_time_s == 0:
        return None
    return round(distance_m / 1000 / (moving_time_s / 3600), 1)


def compute_vam(elevation_gain, moving_time_s):
    """Compute VAM (vertical ascent metres per hour)."""
    if not elevation_gain or elevation_gain < 50 or not moving_time_s or moving_time_s == 0:
        return None
    return round(elevation_gain / (moving_time_s / 3600))


# --------------------------------------------------------------------------------------
# Suunto sport-type handling
# --------------------------------------------------------------------------------------

# Suunto activity identifiers differ from Strava's. This maps the ones we expect to a
# normalised internal key; the renderer turns the key into a human label. Unknown types
# pass through unchanged so they still render (just without a pretty label).
SUUNTO_SPORT_MAP = {
    "running": "Run",
    "trail_running": "TrailRun",
    "treadmill": "VirtualRun",
    "cycling": "Ride",
    "indoor_cycling": "VirtualRide",
    "mountain_biking": "MountainBikeRide",
    "gravel_cycling": "GravelRide",
    "swimming": "Swim",
    "open_water_swimming": "OpenWaterSwim",
    "pool_swimming": "Swim",
    "walking": "Walk",
    "hiking": "Hike",
    "trekking": "Hike",
    "ski_touring": "BackcountrySki",
    "cross_country_skiing": "NordicSki",
    "downhill_skiing": "AlpineSki",
    "snowboarding": "Snowboard",
    "gym": "WeightTraining",
    "strength_training": "WeightTraining",
    "weight_training": "WeightTraining",
    "yoga": "Yoga",
}

RUN_TYPES = {"Run", "TrailRun", "VirtualRun"}


def normalise_sport(suunto_sport):
    """Map a raw Suunto sport identifier to our internal sport key."""
    if not suunto_sport:
        return "Unknown"
    key = str(suunto_sport).strip().lower().replace(" ", "_")
    return SUUNTO_SPORT_MAP.get(key, suunto_sport)


def is_run_type(sport_type):
    return sport_type in RUN_TYPES


# --------------------------------------------------------------------------------------
# TSS / EPOC extraction
# --------------------------------------------------------------------------------------

def extract_tss(workout, hr_data, time_data, moving_time_s, avg_hr, cfg):
    """Return TSS for a workout.

    Prefers a native value from the Suunto object (Open Question #1: confirm the field
    name). Falls back to an hrTSS estimate from average HR vs. threshold HR when no
    native value is present.
    """
    native = _first(
        workout, "tss", "trainingStressScore", "training_stress_score", "trainingStress"
    )
    if native is not None:
        try:
            return round(float(native))
        except (TypeError, ValueError):
            pass

    return estimate_hr_tss(moving_time_s, avg_hr, cfg)


def estimate_hr_tss(moving_time_s, avg_hr, cfg):
    """Estimate TSS from average HR (hrTSS), used when no native TSS is available.

    hrTSS ≈ duration_hours * IF^2 * 100, with IF = avg_HR / threshold_HR. This is a
    coarse approximation (it ignores intra-session variability) but keeps CTL/ATL
    meaningful for sessions that lack a native score.
    """
    if not moving_time_s or not avg_hr:
        return None
    threshold = cfg["THRESHOLD_HR"]
    if not threshold:
        return None
    intensity = avg_hr / threshold
    hours = moving_time_s / 3600
    return round(hours * intensity * intensity * 100)


def summary_tss_by_date(raw_workouts, cfg):
    """Compute ``{date: total_tss}`` from raw workout *summaries* (no FIT/HR streams).

    Used to cheaply seed the CTL/ATL history for dates outside the richly-rendered
    display range. TSS comes from the native field when present, else an hrTSS estimate
    from the summary's average HR.
    """
    daily = defaultdict(float)
    for w in raw_workouts:
        try:
            start = _workout_start(w)
        except KeyError:
            continue
        moving = int(
            _first(w, "duration", "movingTime", "moving_time", "totalTime", default=0) or 0
        )
        avg_hr = _first(w, "avgHr", "averageHeartRate", "avgHeartRate", "average_heartrate")
        tss = extract_tss(w, None, None, moving, avg_hr, cfg)
        if tss:
            daily[start.strftime("%Y-%m-%d")] += tss
    return {k: round(v, 1) for k, v in daily.items()}


def extract_epoc(workout):
    """Return peak EPOC (ml/kg) for a workout if present, else None."""
    epoc = _first(workout, "epoc", "peakEpoc", "peak_epoc", "EPOC")
    if epoc is None:
        return None
    try:
        return round(float(epoc))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------------------
# Workout processing
# --------------------------------------------------------------------------------------

def _workout_start(workout):
    """Return the workout's local start as a datetime, tolerating field-name variation."""
    raw = _first(
        workout, "startTime", "start_time", "startDate", "start_date_local", "timestamp"
    )
    if raw is None:
        raise KeyError("workout has no recognisable start-time field")
    if isinstance(raw, (int, float)):
        # Suunto often uses epoch milliseconds.
        ts = raw / 1000 if raw > 1e12 else raw
        return datetime.fromtimestamp(ts)
    return parse_date(str(raw))


def process_workout(workout, hr_data, time_data, notes, cfg):
    """Process a single Suunto workout dict into an enriched, render-ready dict."""
    raw_sport = _first(workout, "activityType", "sport", "sportType", "type")
    sport = normalise_sport(raw_sport)

    distance_m = _first(workout, "totalDistance", "distance", default=0) or 0
    moving_time_s = int(
        _first(workout, "duration", "movingTime", "moving_time", "totalTime", default=0) or 0
    )
    elevation = _first(workout, "totalAscent", "ascent", "elevationGain", default=0) or 0
    avg_hr = _first(workout, "avgHr", "averageHeartRate", "avgHeartRate", "average_heartrate")
    max_hr = _first(workout, "maxHr", "maxHeartRate", "max_heartrate")
    avg_cadence = _first(workout, "avgCadence", "averageCadence")

    start_dt = _workout_start(workout)

    result = {
        "id": _first(workout, "id", "workoutId", "key"),
        "name": _first(workout, "name", "title", default="Untitled"),
        "notes": (notes or "").strip(),
        "sport_type": sport,
        "date": start_dt.strftime("%Y-%m-%d"),
        "start_dt": start_dt,
        "distance_km": round(distance_m / 1000, 1) if distance_m else 0,
        "moving_time_s": moving_time_s,
        "moving_time_fmt": f"{moving_time_s // 3600}:{(moving_time_s % 3600) // 60:02d}",
        "elevation_gain": round(elevation) if elevation else 0,
        "avg_hr": avg_hr,
        "max_hr": max_hr,
        "avg_cadence": avg_cadence,
        "epoc": extract_epoc(workout),
        "has_heartrate": avg_hr is not None or bool(hr_data),
    }

    # Pace or speed
    if is_run_type(sport):
        pace = compute_pace(distance_m, moving_time_s)
        result["pace"] = f"{pace[0]}:{pace[1]:02d}" if pace else None
        result["speed"] = None
    else:
        result["pace"] = None
        result["speed"] = compute_speed_kmh(distance_m, moving_time_s)

    result["vam"] = compute_vam(elevation, moving_time_s)

    # Zone distribution from the FIT stream when we have it.
    if hr_data and time_data:
        zone_secs = compute_zone_distribution(hr_data, time_data, cfg)
        result["zone_seconds"] = zone_secs
        result["zone_pct"] = zone_seconds_to_pct(zone_secs)
    else:
        result["zone_seconds"] = None
        result["zone_pct"] = None

    result["tss"] = extract_tss(workout, hr_data, time_data, moving_time_s, avg_hr, cfg)

    return result


# --------------------------------------------------------------------------------------
# Wellness processing
# --------------------------------------------------------------------------------------

def _pct(value):
    """Normalise a 0–1 fraction or 0–100 value to an integer percentage, or None."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 1.0:
        v *= 100
    return round(v)


def process_wellness(sleep, sleep_stages, recovery):
    """Combine the three wellness payloads for one day into a single dict.

    Returns None if no usable data is present, so the renderer can omit the recovery
    line entirely on days the watch wasn't worn.
    """
    out = {}

    # Recovery balance (Suunto's HRV-derived readiness), expressed 0–1 in raw JSON.
    rec = _first(recovery or {}, "recovery", "recoveryBalance", "balance", "score")
    out["recovery_pct"] = _pct(rec)

    # Sleep duration / quality / sleeping HR.
    dur = _first(sleep or {}, "sleepDuration", "duration", "totalSleep", "asleepTime")
    out["sleep_duration_s"] = int(dur) if dur is not None else None
    out["sleep_quality_pct"] = _pct(
        _first(sleep or {}, "quality", "sleepQuality", "qualityIndex")
    )
    out["sleep_hr_avg"] = _first(sleep or {}, "avgHr", "averageHeartRate", "sleepHr")

    # Optional raw HRV (Open Question #3) — included only if Suunto exposes it.
    out["hrv_rmssd"] = _first(sleep or {}, "hrv", "rmssd", "hrvRmssd")

    # Sleep-stage split, normalised to percentages of total sleep.
    stages = sleep_stages or {}
    deep = _first(stages, "deep", "deepSleep", "deepPct")
    rem = _first(stages, "rem", "remSleep", "remPct")
    light = _first(stages, "light", "lightSleep", "lightPct")
    out["deep_pct"] = _stage_pct(deep, deep, rem, light)
    out["rem_pct"] = _stage_pct(rem, deep, rem, light)
    out["light_pct"] = _stage_pct(light, deep, rem, light)

    if all(v is None for v in out.values()):
        return None
    return out


def _stage_pct(value, deep, rem, light):
    """Return a sleep stage as a percentage of total sleep.

    Normalising by the total of the three stages handles every unit Suunto might use —
    0–1 fractions, 0–100 percentages, or raw second counts — with one expression.
    """
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None

    total = sum(float(x) for x in (deep, rem, light) if x is not None)
    if not total:
        return None
    return round(value / total * 100)


# --------------------------------------------------------------------------------------
# Day-centric assembly
# --------------------------------------------------------------------------------------

def _date_range(start_dt, end_dt):
    """Yield every date (inclusive) between two datetimes."""
    cur = start_dt.date() if isinstance(start_dt, datetime) else start_dt
    last = end_dt.date() if isinstance(end_dt, datetime) else end_dt
    while cur <= last:
        yield cur
        cur += timedelta(days=1)


def build_days(workouts, wellness_by_date, start_dt, end_dt, load_series):
    """Build an ordered list of day dicts covering the full requested range.

    Every day in [start, end] gets an entry whether or not a workout happened, so the
    output is a true diary. Each day carries its wellness data, its workouts, the day's
    summed TSS, and the CTL/ATL/Form snapshot for that date.
    """
    workouts_by_date = defaultdict(list)
    for w in workouts:
        workouts_by_date[w["date"]].append(w)
    for acts in workouts_by_date.values():
        acts.sort(key=lambda a: a["start_dt"])

    days = []
    for d in _date_range(start_dt, end_dt):
        key = d.isoformat()
        day_workouts = workouts_by_date.get(key, [])
        day_tss = sum(w["tss"] for w in day_workouts if w["tss"])
        days.append(
            {
                "date": key,
                "weekday": d.strftime("%A"),
                "iso": d.isocalendar(),
                "wellness": wellness_by_date.get(key),
                "workouts": day_workouts,
                "tss": round(day_tss) if day_tss else 0,
                "load": load_series.get(key),
            }
        )
    return days


def group_days_by_week(days):
    """Group day dicts by ISO (year, week). Returns ordered dict."""
    weeks = defaultdict(list)
    for day in days:
        iso = day["iso"]
        weeks[(iso[0], iso[1])].append(day)
    return dict(sorted(weeks.items()))


def group_days_by_month(days):
    """Group day dicts by (year, month). Returns ordered dict."""
    months = defaultdict(list)
    for day in days:
        y, m, _ = day["date"].split("-")
        months[(int(y), int(m))].append(day)
    return dict(sorted(months.items()))


# --------------------------------------------------------------------------------------
# Summaries
# --------------------------------------------------------------------------------------

def compute_period_summary(days):
    """Aggregate stats across a list of day dicts (a week or a month)."""
    workouts = [w for day in days for w in day["workouts"]]

    total_time_s = sum(w["moving_time_s"] for w in workouts)
    total_distance = sum(w["distance_km"] for w in workouts)
    total_elevation = sum(w["elevation_gain"] for w in workouts)

    sport_counts = defaultdict(int)
    sport_distance = defaultdict(float)
    for w in workouts:
        sport_counts[w["sport_type"]] += 1
        sport_distance[w["sport_type"]] += w["distance_km"]

    agg_zone_seconds = defaultdict(int)
    has_any_hr = False
    for w in workouts:
        if w["zone_seconds"]:
            has_any_hr = True
            for z, s in w["zone_seconds"].items():
                agg_zone_seconds[z] += s
    zone_pct = zone_seconds_to_pct(dict(agg_zone_seconds)) if has_any_hr else None

    total_tss = sum(w["tss"] for w in workouts if w["tss"])

    # Form snapshot = the load values on the last day of the period.
    last_load = None
    for day in reversed(days):
        if day["load"]:
            last_load = day["load"]
            break

    return {
        "total_time_s": total_time_s,
        "total_time_fmt": f"{total_time_s // 3600}:{(total_time_s % 3600) // 60:02d}",
        "total_distance_km": round(total_distance, 1),
        "total_elevation": total_elevation,
        "num_activities": len(workouts),
        "sport_counts": dict(sport_counts),
        "sport_distance": {k: round(v, 1) for k, v in sport_distance.items()},
        "zone_pct": zone_pct,
        "total_tss": round(total_tss) if total_tss else 0,
        "load": last_load,
        "recovery": _recovery_overview(days),
    }


def _recovery_overview(days):
    """Summarise recovery and sleep across the period for the 'Recovery overview' block."""
    recoveries = []  # (pct, weekday_abbr)
    sleep_durations = []
    sleep_qualities = []
    for day in days:
        w = day["wellness"]
        if not w:
            continue
        if w.get("recovery_pct") is not None:
            recoveries.append((w["recovery_pct"], day["date"], day["weekday"][:3]))
        if w.get("sleep_duration_s"):
            sleep_durations.append(w["sleep_duration_s"])
        if w.get("sleep_quality_pct") is not None:
            sleep_qualities.append(w["sleep_quality_pct"])

    if not recoveries and not sleep_durations:
        return None

    overview = {}
    if recoveries:
        best = max(recoveries, key=lambda r: r[0])
        worst = min(recoveries, key=lambda r: r[0])
        overview["avg_recovery"] = round(sum(r[0] for r in recoveries) / len(recoveries))
        overview["best_recovery"] = (best[0], best[2])
        overview["worst_recovery"] = (worst[0], worst[2])
    if sleep_qualities:
        overview["avg_sleep_quality"] = round(sum(sleep_qualities) / len(sleep_qualities))
    if sleep_durations:
        overview["avg_sleep_s"] = round(sum(sleep_durations) / len(sleep_durations))
    return overview
