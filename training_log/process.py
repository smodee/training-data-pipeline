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

# Suunto's internal numeric activityId values, observed from real API responses.
# activityId 22 = outdoor run (has GPS polyline + step count).
# activityId 29 = has ascent/descent, no distance GPS — likely strength/gym.
# activityId 73 = no GPS/distance, indoor — likely gym/fitness.
# Run `suuntool workouts get <key> --format json` on known workouts to confirm/extend.
SUUNTO_ACTIVITY_ID_MAP = {
    22: "Run",
    29: "WeightTraining",
    73: "WeightTraining",
}

RUN_TYPES = {"Run", "TrailRun", "VirtualRun"}


def normalise_sport(raw):
    """Map a raw Suunto sport identifier (string or numeric activityId) to an internal key."""
    if raw is None:
        return "Unknown"
    if isinstance(raw, int):
        return SUUNTO_ACTIVITY_ID_MAP.get(raw, f"Activity{raw}")
    key = str(raw).strip().lower().replace(" ", "_")
    return SUUNTO_SPORT_MAP.get(key, raw)


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
    # activityId (int) comes from the list; string names may come from workouts get.
    raw_sport = _first(workout, "activityType", "sport", "sportType", "type", "activityId")
    sport = normalise_sport(raw_sport)

    distance_m = _first(workout, "totalDistance", "distance", default=0) or 0
    # totalTime confirmed from workouts list; duration/movingTime may appear in workouts get.
    moving_time_s = int(
        _first(workout, "totalTime", "duration", "movingTime", "moving_time", default=0) or 0
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

def process_wellness_sleep(sleep_records):
    """Process raw NDJSON sleep records for a single day into a wellness dict.

    ``sleep_records`` is the list of raw records from ``suunto.get_wellness_sleep``
    that share the same calendar date (from the ``timestamp`` field).

    Confirmed field shapes (from real API output):
    - ``entryData.duration`` — total sleep in seconds (float)
    - ``entryData.deepSleepDuration / lightSleepDuration / remSleepDuration`` — seconds
    - ``entryData.hrAvg / hrMin`` — beats *per second* (multiply × 60 for bpm)
    - ``entryData.quality`` — 0–1 fraction (multiply × 100 for %)
    - ``entryData.avgHrv`` — RMSSD in milliseconds
    - ``entryData.sleepId`` — groups incremental updates for one session
    - ``entryData.isNap`` — True for daytime naps (excluded)

    Returns None if no usable main-sleep record is found.
    """
    # Filter out naps.
    main = [r for r in sleep_records if not r.get("entryData", {}).get("isNap", False)]
    if not main:
        return None

    # De-duplicate by sleepId: for each session, keep the record with the largest
    # duration (suuntool sends incremental updates; the final one is most complete).
    by_id = {}
    for r in main:
        d = r.get("entryData", {})
        sid = d.get("sleepId")
        if sid is None:
            continue
        prev = by_id.get(sid)
        if prev is None or d.get("duration", 0) > prev.get("entryData", {}).get("duration", 0):
            by_id[sid] = r

    if not by_id:
        return None

    # If multiple sleep sessions on one day (unusual), pick the longest.
    best = max(by_id.values(), key=lambda r: r.get("entryData", {}).get("duration", 0))
    d = best["entryData"]

    duration_s = d.get("duration") or 0
    if not duration_s:
        return None

    deep = d.get("deepSleepDuration") or 0
    light = d.get("lightSleepDuration") or 0
    rem = d.get("remSleepDuration") or 0
    stages_total = deep + light + rem

    # hrAvg is beats/second; × 60 → bpm.
    hr_bps = d.get("hrAvg")
    sleep_hr = round(hr_bps * 60) if hr_bps else None

    return {
        "recovery_pct": None,  # filled in later by merge_recovery()
        "sleep_duration_s": int(duration_s),
        "sleep_quality_pct": round(d["quality"] * 100) if d.get("quality") is not None else None,
        "sleep_hr_avg": sleep_hr,
        "hrv_rmssd": d.get("avgHrv"),  # RMSSD ms, directly usable
        "deep_pct": round(deep / stages_total * 100) if stages_total else None,
        "rem_pct": round(rem / stages_total * 100) if stages_total else None,
        "light_pct": round(light / stages_total * 100) if stages_total else None,
    }


def merge_recovery(wellness, recovery_record):
    """Merge a recovery record into a wellness dict (modifies in-place, returns it).

    Recovery record shape is unverified — tolerant extraction is used.
    """
    if not wellness or not recovery_record:
        return wellness
    entry = recovery_record.get("entryData") or recovery_record
    score = _first(entry, "recovery", "recoveryBalance", "balance", "score", "value")
    if score is not None:
        try:
            v = float(score)
            # Suunto expresses recovery as 0–1; normalise to 0–100 %.
            wellness["recovery_pct"] = round(v * 100) if v <= 1.0 else round(v)
        except (TypeError, ValueError):
            pass
    return wellness


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
