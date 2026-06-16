# Training Data Pipeline — Redesign Spec

## Context

This spec covers the migration from Strava API to Suunto as the primary data source, using the
`suuntool` CLI, along with a set of format and feature changes to the generated training logs.

The motivating change is Strava's June 2026 policy update requiring a paid subscription for
Standard Tier API developers. Since Suunto is the actual source of all training data (Strava was
only used as an API intermediary), switching to direct Suunto access eliminates the dependency
entirely.

---

## Data Source: Suunto via suuntool

[suuntool](https://github.com/tajchert/suuntool) is an unofficial CLI and MCP server that
accesses the same backend API as the Suunto mobile app. It is not affiliated with Suunto Oy, and
the underlying API is undocumented and may change without notice. For low-volume personal use the
practical risk is low.

### Workout data

Suuntool exposes per-workout data including:

- Metadata: date, sport type, name, duration, distance, elevation gain
- Heart rate: average, max, zone distribution
- Pace / speed
- TSS (Training Stress Score) — Suunto displays this in-app; expected to be in FIT file or
  workout metadata. **To be confirmed:** run `suuntool workouts get <id>` on a workout with a
  known TSS value and verify the field name.
- EPOC (Excess Post-Exercise Oxygen Consumption) — Suunto's proprietary aerobic load metric.
  Included as a secondary per-activity field (see rationale in Output Format section).
- Workout notes / description — Suunto supports adding a description when saving a session.
  Accessible via `suuntool workouts comments`. **To be confirmed:** verify whether the
  user-written session description appears here or as a separate metadata field on the workout
  object.

FIT files are downloaded via `suuntool workouts get <id> --format fit` and parsed with
`fitparse` for detailed stream data (HR, GPS, cadence, power).

### Wellness data

Fetched daily via the suuntool wellness endpoints:

| Endpoint | Data |
|---|---|
| `wellness sleep` | Sleep duration, quality %, average HR during sleep |
| `wellness sleepstages` | Time in light / deep / REM stages |
| `wellness recovery` | Recovery balance score (0–1), Suunto's HRV-derived readiness metric |

Nightly HRV (raw RMSSD) is not confirmed as a documented field in suuntool. If it appears in the
raw JSON of `wellness sleep`, include it. Otherwise the recovery balance score is sufficient —
it is already derived from overnight HRV.

### Authentication

suuntool manages authentication itself (`suuntool login`). The pipeline shells out to suuntool;
no OAuth implementation is needed in this codebase. The existing `auth.py` is removed.

---

## Architecture Changes

### Module rename

The `strava_log/` package is renamed to `training_log/`. The CLI entry point becomes
`training_log.py`. All internal imports updated accordingly.

### File changes

| File | Action |
|---|---|
| `strava_log/api.py` | Replaced — new `suunto.py` wraps suuntool CLI calls |
| `strava_log/auth.py` | Removed — suuntool handles auth |
| `strava_log/config.py` | Updated — Strava credentials replaced by suuntool path / config |
| `strava_log/process.py` | Extended — wellness processing, TSS/CTL/ATL added |
| `strava_log/render.py` | Revised — new day-centric format |
| `strava_log/strava_log.py` | Renamed to `training_log/training_log.py`, updated |

### TSS history store

CTL and ATL require a rolling history of daily TSS values (42 days for CTL, 7 for ATL). A small
local JSON file (`~/.training_log_tss.json` or path set via config) stores `{date: tss}` entries
and is updated on each run. On first run, a sufficiently long history (90 days) is fetched to
seed CTL properly.

---

## Output Format

The format shifts from activity-centric to **day-centric**: every day in the requested range gets
an entry, whether or not a workout occurred. Wellness data anchors each day; workouts appear
beneath it.

The training diary tone of the current format is preserved.

### Weekly report structure

```
# Training log — week 2026-W12 (2026-03-16 – 2026-03-22)

## Week summary

- Total time: 6:45
- Total distance: 58.3 km (32.1 km running, 26.2 km trail run)
- Total elevation: 1,230 m
- Activities: 4 (2 runs, 1 trail run, 1 weight training)
- HR zone distribution (% of tracked time): Z1 12% · Z2 45% · Z3 30% · Z4 13%
- Weekly TSS: 423
- CTL: 67 | ATL: 71 | Form: -4

## Recovery overview

- Avg recovery: 72% | Best: 85% (Thu) | Worst: 54% (Mon)
- Avg sleep quality: 78% | Avg sleep: 7h06

---

## Monday, 2026-03-16

**Recovery:** 54% · Sleep: 6h42 · Quality: 61% · Deep: 18% · REM: 22%

### Easy long run (run)

- Distance: 18.2 km | Moving time: 1:32 | Elevation: +85 m
- Avg pace: 5:04 /km
- HR: avg 142 bpm / max 158 bpm | Zone split: Z1 25% · Z2 60% · Z3 15%
- TSS: 87 | EPOC: 43 ml/kg
- Notes: Progressive build to marathon pace last 5k. Left calf felt tight after km 12.

---

## Tuesday, 2026-03-17

**Recovery:** 71% · Sleep: 7h15 · Quality: 74% · Deep: 22% · REM: 19%

*Rest day*

---
```

### Field-level notes

**Recovery line:** Shown at the top of every day entry. If wellness data is unavailable for a
given day (e.g. watch not worn), the line is omitted rather than shown as empty.

**TSS:** Primary load metric per activity and summed for the week. Replaces suffer score from the
Strava-based format.

**EPOC:** Suunto's aerobic load metric, shown alongside TSS per activity. Unlike TSS (which
accounts for training stress relative to FTP/threshold), EPOC captures aerobic stimulus
specifically — useful context for understanding the aerobic vs. anaerobic split of a session.
Shown as a secondary field; not rolled up into weekly totals.

