"""Wrapper around the suuntool CLI for fetching Suunto workout and wellness data.

`suuntool <https://github.com/tajchert/suuntool>`_ is an unofficial CLI / MCP server
that talks to the same backend API as the Suunto mobile app. This module shells out to
it; authentication is handled entirely by suuntool (``suuntool login``), so no OAuth
flow lives in this codebase.

IMPORTANT — unverified interface
--------------------------------
suuntool's exact subcommands, flags, and JSON shapes are undocumented and have NOT been
verified against a live install. To make the eventual correction cheap, two things are
centralised here:

* Subcommand / flag construction lives in the small ``_cmd_*`` helpers below.
* Field extraction is tolerant: ``_first`` tries several candidate keys and returns the
  first present. The candidate lists are the documented guesses; confirm and prune them
  against real output (see SPEC.md "Open Questions").
"""

import json
import os
import subprocess
import sys
import tempfile


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
    """Run ``suuntool <args>`` and return parsed JSON (or raw stdout if not JSON).

    Returns None on failure so callers can degrade gracefully (a missing wellness day
    or an un-fetchable workout should not abort the whole run).
    """
    cmd = [cfg["SUUNTOOL_PATH"], *args]
    if not quiet:
        print(f"  $ {' '.join(cmd)}", file=sys.stderr)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
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


# --------------------------------------------------------------------------------------
# Workouts
# --------------------------------------------------------------------------------------

def list_workouts(cfg, start_date, end_date, quiet=False):
    """List workouts between two ``YYYY-MM-DD`` dates (inclusive).

    Returns a list of raw workout dicts (summary objects). Assumed CLI:
        suuntool workouts list --from <date> --to <date> --format json
    """
    data = _run(
        cfg,
        ["workouts", "list", "--from", start_date, "--to", end_date, "--format", "json"],
        quiet=quiet,
    )
    if data is None:
        return []
    # Tolerate either a bare list or a wrapper object {"workouts": [...]}.
    if isinstance(data, dict):
        data = _first(data, "workouts", "items", "results", default=[])
    return data or []


def get_workout(cfg, workout_id, quiet=False):
    """Fetch the full detail object for a single workout.

    Assumed CLI: suuntool workouts get <id> --format json
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
    """Return the user-written session description/notes for a workout, or "".

    The notes may live on the workout detail object itself or behind a separate
    ``comments`` subcommand — this is Open Question #2 in the spec. We try the comments
    endpoint and fall back to common fields on the detail object.

    Assumed CLI: suuntool workouts comments <id> --format json
    """
    data = _run(
        cfg,
        ["workouts", "comments", str(workout_id), "--format", "json"],
        quiet=quiet,
    )

    if isinstance(data, dict):
        data = _first(data, "comments", "items", default=data)

    if isinstance(data, list):
        parts = [_first(c, "text", "comment", "body", default="") for c in data]
        return "\n".join(p for p in parts if p).strip()

    if isinstance(data, str):
        return data.strip()

    return ""


def download_fit(cfg, workout_id, dest_dir=None, quiet=False):
    """Download the FIT file for a workout. Returns the local path, or None.

    Assumed CLI: suuntool workouts get <id> --format fit --output <path>
    """
    dest_dir = dest_dir or tempfile.gettempdir()
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"suunto_{workout_id}.fit")

    result = _run(
        cfg,
        ["workouts", "get", str(workout_id), "--format", "fit", "--output", dest],
        quiet=quiet,
        capture_json=False,
    )
    if result is None:
        return None
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    return None


# --------------------------------------------------------------------------------------
# Wellness
# --------------------------------------------------------------------------------------

def get_sleep(cfg, date, quiet=False):
    """Fetch sleep summary for a date. Assumed: suuntool wellness sleep --date <date>."""
    return _run(cfg, ["wellness", "sleep", "--date", date, "--format", "json"], quiet=quiet)


def get_sleep_stages(cfg, date, quiet=False):
    """Fetch sleep-stage breakdown for a date.

    Assumed: suuntool wellness sleepstages --date <date>.
    """
    return _run(
        cfg, ["wellness", "sleepstages", "--date", date, "--format", "json"], quiet=quiet
    )


def get_recovery(cfg, date, quiet=False):
    """Fetch the recovery-balance score for a date.

    Assumed: suuntool wellness recovery --date <date>.
    """
    return _run(cfg, ["wellness", "recovery", "--date", date, "--format", "json"], quiet=quiet)
