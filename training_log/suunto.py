"""Wrapper around the suuntool CLI for fetching Suunto workout and wellness data.

`suuntool <https://github.com/tajchert/suuntool>`_ is an unofficial CLI / MCP server
that talks to the same backend API as the Suunto mobile app. This module shells out to
it; authentication is handled entirely by suuntool (``suuntool login``), so no OAuth
flow lives in this codebase.

Confirmed CLI surface (verified against v0.8.0)
------------------------------------------------
* Workouts:
    suuntool workouts list --since <YYYY-MM-DD> --format json
    suuntool workouts get  <key> --format json
    suuntool workouts comments <key> --format json
    suuntool workouts fit  <key> -o <path>

* Wellness (all return NDJSON — one JSON object per line):
    suuntool wellness sleep    --since <YYYY-MM-DD>
    suuntool wellness recovery --since <YYYY-MM-DD>

Still unverified
----------------
* Field names inside ``workouts get`` response (HR, TSS, EPOC, sport name).
  Field extraction uses tolerant ``_first`` calls so corrections are cheap.
* ``wellness recovery`` stdout shape — may need ``--out <dir>`` instead of stdout.
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime


class SuuntoolError(RuntimeError):
    """Raised when a suuntool invocation fails in a way we can't recover from."""


def _first(d, *keys, default=None):
    """Return the first present, non-None value among ``keys`` in dict ``d``."""
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _run(cfg, args, quiet=False, capture_json=True):
    """Run ``suuntool <args>`` and return parsed JSON or raw stdout string.

    Returns None on failure so callers can degrade gracefully.
    """
    cmd = [cfg["SUUNTOOL_PATH"], *args]
    if not quiet:
        print(f"  $ {' '.join(cmd)}", file=sys.stderr)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise SuuntoolError(
            f"suuntool not found at '{cfg['SUUNTOOL_PATH']}'. Install it and run "
            f"'suuntool login', or set SUUNTOOL_PATH in your .env."
        )
    except subprocess.TimeoutExpired:
        print(f"  Warning: suuntool timed out: {' '.join(cmd)}", file=sys.stderr)
        return None

    if proc.returncode != 0:
        print(
            f"  Warning: suuntool exited {proc.returncode}: {' '.join(cmd)}\n"
            f"  {proc.stderr.strip()}",
            file=sys.stderr,
        )
        return None

    if not capture_json:
        return proc.stdout

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(
            f"  Warning: could not parse JSON from: {' '.join(cmd)}",
            file=sys.stderr,
        )
        return None


def _run_ndjson(cfg, args, quiet=False):
    """Run suuntool and parse NDJSON (newline-delimited JSON) output.

    Returns a list of dicts. Blank lines and unparseable lines are silently skipped.
    """
    text = _run(cfg, args, quiet=quiet, capture_json=False)
    if not text:
        return []
    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return records


# --------------------------------------------------------------------------------------
# Workouts
# --------------------------------------------------------------------------------------

def list_workouts(cfg, start_date, end_date, quiet=False):
    """List workouts from ``start_date`` to ``end_date`` (both ``YYYY-MM-DD``, inclusive).

    suuntool's ``workouts list`` takes ``--since <date>`` for the start; there is no
    end-date flag, so we filter to ``end_date`` on the client side.

    Returns a list of raw workout summary dicts.
    """
    data = _run(
        cfg,
        ["workouts", "list", "--since", start_date, "--format", "json"],
        quiet=quiet,
    )
    if data is None:
        return []

    items = _first(data, "items", "workouts", "results", default=[]) if isinstance(data, dict) else data
    if not items:
        return []

    # Filter to end_date client-side; startTime is epoch milliseconds.
    filtered = []
    for w in items:
        ts = _first(w, "startTime", "start_time")
        if ts is not None:
            ts_sec = ts / 1000 if ts > 1e12 else ts
            w_date = datetime.fromtimestamp(ts_sec).strftime("%Y-%m-%d")
            if w_date > end_date:
                continue
        filtered.append(w)

    return filtered


def get_workout(cfg, workout_id, quiet=False):
    """Fetch the full detail object for a single workout.

    CLI: suuntool workouts get <key> --format json
    """
    data = _run(
        cfg,
        ["workouts", "get", str(workout_id), "--format", "json"],
        quiet=quiet,
    )
    if isinstance(data, dict):
        return _first(data, "workout", default=data)
    return data


def get_workout_notes(cfg, workout_id, quiet=False):
    """Return the user-written notes for a workout, or "".

    CLI: suuntool workouts comments <key> --format json

    The response may be a list of comment objects or a single object; we concatenate
    all text fields found.
    """
    data = _run(
        cfg,
        ["workouts", "comments", str(workout_id), "--format", "json"],
        quiet=quiet,
    )

    if isinstance(data, dict):
        data = _first(data, "comments", "items", default=data)

    if isinstance(data, list):
        parts = [_first(c, "text", "comment", "body", "message", default="") for c in data]
        return "\n".join(p for p in parts if p).strip()

    if isinstance(data, str):
        return data.strip()

    return ""


def download_fit(cfg, workout_id, dest_dir=None, quiet=False):
    """Download the FIT file for a workout. Returns the local path, or None.

    CLI: suuntool workouts fit <key> -o <path>
    """
    dest_dir = dest_dir or tempfile.gettempdir()
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"suunto_{workout_id}.fit")

    _run(
        cfg,
        ["workouts", "fit", str(workout_id), "-o", dest],
        quiet=quiet,
        capture_json=False,
    )
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    return None


# --------------------------------------------------------------------------------------
# Wellness
# --------------------------------------------------------------------------------------

def get_wellness_sleep(cfg, since_date, quiet=False):
    """Fetch all sleep records since ``since_date`` (``YYYY-MM-DD``).

    Returns a flat list of raw NDJSON records. Each record has the shape:
        {"timestamp": "<ISO8601+offset>", "entryData": { ... }}

    Multiple records may share the same ``sleepId`` (incremental updates for the same
    sleep session); callers should de-duplicate by taking the record with the largest
    ``entryData.duration`` for each ``sleepId``.

    CLI: suuntool wellness sleep --since <date>
    """
    return _run_ndjson(cfg, ["wellness", "sleep", "--since", since_date], quiet=quiet)


def get_wellness_recovery(cfg, since_date, quiet=False):
    """Fetch recovery data since ``since_date`` and group it by date.

    Returns ``{YYYY-MM-DD: raw_record}``.

    CLI: suuntool wellness recovery --since <date>

    NOTE: If this command writes to a directory (``--out ./dir``) rather than stdout,
    the return value will be {} and recovery data will simply be omitted from reports
    — non-fatal. Adjust the command here once confirmed.
    """
    records = _run_ndjson(cfg, ["wellness", "recovery", "--since", since_date], quiet=quiet)
    by_date = {}
    for r in records:
        ts = r.get("timestamp", "")
        if ts:
            by_date[ts[:10]] = r
    return by_date
