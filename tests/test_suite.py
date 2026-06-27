"""Automated test suite for training-data-pipeline (no suuntool / real FIT required).

Run with:  python -m pytest tests/test_suite.py -v
"""

import json
import math
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from unittest.mock import MagicMock, patch

# Make the package importable from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from training_log import config, tss_store
from training_log.process import (
    build_days,
    compute_pace,
    compute_period_summary,
    compute_speed_kmh,
    compute_vam,
    compute_zone_distribution,
    estimate_hr_tss,
    extract_epoc,
    extract_tss,
    group_days_by_month,
    group_days_by_week,
    merge_recovery,
    normalise_sport,
    process_wellness_sleep,
    process_workout,
    summary_tss_by_date,
    zone_seconds_to_pct,
)
from training_log.render import (
    _format_hm,
    _format_zone_line,
    _render_day,
    _render_recovery_line,
    _render_workout,
    _sport_breakdown_count,
    write_single_report,
    write_weekly_reports,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ──────────────────────────────────────────────────────────────────────────────

def default_cfg():
    return {
        "VT1_BPM": 145,
        "VT2_BPM": 171,
        "MAX_HR": 191,
        "THRESHOLD_HR": 171,
    }


def make_workout(**overrides):
    base = {
        "startTime": 1748000000000,  # epoch ms
        "activityId": 22,
        "totalDistance": 10000,
        "totalTime": 3600,
        "totalAscent": 100,
        "key": "abc123",
        "epoc": 45.5,
    }
    base.update(overrides)
    return base


def make_sleep_record(sleep_id, duration, quality=0.75, deep=1800, light=3600, rem=900,
                      hr_avg=0.93, is_nap=False):
    return {
        "timestamp": "2026-06-10T01:42:00.000+02:00",
        "entryData": {
            "sleepId": sleep_id,
            "duration": duration,
            "quality": quality,
            "deepSleepDuration": deep,
            "lightSleepDuration": light,
            "remSleepDuration": rem,
            "hrAvg": hr_avg,
            "avgHrv": 38.5,
            "isNap": is_nap,
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# config.py
# ──────────────────────────────────────────────────────────────────────────────

class TestConfig(unittest.TestCase):

    def test_zone_boundaries_count(self):
        zones = config.compute_zone_boundaries(default_cfg())
        self.assertEqual(len(zones), 5)
        names = [z[0] for z in zones]
        self.assertEqual(names, ["Z0", "Z1", "Z2", "Z3", "Z4"])

    def test_z0_upper_based_on_vt1(self):
        # Z0 upper = floor(145 * 0.80) - 1 = 116 - 1 = 115
        zones = config.compute_zone_boundaries(default_cfg())
        self.assertIsNone(zones[0][1])   # lower is None
        self.assertEqual(zones[0][2], 115)

    def test_z4_unbounded_upper(self):
        zones = config.compute_zone_boundaries(default_cfg())
        self.assertEqual(zones[4][1], 171)  # lower = VT2
        self.assertIsNone(zones[4][2])       # upper is None

    def test_zones_are_gapless(self):
        zones = config.compute_zone_boundaries(default_cfg())
        for i in range(len(zones) - 1):
            upper = zones[i][2]
            lower = zones[i + 1][1]
            self.assertEqual(upper + 1, lower, f"Gap between {zones[i][0]} and {zones[i+1][0]}")

    def test_load_config_defaults(self):
        with patch.dict(os.environ, {}, clear=False):
            cfg = config.load_config()
        self.assertEqual(cfg["VT1_BPM"], 145)
        self.assertEqual(cfg["VT2_BPM"], 171)
        self.assertEqual(cfg["THRESHOLD_HR"], 171)  # defaults to VT2
        self.assertEqual(cfg["SUUNTOOL_PATH"], "suuntool")


# ──────────────────────────────────────────────────────────────────────────────
# tss_store.py
# ──────────────────────────────────────────────────────────────────────────────

class TestTssStore(unittest.TestCase):

    def test_load_missing_file_returns_empty(self):
        result = tss_store.load_history("/nonexistent/path.json")
        self.assertEqual(result, {})

    def test_save_and_load_roundtrip(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            data = {"2026-06-01": 80.0, "2026-06-02": 45.5}
            tss_store.save_history(path, data)
            loaded = tss_store.load_history(path)
            self.assertEqual(loaded, data)
        finally:
            os.unlink(path)

    def test_save_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "subdir", "history.json")
            tss_store.save_history(path, {"2026-06-01": 50.0})
            self.assertTrue(os.path.exists(path))

    def test_update_history_merges(self):
        existing = {"2026-06-01": 80.0, "2026-06-02": 45.0}
        new = {"2026-06-02": 50.0, "2026-06-03": 30.0}
        result = tss_store.update_history(existing, new)
        self.assertEqual(result["2026-06-01"], 80.0)
        self.assertEqual(result["2026-06-02"], 50.0)  # overwritten
        self.assertEqual(result["2026-06-03"], 30.0)

    def test_compute_load_series_empty(self):
        self.assertEqual(tss_store.compute_load_series({}), {})

    def test_compute_load_series_single_day(self):
        history = {"2026-06-01": 100.0}
        series = tss_store.compute_load_series(history)
        self.assertIn("2026-06-01", series)
        entry = series["2026-06-01"]
        self.assertIn("ctl", entry)
        self.assertIn("atl", entry)
        self.assertIn("form", entry)
        # With only one day, CTL and ATL should be < 100 (decay factor applies)
        self.assertLess(entry["ctl"], 100)
        self.assertLess(entry["atl"], 100)

    def test_compute_load_series_ctl_formula(self):
        # Manually verify: one day with TSS=100, CTL starts at 0
        # CTL = 0 * e^(-1/42) + 100 * (1 - e^(-1/42))
        decay = math.exp(-1 / 42)
        expected_ctl = round(100 * (1 - decay), 1)
        history = {"2026-06-01": 100.0}
        series = tss_store.compute_load_series(history)
        self.assertEqual(series["2026-06-01"]["ctl"], expected_ctl)

    def test_compute_load_series_atl_faster_decay(self):
        # ATL decays faster than CTL (7-day vs 42-day constant)
        # So after a single rest day (TSS=0), ATL drops more than CTL
        history = {"2026-06-01": 100.0, "2026-06-02": 0.0}
        series = tss_store.compute_load_series(history)
        ctl_drop = series["2026-06-01"]["ctl"] - series["2026-06-02"]["ctl"]
        atl_drop = series["2026-06-01"]["atl"] - series["2026-06-02"]["atl"]
        self.assertGreater(atl_drop, ctl_drop)

    def test_compute_load_series_covers_gaps(self):
        # A gap between two dates still gets computed (TSS=0 for missing days)
        history = {"2026-06-01": 80.0, "2026-06-10": 80.0}
        series = tss_store.compute_load_series(history)
        self.assertIn("2026-06-05", series)  # gap day is present

    def test_form_is_ctl_minus_atl(self):
        history = {"2026-06-01": 100.0}
        series = tss_store.compute_load_series(history)
        entry = series["2026-06-01"]
        # form is computed from pre-rounded ctl/atl, so test the sign and approximate magnitude
        ctl_decay = math.exp(-1 / 42)
        atl_decay = math.exp(-1 / 7)
        expected_ctl = 100 * (1 - ctl_decay)
        expected_atl = 100 * (1 - atl_decay)
        self.assertAlmostEqual(entry["form"], round(expected_ctl - expected_atl, 1), places=1)


# ──────────────────────────────────────────────────────────────────────────────
# process.py — zone / metric helpers
# ──────────────────────────────────────────────────────────────────────────────

class TestZoneDistribution(unittest.TestCase):

    def _cfg(self):
        return default_cfg()

    def test_all_z2(self):
        # Z2 = floor(VT1*0.90)+1 .. VT1-1 = 131..144 (with VT1=145)
        # 135 bpm is solidly in Z2
        hr_data = [135, 135, 135, 135]
        time_data = [0, 60, 120, 180]
        secs = compute_zone_distribution(hr_data, time_data, self._cfg())
        self.assertGreater(secs["Z2"], 0)
        self.assertEqual(secs["Z0"], 0)
        self.assertEqual(secs["Z4"], 0)

    def test_z4_at_vt2(self):
        hr_data = [171, 180, 185]
        time_data = [0, 60, 120]
        secs = compute_zone_distribution(hr_data, time_data, self._cfg())
        self.assertGreater(secs["Z4"], 0)
        self.assertEqual(secs["Z0"] + secs["Z1"] + secs["Z2"] + secs["Z3"], 0)

    def test_zone_pct_sums_to_100(self):
        hr_data = [100, 130, 145, 171, 185]
        time_data = [0, 60, 120, 180, 240]
        secs = compute_zone_distribution(hr_data, time_data, self._cfg())
        pcts = zone_seconds_to_pct(secs)
        self.assertAlmostEqual(sum(pcts.values()), 100.0, places=0)

    def test_empty_data_gives_zeros(self):
        secs = compute_zone_distribution([], [], self._cfg())
        self.assertEqual(sum(secs.values()), 0)
        pcts = zone_seconds_to_pct(secs)
        self.assertEqual(sum(pcts.values()), 0)


class TestPaceSpeedVam(unittest.TestCase):

    def test_pace_10k_in_50min(self):
        pace = compute_pace(10000, 3000)  # 10km, 50min
        self.assertEqual(pace, (5, 0))

    def test_pace_none_for_zero_distance(self):
        self.assertIsNone(compute_pace(0, 3600))
        self.assertIsNone(compute_pace(None, 3600))

    def test_speed_kmh(self):
        # 36 km in 1 hour = 36 km/h
        self.assertEqual(compute_speed_kmh(36000, 3600), 36.0)

    def test_speed_none_for_zeros(self):
        self.assertIsNone(compute_speed_kmh(0, 3600))
        self.assertIsNone(compute_speed_kmh(36000, 0))

    def test_vam_1000m_in_1hour(self):
        self.assertEqual(compute_vam(1000, 3600), 1000)

    def test_vam_none_below_50m(self):
        self.assertIsNone(compute_vam(40, 3600))

    def test_vam_none_no_time(self):
        self.assertIsNone(compute_vam(500, 0))


# ──────────────────────────────────────────────────────────────────────────────
# process.py — sport normalisation
# ──────────────────────────────────────────────────────────────────────────────

class TestNormaliseSport(unittest.TestCase):

    def test_known_int_activity_id(self):
        self.assertEqual(normalise_sport(1), "Run")
        self.assertEqual(normalise_sport(11), "Hike")
        self.assertEqual(normalise_sport(22), "TrailRun")
        self.assertEqual(normalise_sport(29), "Climbing")
        self.assertEqual(normalise_sport(51), "YogaFlexibility")
        self.assertEqual(normalise_sport(66), "DiscGolf")
        self.assertEqual(normalise_sport(73), "CircuitTraining")

    def test_unknown_int_passthrough(self):
        self.assertEqual(normalise_sport(999), "Activity999")

    def test_string_map(self):
        self.assertEqual(normalise_sport("running"), "Run")
        self.assertEqual(normalise_sport("trail_running"), "TrailRun")
        self.assertEqual(normalise_sport("cycling"), "Ride")

    def test_string_case_insensitive(self):
        self.assertEqual(normalise_sport("Running"), "Run")

    def test_none_returns_unknown(self):
        self.assertEqual(normalise_sport(None), "Unknown")


# ──────────────────────────────────────────────────────────────────────────────
# process.py — TSS / EPOC extraction
# ──────────────────────────────────────────────────────────────────────────────

class TestExtractTss(unittest.TestCase):

    def _cfg(self):
        return default_cfg()

    def test_fit_tss_preferred(self):
        workout = {"tss": 999}  # JSON has a value too
        result = extract_tss(workout, None, None, 3600, 150, self._cfg(), fit_tss=85)
        self.assertEqual(result, 85)

    def test_json_tss_used_when_no_fit_tss(self):
        workout = {"tss": 77}
        result = extract_tss(workout, None, None, 3600, 150, self._cfg(), fit_tss=None)
        self.assertEqual(result, 77)

    def test_training_stress_score_field(self):
        workout = {"training_stress_score": 60}
        result = extract_tss(workout, None, None, 3600, 150, self._cfg())
        self.assertEqual(result, 60)

    def test_falls_back_to_hr_tss(self):
        # No native TSS, no FIT TSS — should estimate from HR
        workout = {}
        result = extract_tss(workout, None, None, 3600, 150, self._cfg())
        self.assertIsNotNone(result)
        self.assertIsInstance(result, int)

    def test_hr_tss_formula(self):
        # hrTSS = hours * (avg/threshold)^2 * 100
        # 1 hour @ 171 bpm / 171 threshold → IF=1.0 → TSS=100
        result = estimate_hr_tss(3600, 171, self._cfg())
        self.assertEqual(result, 100)

    def test_hr_tss_none_without_hr(self):
        self.assertIsNone(estimate_hr_tss(3600, None, self._cfg()))

    def test_hr_tss_none_without_time(self):
        self.assertIsNone(estimate_hr_tss(0, 150, self._cfg()))

    def test_extract_epoc(self):
        self.assertEqual(extract_epoc({"epoc": 45.7}), 46)
        self.assertEqual(extract_epoc({"peakEpoc": 30}), 30)
        self.assertIsNone(extract_epoc({}))


# ──────────────────────────────────────────────────────────────────────────────
# process.py — process_workout integration
# ──────────────────────────────────────────────────────────────────────────────

class TestProcessWorkout(unittest.TestCase):

    def _cfg(self):
        return default_cfg()

    def test_basic_run(self):
        w = make_workout(activityId=1)  # activityId 1 = Run
        result = process_workout(w, None, None, "", self._cfg())
        self.assertEqual(result["sport_type"], "Run")
        self.assertEqual(result["distance_km"], 10.0)
        self.assertEqual(result["moving_time_s"], 3600)
        self.assertEqual(result["moving_time_fmt"], "1:00")
        self.assertEqual(result["elevation_gain"], 100)
        self.assertIsNotNone(result["pace"])
        self.assertIsNone(result["speed"])

    def test_hr_derived_from_fit_stream(self):
        w = make_workout()
        hr_data = [140, 145, 150, 155, 160]
        time_data = [0, 60, 120, 180, 240]
        result = process_workout(w, hr_data, time_data, "", self._cfg())
        self.assertEqual(result["avg_hr"], round(sum(hr_data) / len(hr_data)))
        self.assertEqual(result["max_hr"], 160)
        self.assertTrue(result["has_heartrate"])

    def test_fit_tss_passed_through(self):
        w = make_workout()
        result = process_workout(w, None, None, "", self._cfg(), fit_tss=92)
        self.assertEqual(result["tss"], 92)

    def test_notes_stored(self):
        w = make_workout()
        result = process_workout(w, None, None, "Felt good today", self._cfg())
        self.assertEqual(result["notes"], "Felt good today")

    def test_gym_no_pace_no_speed(self):
        w = make_workout(activityId=73, totalDistance=0)
        result = process_workout(w, None, None, "", self._cfg())
        self.assertEqual(result["sport_type"], "CircuitTraining")
        self.assertIsNone(result["pace"])
        self.assertIsNone(result["speed"])

    def test_descent_extracted(self):
        w = make_workout(activityId=22, totalDescent=250)
        result = process_workout(w, None, None, "", self._cfg())
        self.assertEqual(result["elevation_descent"], 250)

    def test_date_extracted_from_epoch_ms(self):
        # startTime 1748000000000 ms → date check (just ensure it's a valid date string)
        w = make_workout()
        result = process_workout(w, None, None, "", self._cfg())
        self.assertRegex(result["date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_zone_pct_populated_with_hr_data(self):
        w = make_workout()
        hr_data = [130] * 100
        time_data = list(range(0, 100 * 60, 60))
        result = process_workout(w, hr_data, time_data, "", self._cfg())
        self.assertIsNotNone(result["zone_pct"])
        self.assertAlmostEqual(sum(result["zone_pct"].values()), 100.0, places=0)

    def test_zone_pct_none_without_hr(self):
        w = make_workout()
        result = process_workout(w, None, None, "", self._cfg())
        self.assertIsNone(result["zone_pct"])

    def test_epoc_extracted(self):
        w = make_workout(epoc=45.5)
        result = process_workout(w, None, None, "", self._cfg())
        self.assertEqual(result["epoc"], 46)

    def test_ride_uses_speed_not_pace(self):
        w = make_workout(activityId=None, activityType="cycling", totalDistance=36000)
        result = process_workout(w, None, None, "", self._cfg())
        self.assertIsNone(result["pace"])
        self.assertIsNotNone(result["speed"])


# ──────────────────────────────────────────────────────────────────────────────
# process.py — wellness processing
# ──────────────────────────────────────────────────────────────────────────────

class TestProcessWellnessSleep(unittest.TestCase):

    def test_basic_sleep(self):
        records = [make_sleep_record("s1", duration=25200)]
        result = process_wellness_sleep(records)
        self.assertIsNotNone(result)
        self.assertEqual(result["sleep_duration_s"], 25200)

    def test_hr_avg_converted_from_bps(self):
        # hr_avg=0.93 beats/sec → 0.93*60 = 55.8 → 56 bpm
        records = [make_sleep_record("s1", duration=25200, hr_avg=0.93)]
        result = process_wellness_sleep(records)
        self.assertEqual(result["sleep_hr_avg"], round(0.93 * 60))

    def test_quality_converted_from_fraction(self):
        records = [make_sleep_record("s1", duration=25200, quality=0.80)]
        result = process_wellness_sleep(records)
        self.assertEqual(result["sleep_quality_pct"], 80)

    def test_nap_filtered_out(self):
        records = [make_sleep_record("s1", duration=25200, is_nap=True)]
        result = process_wellness_sleep(records)
        self.assertIsNone(result)

    def test_dedup_keeps_max_duration(self):
        records = [
            make_sleep_record("s1", duration=20000),
            make_sleep_record("s1", duration=25200),  # same ID, larger
        ]
        result = process_wellness_sleep(records)
        self.assertEqual(result["sleep_duration_s"], 25200)

    def test_stage_percentages_sum_to_100(self):
        records = [make_sleep_record("s1", duration=25200, deep=1800, light=3600, rem=900)]
        result = process_wellness_sleep(records)
        total = result["deep_pct"] + result["light_pct"] + result["rem_pct"]
        self.assertEqual(total, 100)

    def test_hrv_passed_through(self):
        records = [make_sleep_record("s1", duration=25200)]
        result = process_wellness_sleep(records)
        self.assertEqual(result["hrv_rmssd"], 38.5)

    def test_empty_records_returns_none(self):
        self.assertIsNone(process_wellness_sleep([]))

    def test_recovery_pct_initially_none(self):
        records = [make_sleep_record("s1", duration=25200)]
        result = process_wellness_sleep(records)
        self.assertIsNone(result["recovery_pct"])

    def test_no_sleep_id_skipped(self):
        records = [{"timestamp": "2026-06-10T01:42:00.000+02:00",
                    "entryData": {"duration": 25200, "quality": 0.8}}]
        result = process_wellness_sleep(records)
        self.assertIsNone(result)  # no sleepId → skipped


class TestMergeRecovery(unittest.TestCase):

    def test_merge_balance_0_to_1(self):
        wellness = {"recovery_pct": None}
        merge_recovery(wellness, {"balance": 0.73})
        self.assertEqual(wellness["recovery_pct"], 73)

    def test_merge_nested_entry_data(self):
        wellness = {"recovery_pct": None}
        merge_recovery(wellness, {"entryData": {"balance": 0.85}})
        self.assertEqual(wellness["recovery_pct"], 85)

    def test_merge_no_recovery_record(self):
        wellness = {"recovery_pct": None}
        merge_recovery(wellness, None)
        self.assertIsNone(wellness["recovery_pct"])

    def test_merge_already_percentage(self):
        # If value > 1.0, treat as already a percentage
        wellness = {"recovery_pct": None}
        merge_recovery(wellness, {"balance": 73.0})
        self.assertEqual(wellness["recovery_pct"], 73)


# ──────────────────────────────────────────────────────────────────────────────
# process.py — day assembly / grouping / summary
# ──────────────────────────────────────────────────────────────────────────────

class TestBuildDays(unittest.TestCase):

    def _make_processed_workout(self, date_str, tss=80):
        return {
            "id": "w1",
            "name": "Run",
            "sport_type": "Run",
            "date": date_str,
            "start_dt": datetime.fromisoformat(f"{date_str}T08:00:00"),
            "distance_km": 10.0,
            "moving_time_s": 3600,
            "moving_time_fmt": "1:00",
            "elevation_gain": 100,
            "avg_hr": 145,
            "max_hr": 170,
            "avg_cadence": None,
            "epoc": 45,
            "has_heartrate": True,
            "pace": "6:00",
            "speed": None,
            "vam": None,
            "zone_seconds": None,
            "zone_pct": None,
            "tss": tss,
            "notes": "",
        }

    def test_all_days_in_range(self):
        start = datetime(2026, 6, 1)
        end = datetime(2026, 6, 7, 23, 59, 59)
        days = build_days([], {}, start, end, {})
        self.assertEqual(len(days), 7)

    def test_workouts_assigned_to_correct_day(self):
        w = self._make_processed_workout("2026-06-03")
        start = datetime(2026, 6, 1)
        end = datetime(2026, 6, 7, 23, 59, 59)
        days = build_days([w], {}, start, end, {})
        day_june3 = next(d for d in days if d["date"] == "2026-06-03")
        self.assertEqual(len(day_june3["workouts"]), 1)
        # Other days have no workouts
        other_days = [d for d in days if d["date"] != "2026-06-03"]
        for d in other_days:
            self.assertEqual(len(d["workouts"]), 0)

    def test_rest_days_have_empty_workouts(self):
        start = datetime(2026, 6, 1)
        end = datetime(2026, 6, 3, 23, 59, 59)
        days = build_days([], {}, start, end, {})
        for d in days:
            self.assertEqual(d["workouts"], [])

    def test_tss_summed_per_day(self):
        w1 = self._make_processed_workout("2026-06-01", tss=80)
        w2 = self._make_processed_workout("2026-06-01", tss=40)
        start = datetime(2026, 6, 1)
        end = datetime(2026, 6, 1, 23, 59, 59)
        days = build_days([w1, w2], {}, start, end, {})
        self.assertEqual(days[0]["tss"], 120)

    def test_load_series_attached(self):
        start = datetime(2026, 6, 1)
        end = datetime(2026, 6, 1, 23, 59, 59)
        load = {"2026-06-01": {"ctl": 55.0, "atl": 60.0, "form": -5.0}}
        days = build_days([], {}, start, end, load)
        self.assertEqual(days[0]["load"]["ctl"], 55.0)

    def test_wellness_attached(self):
        wellness = {"2026-06-01": {"recovery_pct": 80, "sleep_duration_s": 25200}}
        start = datetime(2026, 6, 1)
        end = datetime(2026, 6, 1, 23, 59, 59)
        days = build_days([], wellness, start, end, {})
        self.assertEqual(days[0]["wellness"]["recovery_pct"], 80)


class TestGrouping(unittest.TestCase):

    def _make_day(self, date_str):
        d = datetime.fromisoformat(date_str)
        return {
            "date": date_str,
            "weekday": d.strftime("%A"),
            "iso": d.isocalendar(),
            "wellness": None,
            "workouts": [],
            "tss": 0,
            "load": None,
        }

    def test_group_by_week(self):
        days = [self._make_day("2026-06-01"), self._make_day("2026-06-08")]
        weeks = group_days_by_week(days)
        self.assertEqual(len(weeks), 2)

    def test_group_by_month(self):
        days = [self._make_day("2026-06-01"), self._make_day("2026-07-01")]
        months = group_days_by_month(days)
        self.assertEqual(len(months), 2)
        self.assertIn((2026, 6), months)
        self.assertIn((2026, 7), months)

    def test_same_week_grouped_together(self):
        # 2026-06-01 is a Monday (week 23)
        days = [self._make_day("2026-06-01"), self._make_day("2026-06-05")]
        weeks = group_days_by_week(days)
        self.assertEqual(len(weeks), 1)
        self.assertEqual(len(list(weeks.values())[0]), 2)


class TestComputePeriodSummary(unittest.TestCase):

    def _day_with_workout(self, tss=80, dist=10.0, time_s=3600, elev=100, sport="Run"):
        return {
            "date": "2026-06-01",
            "weekday": "Monday",
            "iso": (2026, 23, 1),
            "wellness": None,
            "workouts": [{
                "sport_type": sport,
                "distance_km": dist,
                "moving_time_s": time_s,
                "elevation_gain": elev,
                "tss": tss,
                "zone_seconds": None,
                "zone_pct": None,
            }],
            "tss": tss,
            "load": {"ctl": 55.0, "atl": 60.0, "form": -5.0},
        }

    def test_total_time(self):
        days = [self._day_with_workout(time_s=3600), self._day_with_workout(time_s=1800)]
        summary = compute_period_summary(days)
        self.assertEqual(summary["total_time_s"], 5400)

    def test_total_tss(self):
        days = [self._day_with_workout(tss=80), self._day_with_workout(tss=40)]
        summary = compute_period_summary(days)
        self.assertEqual(summary["total_tss"], 120)

    def test_sport_counts(self):
        days = [
            self._day_with_workout(sport="Run"),
            self._day_with_workout(sport="Run"),
            self._day_with_workout(sport="WeightTraining"),
        ]
        summary = compute_period_summary(days)
        self.assertEqual(summary["sport_counts"]["Run"], 2)
        self.assertEqual(summary["sport_counts"]["WeightTraining"], 1)

    def test_load_taken_from_last_day(self):
        day1 = self._day_with_workout()
        day2 = self._day_with_workout()
        day2["load"] = {"ctl": 60.0, "atl": 65.0, "form": -5.0}
        summary = compute_period_summary([day1, day2])
        self.assertEqual(summary["load"]["ctl"], 60.0)


# ──────────────────────────────────────────────────────────────────────────────
# process.py — summary_tss_by_date
# ──────────────────────────────────────────────────────────────────────────────

class TestSummaryTssByDate(unittest.TestCase):

    def test_sums_tss_by_date(self):
        cfg = default_cfg()
        w1 = {"startTime": 1748000000000, "totalTime": 3600, "tss": 80}
        result = summary_tss_by_date([w1], cfg)
        dates = list(result.keys())
        self.assertEqual(len(dates), 1)
        self.assertEqual(result[dates[0]], 80.0)

    def test_groups_multiple_workouts_same_day(self):
        cfg = default_cfg()
        ts = 1748000000000
        w1 = {"startTime": ts, "totalTime": 3600, "tss": 80}
        w2 = {"startTime": ts + 3600000, "totalTime": 1800, "tss": 40}
        result = summary_tss_by_date([w1, w2], cfg)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(list(result.values())[0], 120.0)


# ──────────────────────────────────────────────────────────────────────────────
# fit.py — parse_fit with mocked fitparse
# ──────────────────────────────────────────────────────────────────────────────

class TestParseFit(unittest.TestCase):
    """
    FitFile is imported lazily inside parse_fit (`from fitparse import FitFile`).
    To mock it we inject a fake 'fitparse' module into sys.modules before calling
    parse_fit, then restore the original (or absence) afterwards.
    """

    def _inject_fitparse(self, mock_fitfile_instance):
        """Return a context manager that installs a fake fitparse module."""
        mock_fitparse = MagicMock()
        mock_fitparse.FitFile.return_value = mock_fitfile_instance
        return patch.dict(sys.modules, {"fitparse": mock_fitparse})

    def _make_mock_field(self, name, value):
        f = MagicMock()
        f.name = name
        f.value = value
        return f

    def _make_mock_message(self, msg_name, fields_dict):
        m = MagicMock()
        m.name = msg_name
        fields = [self._make_mock_field(k, v) for k, v in fields_dict.items()]
        m.__iter__ = MagicMock(return_value=iter(fields))
        m.fields = fields
        return m

    def _make_hr_records(self, hr_values, t0=datetime(2026, 6, 1, 8, 0, 0)):
        from datetime import timedelta
        messages = []
        for i, hr in enumerate(hr_values):
            ts = t0 + timedelta(seconds=i * 60)
            m = self._make_mock_message("record", {"heart_rate": hr, "timestamp": ts})
            messages.append(m)
        return messages

    def test_returns_4_tuple(self):
        from training_log.fit import parse_fit
        mock_ff = MagicMock()
        mock_ff.get_messages.return_value = []
        with self._inject_fitparse(mock_ff):
            hr, time_data, desc, tss = parse_fit("/fake/path.fit")
        self.assertIsNone(hr)
        self.assertIsNone(time_data)
        self.assertEqual(desc, "")
        self.assertIsNone(tss)

    def test_hr_extracted(self):
        from training_log.fit import parse_fit
        messages = self._make_hr_records([140, 145, 150])
        mock_ff = MagicMock()
        mock_ff.get_messages.return_value = messages
        with self._inject_fitparse(mock_ff):
            hr, time_data, desc, tss = parse_fit("/fake/path.fit")
        self.assertEqual(hr, [140, 145, 150])
        self.assertEqual(time_data, [0, 60, 120])

    def test_tss_from_session_message(self):
        from training_log.fit import parse_fit
        session_msg = self._make_mock_message("session", {"training_stress_score": 85.3})
        mock_ff = MagicMock()
        mock_ff.get_messages.return_value = [session_msg]
        with self._inject_fitparse(mock_ff):
            hr, time_data, desc, tss = parse_fit("/fake/path.fit")
        self.assertEqual(tss, 85)

    def test_description_extracted_from_dev_field(self):
        from training_log.fit import parse_fit
        msg = MagicMock()
        msg.name = "workout"
        msg.__iter__ = MagicMock(return_value=iter([]))
        msg.fields = [self._make_mock_field("description", "Legs prehab")]
        mock_ff = MagicMock()
        mock_ff.get_messages.return_value = [msg]
        with self._inject_fitparse(mock_ff):
            hr, time_data, desc, tss = parse_fit("/fake/path.fit")
        self.assertEqual(desc, "Legs prehab")

    def test_fitparse_import_error(self):
        from training_log.fit import parse_fit
        # Remove fitparse so the ImportError branch fires
        with patch.dict(sys.modules, {"fitparse": None}):
            hr, time_data, desc, tss = parse_fit("/fake/path.fit")
        self.assertIsNone(hr)
        self.assertIsNone(time_data)
        self.assertEqual(desc, "")
        self.assertIsNone(tss)

    def test_exception_during_parse_returns_none(self):
        from training_log.fit import parse_fit
        mock_fitparse = MagicMock()
        mock_fitparse.FitFile.side_effect = Exception("corrupt file")
        with patch.dict(sys.modules, {"fitparse": mock_fitparse}):
            hr, time_data, desc, tss = parse_fit("/fake/path.fit")
        self.assertIsNone(hr)
        self.assertIsNone(tss)


# ──────────────────────────────────────────────────────────────────────────────
# suunto.py — pure-Python helpers (no subprocess)
# ──────────────────────────────────────────────────────────────────────────────

class TestSuuntoHelpers(unittest.TestCase):

    def test_first_returns_first_present(self):
        from training_log.suunto import _first
        d = {"a": None, "b": 42, "c": 99}
        self.assertEqual(_first(d, "a", "b", "c"), 42)

    def test_first_default_on_miss(self):
        from training_log.suunto import _first
        self.assertIsNone(_first({}, "x", "y"))
        self.assertEqual(_first({}, "x", default="fallback"), "fallback")

    def test_first_non_dict(self):
        from training_log.suunto import _first
        self.assertIsNone(_first(None, "x"))
        self.assertIsNone(_first("string", "x"))

    def test_run_ndjson_parses_lines(self):
        from training_log.suunto import _run_ndjson
        cfg = {"SUUNTOOL_PATH": "suuntool"}
        ndjson_output = '{"a": 1}\n{"b": 2}\n\n{"c": 3}\n'
        with patch("training_log.suunto._run", return_value=ndjson_output):
            result = _run_ndjson(cfg, ["wellness", "sleep", "--since", "2026-06-01"])
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], {"a": 1})

    def test_run_ndjson_skips_bad_lines(self):
        from training_log.suunto import _run_ndjson
        cfg = {"SUUNTOOL_PATH": "suuntool"}
        ndjson_output = '{"a": 1}\nnot-json\n{"b": 2}\n'
        with patch("training_log.suunto._run", return_value=ndjson_output):
            result = _run_ndjson(cfg, [])
        self.assertEqual(len(result), 2)

    def test_run_ndjson_empty_on_none(self):
        from training_log.suunto import _run_ndjson
        cfg = {"SUUNTOOL_PATH": "suuntool"}
        with patch("training_log.suunto._run", return_value=None):
            result = _run_ndjson(cfg, [])
        self.assertEqual(result, [])

    def test_get_wellness_recovery_takes_max_per_day(self):
        from training_log.suunto import get_wellness_recovery
        cfg = {"SUUNTOOL_PATH": "suuntool"}
        records = [
            {"timestamp": "2026-06-01T06:00:00.000+02:00", "entryData": {"balance": 0.65}},
            {"timestamp": "2026-06-01T08:00:00.000+02:00", "entryData": {"balance": 0.80}},  # peak
            {"timestamp": "2026-06-01T10:00:00.000+02:00", "entryData": {"balance": 0.70}},
        ]
        with patch("training_log.suunto._run_ndjson", return_value=records):
            result = get_wellness_recovery(cfg, "2026-06-01")
        self.assertIn("2026-06-01", result)
        self.assertAlmostEqual(result["2026-06-01"]["balance"], 0.80)

    def test_list_workouts_filters_by_end_date(self):
        from training_log.suunto import list_workouts
        cfg = {"SUUNTOOL_PATH": "suuntool"}
        # epoch ms: 2026-06-01 and 2026-06-30
        t1 = int(datetime(2026, 6, 1, 10, 0).timestamp() * 1000)
        t2 = int(datetime(2026, 6, 30, 10, 0).timestamp() * 1000)
        items = [{"startTime": t1, "key": "w1"}, {"startTime": t2, "key": "w2"}]
        with patch("training_log.suunto._run", return_value={"items": items}):
            result = list_workouts(cfg, "2026-06-01", "2026-06-15")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["key"], "w1")

    def test_get_workout_notes_list_response(self):
        from training_log.suunto import get_workout_notes
        cfg = {"SUUNTOOL_PATH": "suuntool"}
        data = {"items": [{"text": "Good run"}, {"text": "Felt strong"}]}
        with patch("training_log.suunto._run", return_value=data):
            result = get_workout_notes(cfg, "abc123")
        self.assertIn("Good run", result)
        self.assertIn("Felt strong", result)

    def test_get_workout_notes_empty_items(self):
        from training_log.suunto import get_workout_notes
        cfg = {"SUUNTOOL_PATH": "suuntool"}
        with patch("training_log.suunto._run", return_value={"items": []}):
            result = get_workout_notes(cfg, "abc123")
        self.assertEqual(result, "")


# ──────────────────────────────────────────────────────────────────────────────
# render.py
# ──────────────────────────────────────────────────────────────────────────────

class TestRenderHelpers(unittest.TestCase):

    def test_format_hm(self):
        self.assertEqual(_format_hm(3600), "1h00")
        self.assertEqual(_format_hm(5400), "1h30")
        self.assertEqual(_format_hm(3660), "1h01")
        self.assertIsNone(_format_hm(0))
        self.assertIsNone(_format_hm(None))

    def test_format_zone_line_omits_zeros(self):
        pct = {"Z0": 0, "Z1": 0, "Z2": 65.0, "Z3": 25.0, "Z4": 10.0}
        result = _format_zone_line(pct)
        self.assertNotIn("Z0", result)
        self.assertNotIn("Z1", result)
        self.assertIn("Z2 65.0%", result)

    def test_sport_breakdown_count_plural(self):
        result = _sport_breakdown_count({"Run": 3, "WeightTraining": 1})
        self.assertIn("3 runs", result)
        self.assertIn("1 weight training", result)

    def test_render_recovery_line_basic(self):
        wellness = {
            "recovery_pct": 80,
            "sleep_duration_s": 25200,
            "sleep_quality_pct": 78,
            "deep_pct": 22,
            "rem_pct": 19,
            "hrv_rmssd": 38.5,
        }
        line = _render_recovery_line(wellness)
        self.assertIn("**Recovery:**", line)
        self.assertIn("80%", line)
        self.assertIn("7h00", line)
        self.assertIn("HRV: 38 ms", line)  # round(38.5) == 38 in Python 3 (banker's rounding)

    def test_render_recovery_line_none_on_no_data(self):
        self.assertIsNone(_render_recovery_line(None))
        self.assertIsNone(_render_recovery_line({}))

    def test_render_workout_contains_key_fields(self):
        workout = {
            "name": "Morning Run",
            "sport_type": "Run",
            "distance_km": 10.0,
            "moving_time_s": 3600,
            "moving_time_fmt": "1:00",
            "elevation_gain": 50,
            "avg_hr": 145,
            "max_hr": 168,
            "has_heartrate": True,
            "pace": "6:00",
            "speed": None,
            "vam": None,
            "zone_pct": {"Z0": 0, "Z1": 5.0, "Z2": 65.0, "Z3": 25.0, "Z4": 5.0},
            "tss": 85,
            "epoc": 42,
            "notes": "Felt great",
            "avg_cadence": None,
        }
        lines = _render_workout(workout)
        full = "\n".join(lines)
        self.assertIn("Morning Run", full)
        self.assertIn("10.0 km", full)
        self.assertIn("145 bpm", full)
        self.assertIn("TSS: 85", full)
        self.assertIn("EPOC: 42", full)
        self.assertIn("Felt great", full)
        self.assertIn("Z2 65.0%", full)

    def test_render_day_rest_day(self):
        day = {
            "date": "2026-06-01",
            "weekday": "Monday",
            "wellness": None,
            "workouts": [],
        }
        lines = _render_day(day)
        full = "\n".join(lines)
        self.assertIn("*Rest day*", full)

    def test_render_day_with_workout(self):
        workout = {
            "name": "Easy Run",
            "sport_type": "Run",
            "distance_km": 8.0,
            "moving_time_s": 2880,
            "moving_time_fmt": "0:48",
            "elevation_gain": 30,
            "avg_hr": 138,
            "max_hr": 155,
            "has_heartrate": True,
            "pace": "6:00",
            "speed": None,
            "vam": None,
            "zone_pct": None,
            "tss": 55,
            "epoc": None,
            "notes": "",
            "avg_cadence": None,
        }
        day = {
            "date": "2026-06-01",
            "weekday": "Monday",
            "wellness": {"recovery_pct": 75, "sleep_duration_s": 25200,
                         "sleep_quality_pct": 80, "deep_pct": 20, "rem_pct": 18, "hrv_rmssd": None},
            "workouts": [workout],
        }
        lines = _render_day(day)
        full = "\n".join(lines)
        self.assertNotIn("*Rest day*", full)
        self.assertIn("Easy Run", full)
        self.assertIn("**Recovery:**", full)


class TestWriteReports(unittest.TestCase):

    def _minimal_day(self, date_str, sport_type="Run"):
        d = datetime.fromisoformat(date_str)
        return {
            "date": date_str,
            "weekday": d.strftime("%A"),
            "iso": d.isocalendar(),
            "wellness": None,
            "workouts": [],
            "tss": 0,
            "load": None,
        }

    def test_write_weekly_report_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            days = [self._minimal_day("2026-06-01")]
            weeks_data = {(2026, 23): (days, {
                "total_time_s": 3600, "total_time_fmt": "1:00",
                "total_distance_km": 10.0, "total_elevation": 100,
                "num_activities": 1, "sport_counts": {"Run": 1},
                "sport_distance": {"Run": 10.0}, "zone_pct": None,
                "total_tss": 80, "load": None, "recovery": None,
            })}
            written = write_weekly_reports(weeks_data, tmpdir)
            self.assertEqual(len(written), 1)
            self.assertTrue(os.path.exists(written[0]))
            content = open(written[0]).read()
            self.assertIn("Training log", content)
            self.assertIn("Week summary", content)

    def test_write_single_report_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            days = [self._minimal_day("2026-06-01"), self._minimal_day("2026-06-07")]
            summary = {
                "total_time_s": 0, "total_time_fmt": "0:00",
                "total_distance_km": 0, "total_elevation": 0,
                "num_activities": 0, "sport_counts": {}, "sport_distance": {},
                "zone_pct": None, "total_tss": 0, "load": None, "recovery": None,
            }
            written = write_single_report(days, summary, tmpdir)
            self.assertEqual(len(written), 1)
            content = open(written[0]).read()
            self.assertIn("2026-06-01 to 2026-06-07", content)
            # Every day gets a section
            self.assertIn("Monday, 2026-06-01", content)
            self.assertIn("Sunday, 2026-06-07", content)


# ──────────────────────────────────────────────────────────────────────────────
# End-to-end: process + render with realistic data
# ──────────────────────────────────────────────────────────────────────────────

class TestEndToEnd(unittest.TestCase):
    """Verifies the full processing → rendering pipeline with realistic data shapes."""

    def setUp(self):
        self.cfg = default_cfg()
        self.cfg["TSS_HISTORY_FILE"] = "/tmp/_test_tss_history.json"
        self.cfg["OUTPUT_DIR"] = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        if os.path.exists(self.cfg["TSS_HISTORY_FILE"]):
            os.unlink(self.cfg["TSS_HISTORY_FILE"])
        shutil.rmtree(self.cfg["OUTPUT_DIR"], ignore_errors=True)

    def test_run_with_fit_tss(self):
        raw = make_workout(
            startTime=int(datetime(2026, 6, 10, 8, 0).timestamp() * 1000),
        )
        hr_data = [140] * 60
        time_data = list(range(0, 60 * 60, 60))
        w = process_workout(raw, hr_data, time_data, "Test notes", self.cfg, fit_tss=92)
        self.assertEqual(w["tss"], 92)
        self.assertEqual(w["notes"], "Test notes")
        self.assertEqual(w["date"], "2026-06-10")

    def test_full_week_report(self):
        raw = make_workout(
            startTime=int(datetime(2026, 6, 9, 8, 0).timestamp() * 1000),
        )
        w = process_workout(raw, None, None, "", self.cfg)

        sleep = [make_sleep_record("s1", duration=27000, quality=0.82)]
        wellness = process_wellness_sleep(sleep)
        merge_recovery(wellness, {"balance": 0.76})

        start = datetime(2026, 6, 9)
        end = datetime(2026, 6, 15, 23, 59, 59)
        history = tss_store.update_history({}, {"2026-06-09": w["tss"] or 0})
        load_series = tss_store.compute_load_series(history)

        days = build_days(
            [w],
            {"2026-06-09": wellness},
            start, end,
            load_series,
        )

        self.assertEqual(len(days), 7)
        june9 = next(d for d in days if d["date"] == "2026-06-09")
        self.assertEqual(len(june9["workouts"]), 1)
        self.assertEqual(june9["wellness"]["recovery_pct"], 76)

        weeks = group_days_by_week(days)
        weeks_data = {
            k: (dl, compute_period_summary(dl))
            for k, dl in weeks.items()
        }
        written = write_weekly_reports(weeks_data, self.cfg["OUTPUT_DIR"])
        self.assertGreater(len(written), 0)

        content = open(written[0]).read()
        self.assertIn("Training log", content)
        self.assertIn("Recovery:", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
