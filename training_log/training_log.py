#!/usr/bin/env python3
"""CLI entry point for the Suunto training log generator.

Pulls workout and wellness data from Suunto (via the suuntool CLI), computes HR-zone
distributions and training load (TSS / CTL / ATL / Form), and writes day-centric
Markdown training-diary reports.
"""

from collections import defaultdict
from datetime import datetime, timedelta

import click
from dateutil.parser import parse as parse_date

from . import suunto, tss_store
from .config import load_config
from .fit import parse_hr_stream
from .process import (
    build_days,
    compute_period_summary,
    group_days_by_month,
    group_days_by_week,
    merge_recovery,
    process_wellness_sleep,
    process_workout,
    summary_tss_by_date,
)
from .render import (
    write_monthly_reports,
    write_single_report,
    write_weekly_reports,
)

# How far back to seed the TSS history on first run so CTL starts from a realistic
# value (CTL has a 42-day time constant; 90 days lets it settle).
SEED_DAYS = 90


def _resolve_date_range(weeks, from_date, to_date):
    """Resolve the date range from CLI options. Returns (start_dt, end_dt)."""
    end = parse_date(to_date) if to_date else datetime.now()
    end = end.replace(hour=23, minute=59, second=59)

    if from_date:
        start = parse_date(from_date)
    else:
        today = end.date()
        current_monday = today - timedelta(days=today.weekday())
        start = datetime.combine(
            current_monday - timedelta(weeks=weeks), datetime.min.time()
        )

    return start, end


def _fetch_workouts(cfg, start, end, no_fit, quiet):
    """Fetch and richly process all workouts in [start, end]."""
    raw = suunto.list_workouts(
        cfg, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"), quiet=quiet
    )
    if not quiet:
        print(f"Found {len(raw)} workout(s). Processing...")

    processed = []
    for summary in raw:
        workout_id = suunto._first(summary, "id", "workoutId", "key")
        detail = suunto.get_workout(cfg, workout_id, quiet=quiet) or summary
        notes = suunto.get_workout_notes(cfg, workout_id, quiet=quiet)

        hr_data, time_data = None, None
        if not no_fit:
            fit_path = suunto.download_fit(cfg, workout_id, quiet=quiet)
            if fit_path:
                hr_data, time_data = parse_hr_stream(fit_path)

        processed.append(process_workout(detail, hr_data, time_data, notes, cfg))

    return processed


def _fetch_wellness(cfg, start, end, quiet):
    """Fetch and process wellness data for all days in [start, end].

    Fetches the full date range in two calls (sleep + recovery) rather than looping
    per day, then groups records by calendar date for assembly.
    """
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    # Sleep returns NDJSON; group records by the date portion of each timestamp.
    all_sleep = suunto.get_wellness_sleep(cfg, start_str, quiet=quiet)
    sleep_by_date = {}
    for record in all_sleep:
        ts = record.get("timestamp", "")
        if not ts:
            continue
        date_str = ts[:10]  # "2026-06-10" from "2026-06-10T01:42:00.000+02:00"
        if start_str <= date_str <= end_str:
            sleep_by_date.setdefault(date_str, []).append(record)

    # Recovery is also range-based; result is {date: record} already grouped.
    recovery_by_date = suunto.get_wellness_recovery(cfg, start_str, quiet=quiet)

    wellness_by_date = {}
    cur = start.date()
    last = end.date()
    while cur <= last:
        key = cur.isoformat()
        wellness = process_wellness_sleep(sleep_by_date.get(key, []))
        if wellness:
            merge_recovery(wellness, recovery_by_date.get(key))
            wellness_by_date[key] = wellness
        cur += timedelta(days=1)
    return wellness_by_date


def _seed_tss_history(cfg, history, start, quiet):
    """Seed CTL/ATL history with ~90 days of workout summaries before ``start``.

    Skips the fetch if the stored history already reaches back far enough.
    """
    seed_start = (start - timedelta(days=SEED_DAYS)).date()
    if history:
        earliest = min(history)
        if earliest <= seed_start.isoformat():
            return history

    if not quiet:
        print(f"Seeding training-load history from {seed_start}...")

    raw = suunto.list_workouts(
        cfg,
        seed_start.isoformat(),
        (start.date() - timedelta(days=1)).isoformat(),
        quiet=quiet,
    )
    seed_tss = summary_tss_by_date(raw, cfg)
    return tss_store.update_history(history, seed_tss)


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
@click.option("--no-fit", is_flag=True, help="Skip FIT download/parsing (no HR zone splits)")
@click.option("--no-wellness", is_flag=True, help="Skip wellness (sleep/recovery) fetching")
@click.option("--quiet", is_flag=True, help="Suppress progress output")
def main(weeks, from_date, to_date, output, report_format, no_fit, no_wellness, quiet):
    """Pull Suunto training data and generate day-centric Markdown reports."""
    cfg = load_config()
    output_dir = output or cfg["OUTPUT_DIR"]

    start, end = _resolve_date_range(weeks, from_date, to_date)
    if not quiet:
        print(f"Fetching Suunto data from {start.date()} to {end.date()}...")

    # Workouts (richly processed for the display range).
    workouts = _fetch_workouts(cfg, start, end, no_fit, quiet)

    # Wellness, per day.
    wellness_by_date = {} if no_wellness else _fetch_wellness(cfg, start, end, quiet)

    # Training-load history: seed if needed, fold in this range's TSS, compute series.
    history = tss_store.load_history(cfg["TSS_HISTORY_FILE"])
    history = _seed_tss_history(cfg, history, start, quiet)

    daily_tss = defaultdict(float)
    for w in workouts:
        if w["tss"]:
            daily_tss[w["date"]] += w["tss"]
    history = tss_store.update_history(history, dict(daily_tss))
    tss_store.save_history(cfg["TSS_HISTORY_FILE"], history)

    load_series = tss_store.compute_load_series(history)

    # Assemble day-centric structure.
    days = build_days(workouts, wellness_by_date, start, end, load_series)

    if report_format == "weekly":
        weeks_grouped = group_days_by_week(days)
        weeks_data = {
            key: (day_list, compute_period_summary(day_list))
            for key, day_list in weeks_grouped.items()
        }
        written = write_weekly_reports(weeks_data, output_dir)
    elif report_format == "monthly":
        months_grouped = group_days_by_month(days)
        months_data = {
            key: (day_list, compute_period_summary(day_list))
            for key, day_list in months_grouped.items()
        }
        written = write_monthly_reports(months_data, output_dir)
    else:
        summary = compute_period_summary(days)
        written = write_single_report(days, summary, output_dir)

    if not quiet:
        print(f"\nWrote {len(written)} report(s):")
        for path in written:
            print(f"  {path}")


if __name__ == "__main__":
    main()
