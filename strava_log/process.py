"""Processing: zone calculations, pace/speed, VAM, weekly aggregation."""

from collections import defaultdict
from datetime import datetime

from dateutil.parser import parse as parse_date

from .config import compute_zone_boundaries


def compute_zone_distribution(hr_data, time_data, cfg):
    """Compute seconds spent in each zone from HR and time arrays.

    Returns dict: {"Z0": seconds, "Z1": seconds, ...}
    """
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


def is_run_type(sport_type):
    """Check if sport type is a run/trail run."""
    return sport_type in ("Run", "TrailRun", "VirtualRun")


def process_activity(activity, hr_data, time_data, cfg):
    """Process a single activity and return an enriched dict."""
    sport = activity.get("sport_type") or activity.get("type", "Unknown")
    distance_m = activity.get("distance", 0)
    moving_time_s = activity.get("moving_time", 0)
    elevation = activity.get("total_elevation_gain", 0)
    manual = activity.get("manual", False)

    result = {
        "id": activity["id"],
        "name": activity.get("name", "Untitled"),
        "sport_type": sport,
        "date": parse_date(activity["start_date_local"]).strftime("%Y-%m-%d"),
        "start_date_local": activity["start_date_local"],
        "distance_km": round(distance_m / 1000, 1) if distance_m else 0,
        "moving_time_s": moving_time_s,
        "moving_time_fmt": f"{moving_time_s // 3600}:{(moving_time_s % 3600) // 60:02d}",
        "elapsed_time_s": activity.get("elapsed_time", 0),
        "elevation_gain": round(elevation) if elevation else 0,
        "avg_hr": activity.get("average_heartrate"),
        "max_hr": activity.get("max_heartrate"),
        "avg_cadence": activity.get("average_cadence"),
        "suffer_score": activity.get("suffer_score"),
        "manual": manual,
        "has_heartrate": activity.get("has_heartrate", False),
    }

    # Pace or speed
    if is_run_type(sport):
        pace = compute_pace(distance_m, moving_time_s)
        if pace:
            result["pace"] = f"{pace[0]}:{pace[1]:02d}"
        result["speed"] = None
    else:
        result["pace"] = None
        speed = compute_speed_kmh(distance_m, moving_time_s)
        result["speed"] = speed

    # VAM
    result["vam"] = compute_vam(elevation, moving_time_s)

    # Zone distribution
    if hr_data and time_data and not manual:
        zone_secs = compute_zone_distribution(hr_data, time_data, cfg)
        result["zone_seconds"] = zone_secs
        result["zone_pct"] = zone_seconds_to_pct(zone_secs)
    else:
        result["zone_seconds"] = None
        result["zone_pct"] = None

    return result


def group_by_week(activities):
    """Group processed activities by ISO week. Returns dict of (year, week) -> [activities]."""
    weeks = defaultdict(list)
    for a in activities:
        dt = parse_date(a["start_date_local"])
        iso = dt.isocalendar()
        weeks[(iso[0], iso[1])].append(a)

    # Sort activities within each week by date
    for key in weeks:
        weeks[key].sort(key=lambda a: a["start_date_local"])

    return dict(sorted(weeks.items()))


def compute_weekly_summary(activities):
    """Compute aggregate stats for a list of activities in a week."""
    total_time_s = sum(a["moving_time_s"] for a in activities)
    total_distance = sum(a["distance_km"] for a in activities)
    total_elevation = sum(a["elevation_gain"] for a in activities)

    # Breakdown by sport
    sport_counts = defaultdict(int)
    sport_distance = defaultdict(float)
    for a in activities:
        sport_counts[a["sport_type"]] += 1
        sport_distance[a["sport_type"]] += a["distance_km"]

    # Aggregate zone distribution
    agg_zone_seconds = defaultdict(int)
    has_any_hr = False
    for a in activities:
        if a["zone_seconds"]:
            has_any_hr = True
            for z, s in a["zone_seconds"].items():
                agg_zone_seconds[z] += s

    zone_pct = None
    if has_any_hr:
        zone_pct = zone_seconds_to_pct(dict(agg_zone_seconds))

    # Suffer score
    scores = [a["suffer_score"] for a in activities if a["suffer_score"] is not None]
    avg_suffer = round(sum(scores) / len(scores), 1) if scores else None

    return {
        "total_time_s": total_time_s,
        "total_time_fmt": f"{total_time_s // 3600}:{(total_time_s % 3600) // 60:02d}",
        "total_distance_km": round(total_distance, 1),
        "total_elevation": total_elevation,
        "num_activities": len(activities),
        "sport_counts": dict(sport_counts),
        "sport_distance": {k: round(v, 1) for k, v in sport_distance.items()},
        "zone_pct": zone_pct,
        "avg_suffer_score": avg_suffer,
    }
