"""Render processed data to Markdown reports."""

import os
from datetime import datetime, timedelta

from dateutil.parser import parse as parse_date


SPORT_LABELS = {
    "Run": "run",
    "TrailRun": "trail run",
    "VirtualRun": "virtual run",
    "Ride": "ride",
    "VirtualRide": "virtual ride",
    "MountainBikeRide": "MTB ride",
    "GravelRide": "gravel ride",
    "Swim": "swim",
    "Walk": "walk",
    "Hike": "hike",
    "BackcountrySki": "ski tour",
    "NordicSki": "XC ski",
    "AlpineSki": "alpine ski",
    "Snowboard": "snowboard",
    "Workout": "workout",
    "WeightTraining": "weight training",
    "Yoga": "yoga",
}


def _sport_label(sport_type):
    return SPORT_LABELS.get(sport_type, sport_type.lower())


def _format_zone_line(zone_pct):
    """Format zone distribution as inline string, omitting 0% zones."""
    parts = []
    for z in ("Z0", "Z1", "Z2", "Z3", "Z4"):
        pct = zone_pct.get(z, 0)
        if pct > 0:
            parts.append(f"{z} {pct}%")
    return " · ".join(parts)


def _sport_breakdown_count(sport_counts):
    """Format sport breakdown like '3 runs, 1 ski tour'."""
    parts = []
    for sport, count in sorted(sport_counts.items()):
        label = _sport_label(sport)
        if count > 1:
            # Simple pluralisation
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
    """Return (monday_date, sunday_date) for an ISO week."""
    jan4 = datetime(year, 1, 4)
    start = jan4 - timedelta(days=jan4.weekday())
    monday = start + timedelta(weeks=week - 1)
    sunday = monday + timedelta(days=6)
    return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")


def render_weekly_report(year, week, activities, summary):
    """Render a single weekly Markdown report."""
    mon_date, sun_date = _week_date_range(year, week)
    lines = []

    lines.append(f"# Training log — week {year}-W{week:02d} ({mon_date} – {sun_date})")
    lines.append("")
    lines.append("## Week summary")
    lines.append("")
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

    if summary["avg_suffer_score"] is not None:
        lines.append(f"- Avg suffer score: {summary['avg_suffer_score']}")

    lines.append("")
    lines.append("## Activities")
    lines.append("")

    for a in activities:
        tag = " [manual entry]" if a["manual"] else ""
        lines.append(f"### {a['date']} — {a['name']} ({_sport_label(a['sport_type'])}){tag}")
        lines.append("")

        # Distance / time / elevation
        metrics = []
        if a["distance_km"]:
            metrics.append(f"Distance: {a['distance_km']} km")
        metrics.append(f"Moving time: {a['moving_time_fmt']}")
        if a["elevation_gain"]:
            metrics.append(f"Elevation: +{a['elevation_gain']} m")
        lines.append(f"- {' | '.join(metrics)}")

        # Pace/speed and VAM
        secondary = []
        if a["pace"]:
            secondary.append(f"Avg pace: {a['pace']} /km")
        elif a["speed"]:
            secondary.append(f"Avg speed: {a['speed']} km/h")
        if a["vam"]:
            secondary.append(f"VAM: {a['vam']:,} m/h")
        if secondary:
            lines.append(f"- {' | '.join(secondary)}")

        # HR data
        if a["has_heartrate"] and not a["manual"]:
            hr_parts = []
            if a["avg_hr"]:
                hr_parts.append(f"avg {round(a['avg_hr'])} bpm")
            if a["max_hr"]:
                hr_parts.append(f"max {round(a['max_hr'])} bpm")
            if hr_parts:
                lines.append(f"- HR: {' / '.join(hr_parts)}")

            if a["zone_pct"]:
                lines.append(f"- Zone split: {_format_zone_line(a['zone_pct'])}")

        # Suffer score
        if a["suffer_score"] is not None:
            lines.append(f"- Suffer score: {a['suffer_score']}")

        # Description and private note
        if a.get("description"):
            lines.append(f"- Description: {a['description']}")
        if a.get("private_note"):
            lines.append(f"- Private note: {a['private_note']}")

        lines.append("")

    return "\n".join(lines)


def write_weekly_reports(weeks_data, output_dir):
    """Write weekly Markdown reports to disk."""
    os.makedirs(output_dir, exist_ok=True)
    written = []

    for (year, week), (activities, summary) in weeks_data.items():
        content = render_weekly_report(year, week, activities, summary)
        filename = f"training_log_{year}-W{week:02d}.md"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        written.append(filepath)

    return written


