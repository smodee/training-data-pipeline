"""Render processed day-centric data to Markdown reports.

The format is a training diary: every day in the requested range gets a section anchored
by its wellness data, with any workouts nested beneath it. Weekly, monthly, and single
combined modes are supported, all sharing the per-day and summary rendering helpers.
"""

import os
from datetime import datetime, timedelta


SPORT_LABELS = {
    "Run": "run",
    "TrailRun": "trail run",
    "VirtualRun": "treadmill run",
    "Ride": "ride",
    "VirtualRide": "indoor ride",
    "MountainBikeRide": "MTB ride",
    "GravelRide": "gravel ride",
    "Swim": "swim",
    "OpenWaterSwim": "open water swim",
    "Walk": "walk",
    "Hike": "hike",
    "BackcountrySki": "ski tour",
    "NordicSki": "XC ski",
    "AlpineSki": "alpine ski",
    "Snowboard": "snowboard",
    "WeightTraining": "weight training",
    "CircuitTraining": "circuit training",
    "Climbing": "climbing",
    "Yoga": "yoga",
}


def _sport_label(sport_type):
    return SPORT_LABELS.get(sport_type, str(sport_type).lower())


def _format_zone_line(zone_pct):
    """Format zone distribution as inline string, omitting 0% zones."""
    parts = []
    for z in ("Z0", "Z1", "Z2", "Z3", "Z4"):
        pct = zone_pct.get(z, 0)
        if pct > 0:
            parts.append(f"{z} {pct}%")
    return " · ".join(parts)


def _format_hm(seconds):
    """Format a duration in seconds as 'Hh MM' style, e.g. 25560 -> '7h06'."""
    if not seconds:
        return None
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}h{minutes:02d}"


def _sport_breakdown_count(sport_counts):
    """Format sport breakdown like '3 runs, 1 ski tour'."""
    parts = []
    for sport, count in sorted(sport_counts.items()):
        label = _sport_label(sport)
        if count > 1:
            if label.endswith("ski"):
                label += "s"
            elif label.endswith("y"):
                label = label[:-1] + "ies"
            elif not label.endswith("s"):
                label += "s"
        parts.append(f"{count} {label}")
    return ", ".join(parts)


def _sport_breakdown_distance(sport_distance):
    """Format distance breakdown like '25.3 km running, 40.1 km cycling'."""
    parts = []
    for sport, dist in sorted(sport_distance.items()):
        if dist > 0:
            parts.append(f"{dist} km {_sport_label(sport)}")
    return ", ".join(parts)


def _week_date_range(year, week):
    """Return (monday_date, sunday_date) strings for an ISO week."""
    jan4 = datetime(year, 1, 4)
    start = jan4 - timedelta(days=jan4.weekday())
    monday = start + timedelta(weeks=week - 1)
    sunday = monday + timedelta(days=6)
    return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")


# --------------------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------------------

def _render_summary_block(summary, heading):
    """Render a '## <heading> summary' block shared by all report modes."""
    lines = [f"## {heading}", ""]
    lines.append(f"- Total time: {summary['total_time_fmt']}")

    dist_line = f"- Total distance: {summary['total_distance_km']} km"
    if len(summary["sport_distance"]) > 1:
        dist_line += f" ({_sport_breakdown_distance(summary['sport_distance'])})"
    lines.append(dist_line)

    lines.append(f"- Total elevation: {summary['total_elevation']:,} m")

    act_line = f"- Activities: {summary['num_activities']}"
    if summary["sport_counts"]:
        act_line += f" ({_sport_breakdown_count(summary['sport_counts'])})"
    lines.append(act_line)

    if summary["zone_pct"]:
        lines.append(
            f"- HR zone distribution (% of tracked time): {_format_zone_line(summary['zone_pct'])}"
        )

    if summary["total_tss"]:
        lines.append(f"- Total TSS: {summary['total_tss']}")

    if summary["load"]:
        load = summary["load"]
        lines.append(
            f"- CTL: {round(load['ctl'])} | ATL: {round(load['atl'])} | Form: {round(load['form'])}"
        )

    return lines


def _render_recovery_overview(summary):
    """Render the '## Recovery overview' block, or [] if no wellness data."""
    rec = summary.get("recovery")
    if not rec:
        return []

    lines = ["## Recovery overview", ""]
    if "avg_recovery" in rec:
        best_pct, best_day = rec["best_recovery"]
        worst_pct, worst_day = rec["worst_recovery"]
        lines.append(
            f"- Avg recovery: {rec['avg_recovery']}% | "
            f"Best: {best_pct}% ({best_day}) | Worst: {worst_pct}% ({worst_day})"
        )

    sleep_parts = []
    if "avg_sleep_quality" in rec:
        sleep_parts.append(f"Avg sleep quality: {rec['avg_sleep_quality']}%")
    if "avg_sleep_s" in rec:
        sleep_parts.append(f"Avg sleep: {_format_hm(rec['avg_sleep_s'])}")
    if sleep_parts:
        lines.append("- " + " | ".join(sleep_parts))

    return lines


def _render_recovery_line(wellness):
    """Render the bold per-day recovery line, or None if no wellness data."""
    if not wellness:
        return None

    parts = []
    if wellness.get("recovery_pct") is not None:
        parts.append(f"Recovery: {wellness['recovery_pct']}%")
    if wellness.get("sleep_duration_s"):
        parts.append(f"Sleep: {_format_hm(wellness['sleep_duration_s'])}")
    if wellness.get("sleep_quality_pct") is not None:
        parts.append(f"Quality: {wellness['sleep_quality_pct']}%")
    if wellness.get("deep_pct") is not None:
        parts.append(f"Deep: {wellness['deep_pct']}%")
    if wellness.get("rem_pct") is not None:
        parts.append(f"REM: {wellness['rem_pct']}%")
    if wellness.get("hrv_rmssd") is not None:
        parts.append(f"HRV: {round(float(wellness['hrv_rmssd']))} ms")

    if not parts:
        return None
    # First label is bolded to match the spec's "**Recovery:** ..." styling.
    first, rest = parts[0], parts[1:]
    label, _, value = first.partition(": ")
    line = f"**{label}:** {value}"
    if rest:
        line += " · " + " · ".join(rest)
    return line


