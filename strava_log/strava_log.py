#!/usr/bin/env python3
"""CLI entry point for Strava training log generator."""

import sys
from datetime import datetime, timedelta

import click
from dateutil.parser import parse as parse_date

from .config import load_config
from .auth import load_or_authorize
from .api import fetch_activities, fetch_activity_detail, fetch_hr_stream
from .process import process_activity, group_by_week, compute_weekly_summary
from .render import write_weekly_reports, write_monthly_reports, write_single_report


def _resolve_date_range(weeks, from_date, to_date):
    """Resolve the date range from CLI options. Returns (start_dt, end_dt)."""
    if to_date:
        end = parse_date(to_date)
    else:
        end = datetime.now()

    # Set end to end of day
    end = end.replace(hour=23, minute=59, second=59)

    if from_date:
        start = parse_date(from_date)
    else:
        # Go back N complete weeks from the most recent Monday
        today = end.date()
        # Find the most recent Monday (start of current week)
        days_since_monday = today.weekday()
        current_monday = today - timedelta(days=days_since_monday)
        start = datetime.combine(
            current_monday - timedelta(weeks=weeks), datetime.min.time()
        )

    return start, end


@click.command()
@click.option("--weeks", default=4, help="Fetch the last N complete weeks")
@click.option("--from", "from_date", default=None, help="Start date (YYYY-MM-DD, overrides --weeks)")
@click.option("--to", "to_date", default=None, help="End date (YYYY-MM-DD, default: today)")
@click.option("--output", default=None, help="Output directory for reports")
@click.option(
    "--format",
    "report_format",
    type=click.Choice(["weekly", "monthly", "single"]),
    default="weekly",
    help="Report granularity",
)
@click.option("--auth", is_flag=True, help="Force re-authentication")
@click.option("--quiet", is_flag=True, help="Suppress progress output")
def main(weeks, from_date, to_date, output, report_format, auth, quiet):
    """Pull Strava training data and generate Markdown reports."""
    cfg = load_config()

    output_dir = output or cfg["OUTPUT_DIR"]

    # Authenticate
    token_data = load_or_authorize(cfg, force_auth=auth)
    access_token = token_data["access_token"]

    # Resolve date range
    start, end = _resolve_date_range(weeks, from_date, to_date)
    if not quiet:
        print(f"Fetching activities from {start.date()} to {end.date()}...")

    # Fetch activities
    activities = fetch_activities(
        access_token, start.timestamp(), end.timestamp(), quiet=quiet
    )

    if activities is None:
        # 401 — try one token refresh
        print("Got 401, attempting token refresh...", file=sys.stderr)
        from .auth import refresh_tokens

        try:
            token_data = refresh_tokens(cfg, token_data)
            access_token = token_data["access_token"]
            activities = fetch_activities(
                access_token, start.timestamp(), end.timestamp(), quiet=quiet
            )
        except Exception:
            pass

        if activities is None:
            print(
                "Authentication failed. Re-run with --auth to re-authenticate.",
                file=sys.stderr,
            )
            sys.exit(1)

    if not activities:
        print("No activities found in the specified date range.")
        return

    if not quiet:
        print(f"Found {len(activities)} activities. Processing...")

    # Process each activity
    processed = []
    for activity in activities:
        hr_data, time_data = None, None

        if activity.get("has_heartrate") and not activity.get("manual"):
            hr_data, time_data = fetch_hr_stream(
                access_token, activity["id"], quiet=quiet
            )

        detail = fetch_activity_detail(access_token, activity["id"], quiet=quiet)

        processed.append(process_activity(activity, hr_data, time_data, cfg, detail=detail))

    # Group by week and compute summaries
    weeks_grouped = group_by_week(processed)
    weeks_data = {}
    for key, acts in weeks_grouped.items():
        summary = compute_weekly_summary(acts)
        weeks_data[key] = (acts, summary)

    # Write reports
    if report_format == "weekly":
        written = write_weekly_reports(weeks_data, output_dir)
    elif report_format == "monthly":
        written = write_monthly_reports(weeks_data, output_dir)
    else:
        written = write_single_report(weeks_data, output_dir)

    if not quiet:
        print(f"\nWrote {len(written)} report(s):")
        for path in written:
            print(f"  {path}")


if __name__ == "__main__":
    main()
