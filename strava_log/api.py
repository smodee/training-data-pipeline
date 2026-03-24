"""Strava API calls for activities and streams."""

import sys
import time

import requests


BASE_URL = "https://www.strava.com/api/v3"


def _request_with_retry(method, url, headers, params=None, max_retries=3):
    """Make an HTTP request with exponential backoff on network errors."""
    for attempt in range(max_retries + 1):
        try:
            resp = requests.request(
                method, url, headers=headers, params=params, timeout=30
            )

            if resp.status_code == 401:
                return resp  # let caller handle auth retry

            if resp.status_code == 429:
                if attempt == 0:
                    print("Rate limited (429). Waiting 60 seconds...", file=sys.stderr)
                    time.sleep(60)
                    continue
                else:
                    print(
                        "Rate limited again after retry. Exiting.",
                        file=sys.stderr,
                    )
                    sys.exit(1)

            resp.raise_for_status()
            return resp

        except requests.exceptions.RequestException as e:
            if attempt < max_retries:
                wait = 2 ** (attempt + 1)
                print(
                    f"Network error: {e}. Retrying in {wait}s...",
                    file=sys.stderr,
                )
                time.sleep(wait)
            else:
                print(f"Network error after {max_retries} retries: {e}", file=sys.stderr)
                raise


def fetch_activities(token, after_ts, before_ts, quiet=False):
    """Fetch all activities in the given time range. Returns list of activity dicts."""
    headers = {"Authorization": f"Bearer {token}"}
    all_activities = []
    page = 1

    while True:
        params = {
            "after": int(after_ts),
            "before": int(before_ts),
            "per_page": 100,
            "page": page,
        }

        resp = _request_with_retry("GET", f"{BASE_URL}/athlete/activities", headers, params)

        if resp.status_code == 401:
            return None  # signal auth failure

        activities = resp.json()
        if not activities:
            break

        all_activities.extend(activities)
        if not quiet:
            print(f"Fetched page {page} ({len(activities)} activities)")
        page += 1

    return all_activities


def fetch_activity_detail(token, activity_id, quiet=False):
    """Fetch detailed activity data (includes description and private_note).

    Returns dict with 'description' and 'private_note' keys, or empty strings if unavailable.
    """
    headers = {"Authorization": f"Bearer {token}"}

    if not quiet:
        print(f"  Fetching details for activity {activity_id}...")

    time.sleep(0.5)  # rate limit courtesy

    try:
        resp = _request_with_retry(
            "GET",
            f"{BASE_URL}/activities/{activity_id}",
            headers,
        )
    except requests.exceptions.RequestException:
        print(
            f"  Warning: failed to fetch details for activity {activity_id}",
            file=sys.stderr,
        )
        return {"description": "", "private_note": ""}

    if resp.status_code == 401:
        return {"description": "", "private_note": ""}

    try:
        data = resp.json()
        return {
            "description": data.get("description") or "",
            "private_note": data.get("private_note") or "",
        }
    except (ValueError, KeyError):
        return {"description": "", "private_note": ""}


def fetch_hr_stream(token, activity_id, quiet=False):
    """Fetch HR and time streams for an activity. Returns (heartrate_data, time_data) or (None, None)."""
    headers = {"Authorization": f"Bearer {token}"}
    params = {"keys": "heartrate,time", "key_by_type": "true"}

    if not quiet:
        print(f"  Fetching HR stream for activity {activity_id}...")

    time.sleep(0.5)  # rate limit courtesy

    try:
        resp = _request_with_retry(
            "GET",
            f"{BASE_URL}/activities/{activity_id}/streams",
            headers,
            params,
        )
    except requests.exceptions.RequestException:
        print(
            f"  Warning: failed to fetch stream for activity {activity_id}",
            file=sys.stderr,
        )
        return None, None

    if resp.status_code == 401:
        return None, None

    try:
        data = resp.json()
        hr_data = data.get("heartrate", {}).get("data")
        time_data = data.get("time", {}).get("data")
        if hr_data and time_data:
            return hr_data, time_data
        print(
            f"  Warning: malformed stream response for activity {activity_id}",
            file=sys.stderr,
        )
        return None, None
    except (ValueError, KeyError, AttributeError):
        print(
            f"  Warning: malformed stream response for activity {activity_id}",
            file=sys.stderr,
        )
        return None, None