def _render_workout(workout):
    """Render a single workout block (### heading + metric bullets)."""
    name = workout["name"]
    sport = workout["sport_type"]
    sport_lbl = _sport_label(sport)
    # When name is the sport-type key (i.e. no custom name was found in the API),
    # show just the human sport label rather than the redundant "Key (label)" form.
    if name and name != sport:
        heading = f"{name} ({sport_lbl})"
    else:
        heading = sport_lbl.title()
    lines = [f"### {heading}", ""]

    metrics = []
    if workout["distance_km"]:
        metrics.append(f"Distance: {workout['distance_km']} km")
    metrics.append(f"Moving time: {workout['moving_time_fmt']}")
    asc = workout["elevation_gain"]
    desc = workout.get("elevation_descent", 0)
    if asc or desc:
        elev = f"+{asc} m"
        if desc:
            elev += f" / -{desc} m"
        metrics.append(f"Elevation: {elev}")
    lines.append(f"- {' | '.join(metrics)}")

    secondary = []
    if workout["pace"]:
        secondary.append(f"Avg pace: {workout['pace']} /km")
    elif workout["speed"]:
        secondary.append(f"Avg speed: {workout['speed']} km/h")
    if workout["vam"]:
        secondary.append(f"VAM: {workout['vam']:,} m/h")
    if secondary:
        lines.append(f"- {' | '.join(secondary)}")

    # HR (avg/max) and zone split share one line per the spec example.
    if workout["has_heartrate"]:
        hr_parts = []
        if workout["avg_hr"]:
            hr_parts.append(f"avg {round(workout['avg_hr'])} bpm")
        if workout["max_hr"]:
            hr_parts.append(f"max {round(workout['max_hr'])} bpm")
        hr_line = ""
        if hr_parts:
            hr_line = f"HR: {' / '.join(hr_parts)}"
        if workout["zone_pct"]:
            zone_str = f"Zone split: {_format_zone_line(workout['zone_pct'])}"
            hr_line = f"{hr_line} | {zone_str}" if hr_line else zone_str
        if hr_line:
            lines.append(f"- {hr_line}")

    # Load metrics: TSS primary, EPOC secondary, on one line.
    load_parts = []
    if workout["tss"] is not None:
        load_parts.append(f"TSS: {workout['tss']}")
    if workout["epoc"] is not None:
        load_parts.append(f"EPOC: {workout['epoc']} ml/kg")
    if load_parts:
        lines.append(f"- {' | '.join(load_parts)}")

    if workout["notes"]:
        lines.append(f"- Notes: {workout['notes']}")

    return lines


def _render_day(day):
    """Render one day section: heading, recovery line, workouts (or rest day)."""
    lines = [f"## {day['weekday']}, {day['date']}", ""]

    recovery_line = _render_recovery_line(day["wellness"])
    if recovery_line:
        lines.append(recovery_line)
        lines.append("")

    if day["workouts"]:
        for workout in day["workouts"]:
            lines.extend(_render_workout(workout))
            lines.append("")
    else:
        lines.append("*Rest day*")
        lines.append("")

    lines.append("---")
    lines.append("")
    return lines


# --------------------------------------------------------------------------------------
# Report assembly
# --------------------------------------------------------------------------------------

def _render_report(title, summary_heading, summary, days):
    """Assemble a full report from a title, summary, optional recovery block, and days."""
    lines = [f"# {title}", ""]
    lines.extend(_render_summary_block(summary, summary_heading))
    lines.append("")
    recovery = _render_recovery_overview(summary)
    if recovery:
        lines.extend(recovery)
        lines.append("")
    lines.append("---")
    lines.append("")
    for day in days:
        lines.extend(_render_day(day))
    return "\n".join(lines).rstrip() + "\n"


def _write(output_dir, filename, content):
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath


def write_weekly_reports(weeks_data, output_dir):
    """Write one Markdown file per ISO week. weeks_data: {(year, week): (days, summary)}."""
    written = []
    for (year, week), (days, summary) in weeks_data.items():
        mon, sun = _week_date_range(year, week)
        title = f"Training log — week {year}-W{week:02d} ({mon} – {sun})"
        content = _render_report(title, "Week summary", summary, days)
        written.append(_write(output_dir, f"training_log_{year}-W{week:02d}.md", content))
    return written


def write_monthly_reports(months_data, output_dir):
    """Write one Markdown file per month. months_data: {(year, month): (days, summary)}."""
    written = []
    for (year, month), (days, summary) in months_data.items():
        title = f"Training log — {year}-{month:02d}"
        content = _render_report(title, "Month summary", summary, days)
        written.append(_write(output_dir, f"training_log_{year}-{month:02d}.md", content))
    return written


def write_single_report(days, summary, output_dir):
    """Write a single combined report across the whole fetched range."""
    if not days:
        return []
    first_date = days[0]["date"]
    last_date = days[-1]["date"]
    title = f"Training log — {first_date} to {last_date}"
    content = _render_report(title, "Summary", summary, days)
    filename = f"training_log_{first_date}_to_{last_date}.md"
    return [_write(output_dir, filename, content)]