def write_monthly_reports(weeks_data, output_dir):
    """Write monthly Markdown reports (all weeks in a month combined)."""
    os.makedirs(output_dir, exist_ok=True)

    # Group by month
    from collections import defaultdict

    months = defaultdict(list)
    for (year, week), (activities, summary) in weeks_data.items():
        for a in activities:
            dt = parse_date(a["start_date_local"])
            months[(dt.year, dt.month)].append(a)

    written = []
    for (year, month), activities in sorted(months.items()):
        activities.sort(key=lambda a: a["start_date_local"])
        from .process import compute_weekly_summary

        summary = compute_weekly_summary(activities)

        lines = []
        lines.append(f"# Training log — {year}-{month:02d}")
        lines.append("")
        lines.append("## Month summary")
        lines.append("")
        lines.append(f"- Total time: {summary['total_time_fmt']}")
        lines.append(f"- Total distance: {summary['total_distance_km']} km")
        lines.append(f"- Total elevation: {summary['total_elevation']:,} m")
        act_line = f"- Activities: {summary['num_activities']}"
        if summary["sport_counts"]:
            act_line += f" ({_sport_breakdown_count(summary['sport_counts'])})"
        lines.append(act_line)
        if summary["zone_pct"]:
            lines.append(
                f"- HR zone distribution (% of tracked time): {_format_zone_line(summary['zone_pct'])}"
            )
        lines.append("")
        lines.append("## Activities")
        lines.append("")

        for a in activities:
            tag = " [manual entry]" if a["manual"] else ""
            lines.append(f"### {a['date']} — {a['name']} ({_sport_label(a['sport_type'])}){tag}")
            lines.append("")
            metrics = []
            if a["distance_km"]:
                metrics.append(f"Distance: {a['distance_km']} km")
            metrics.append(f"Moving time: {a['moving_time_fmt']}")
            if a["elevation_gain"]:
                metrics.append(f"Elevation: +{a['elevation_gain']} m")
            lines.append(f"- {' | '.join(metrics)}")

            secondary = []
            if a["pace"]:
                secondary.append(f"Avg pace: {a['pace']} /km")
            elif a["speed"]:
                secondary.append(f"Avg speed: {a['speed']} km/h")
            if a["vam"]:
                secondary.append(f"VAM: {a['vam']:,} m/h")
            if secondary:
                lines.append(f"- {' | '.join(secondary)}")

            if a["has_heartrate"] and not a["manual"]:
                hr_parts = []
                if a["avg_hr"]:
                    hr_parts.append(f"avg {round(a['avg_hr'])} bpm")
                if a["max_hr"]:
                    hr_parts.append(f"max {round(a['max_hr'])} bpm")
                if hr_parts:
                    lines.append(f"- HR: {' / '.join(hr_parts)}")
                if a["zone_pct"]:
                    lines.append(f"- Zone split: {_format_zone_line(a['zone_pct'])}")

            if a["suffer_score"] is not None:
                lines.append(f"- Suffer score: {a['suffer_score']}")

            if a.get("description"):
                lines.append(f"- Description: {a['description']}")
            if a.get("private_note"):
                lines.append(f"- Private note: {a['private_note']}")

            lines.append("")

        filename = f"training_log_{year}-{month:02d}.md"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        written.append(filepath)

    return written


def write_single_report(weeks_data, output_dir):
    """Write a single combined Markdown report for all fetched data."""
    os.makedirs(output_dir, exist_ok=True)

    all_activities = []
    for (year, week), (activities, summary) in sorted(weeks_data.items()):
        all_activities.extend(activities)

    if not all_activities:
        return []

    all_activities.sort(key=lambda a: a["start_date_local"])
    from .process import compute_weekly_summary

    summary = compute_weekly_summary(all_activities)

    first_date = all_activities[0]["date"]
    last_date = all_activities[-1]["date"]

    lines = []
    lines.append(f"# Training log — {first_date} to {last_date}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total time: {summary['total_time_fmt']}")
    lines.append(f"- Total distance: {summary['total_distance_km']} km")
    lines.append(f"- Total elevation: {summary['total_elevation']:,} m")
    act_line = f"- Activities: {summary['num_activities']}"
    if summary["sport_counts"]:
        act_line += f" ({_sport_breakdown_count(summary['sport_counts'])})"
    lines.append(act_line)
    if summary["zone_pct"]:
        lines.append(
            f"- HR zone distribution (% of tracked time): {_format_zone_line(summary['zone_pct'])}"
        )
    lines.append("")

    # Include weekly breakdowns
    for (year, week), (activities, week_summary) in sorted(weeks_data.items()):
        mon, sun = _week_date_range(year, week)
        lines.append(f"## Week {year}-W{week:02d} ({mon} – {sun})")
        lines.append("")
        lines.append(f"- Time: {week_summary['total_time_fmt']} | Distance: {week_summary['total_distance_km']} km | Elevation: {week_summary['total_elevation']:,} m")
        if week_summary["zone_pct"]:
            lines.append(f"- HR zones: {_format_zone_line(week_summary['zone_pct'])}")
        lines.append("")

        for a in activities:
            tag = " [manual entry]" if a["manual"] else ""
            lines.append(f"### {a['date']} — {a['name']} ({_sport_label(a['sport_type'])}){tag}")
            lines.append("")
            metrics = []
            if a["distance_km"]:
                metrics.append(f"Distance: {a['distance_km']} km")
            metrics.append(f"Moving time: {a['moving_time_fmt']}")
            if a["elevation_gain"]:
                metrics.append(f"Elevation: +{a['elevation_gain']} m")
            lines.append(f"- {' | '.join(metrics)}")

            secondary = []
            if a["pace"]:
                secondary.append(f"Avg pace: {a['pace']} /km")
            elif a["speed"]:
                secondary.append(f"Avg speed: {a['speed']} km/h")
            if a["vam"]:
                secondary.append(f"VAM: {a['vam']:,} m/h")
            if secondary:
                lines.append(f"- {' | '.join(secondary)}")

            if a["has_heartrate"] and not a["manual"]:
                hr_parts = []
                if a["avg_hr"]:
                    hr_parts.append(f"avg {round(a['avg_hr'])} bpm")
                if a["max_hr"]:
                    hr_parts.append(f"max {round(a['max_hr'])} bpm")
                if hr_parts:
                    lines.append(f"- HR: {' / '.join(hr_parts)}")
                if a["zone_pct"]:
                    lines.append(f"- Zone split: {_format_zone_line(a['zone_pct'])}")

            if a["suffer_score"] is not None:
                lines.append(f"- Suffer score: {a['suffer_score']}")

            if a.get("description"):
                lines.append(f"- Description: {a['description']}")
            if a.get("private_note"):
                lines.append(f"- Private note: {a['private_note']}")

            lines.append("")

    filename = f"training_log_{first_date}_to_{last_date}.md"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return [filepath]