**CTL / ATL / Form:**
- CTL (Chronic Training Load): exponentially weighted 42-day average of daily TSS.
  Represents fitness. Formula: `CTL = prev_CTL × e^(−1/42) + tss × (1 − e^(−1/42))`
- ATL (Acute Training Load): exponentially weighted 7-day average of daily TSS.
  Represents fatigue. Formula: `ATL = prev_ATL × e^(−1/7) + tss × (1 − e^(−1/7))`
- Form (TSB): `CTL − ATL`. Positive = fresh/undertrained, negative = fatigued/building.
  Shown at end of week as the Sunday-evening value (after all workouts for the week are
  counted).

**Notes field:** Replaces the separate `Description` / `Private note` fields from the Strava
format. Suunto has a single notes/description field per workout; both are shown as one `Notes:`
line. If empty, the line is omitted.

**Sport labels:** The existing mapping in `render.py` is extended to cover Suunto's sport type
identifiers (which differ from Strava's).

### Report modes

The three existing report modes are preserved:

| Mode | Output |
|---|---|
| Weekly | One file per ISO week: `training_log_2026-W12.md` |
| Monthly | One file per month: `training_log_2026-03.md` |
| Single | One combined file across all fetched data |

---

## Open Questions

1. **TSS field name in suuntool:** Confirm by running `suuntool workouts get <id>` on a workout
   with a known TSS value. If TSS is not in workout metadata, derive it from normalized power /
   threshold HR using standard formulas from the FIT file data.

2. **Workout notes field:** Confirm whether user-written session descriptions appear in
   `workouts comments` or as a top-level field on the workout object.

3. **Nightly HRV (RMSSD):** Inspect raw JSON from `suuntool wellness sleep` to see if a raw HRV
   value is present alongside the aggregated recovery score.

4. **Historical notes in Strava:** If workout notes have historically been written in Strava
   rather than in the Suunto app, they will not be accessible via suuntool. A one-time migration
   script may be needed to export and reattach them.
