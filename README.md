# training-data-pipeline

Personal training data pipeline — pulls workouts and wellness data from Suunto (via the
[suuntool](https://github.com/tajchert/suuntool) CLI), computes HR zone distributions and
training load (TSS / CTL / ATL / Form), and generates day-centric Markdown training-diary
reports suitable for LLM analysis and coaching.

> **Why Suunto?** Suunto is the actual source of all training data (Strava was previously
> used only as an API intermediary). Switching to direct Suunto access via suuntool removes
> the Strava dependency entirely — relevant given Strava's June 2026 move to require a paid
> subscription for Standard Tier API access.

## What it does

- Fetches your Suunto workouts for a given date range via `suuntool`
- Computes per-activity HR zone splits from FIT-file streams using your personal VT1/VT2 thresholds
- Calculates pace (runs), speed (rides), and VAM (climbing)
- Tracks training load: per-activity **TSS** and **EPOC**, plus rolling **CTL / ATL / Form**
- Anchors every day with wellness data (recovery, sleep duration/quality/stages)
- Outputs day-centric Markdown reports (weekly, monthly, or single combined)

### Example output

```
# Training log — week 2026-W12 (2026-03-16 – 2026-03-22)

## Week summary

- Total time: 6:45
- Total distance: 58.3 km (32.1 km run, 26.2 km trail run)
- Total elevation: 1,230 m
- Activities: 4 (2 runs, 1 trail run, 1 weight training)
- HR zone distribution (% of tracked time): Z1 12% · Z2 45% · Z3 30% · Z4 13%
- Total TSS: 423
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

## Setup

### 1. Install and authenticate suuntool

This pipeline shells out to [suuntool](https://github.com/tajchert/suuntool), which manages
its own authentication. Install it, then log in once:

```bash
suuntool login
```

> **Note:** suuntool is an unofficial CLI that talks to the same backend as the Suunto
> mobile app. It is not affiliated with Suunto Oy and the underlying API is undocumented.
> For low-volume personal use the practical risk is low.

### 2. Clone and install

```bash
git clone <repo-url>
cd training-data-pipeline
pip install -r training_log/requirements.txt
```

### 3. Configure (optional)

Authentication needs no config here — suuntool owns it. The `.env` file is only for
overriding defaults (HR thresholds, output directory, suuntool path):

```bash
cp .env.example .env
```

#### Optional settings

| Variable | Default | Description |
|---|---|---|
| `SUUNTOOL_PATH` | `suuntool` | Path to the suuntool executable |
| `VT1_BPM` | `145` | Ventilatory threshold 1 (aerobic) heart rate |
| `VT2_BPM` | `171` | Ventilatory threshold 2 (anaerobic) heart rate |
| `MAX_HR` | `191` | Maximum heart rate |
| `THRESHOLD_HR` | `VT2_BPM` | Threshold HR used for the hrTSS fallback estimate |
| `OUTPUT_DIR` | `./training_logs` | Directory for generated reports |
| `TSS_HISTORY_FILE` | `~/.training_log_tss.json` | Rolling daily-TSS history (seeds CTL/ATL) |

HR zones are derived from VT1 and VT2:

| Zone | Range |
|---|---|
| Z0 | Below 80% of VT1 (recovery) |
| Z1 | 80–90% of VT1 (easy aerobic) |
| Z2 | 90% of VT1 to VT1 (moderate aerobic) |
| Z3 | VT1 to VT2 (threshold) |
| Z4 | Above VT2 (high intensity) |

## Training load (TSS / CTL / ATL / Form)

- **TSS** (Training Stress Score) is taken from the Suunto workout when present, otherwise
  estimated from average HR vs. threshold HR (hrTSS).
- **EPOC** is Suunto's proprietary aerobic-load metric, shown per activity as context.
- **CTL** (fitness) is a 42-day exponentially-weighted average of daily TSS.
- **ATL** (fatigue) is a 7-day exponentially-weighted average of daily TSS.
- **Form** (TSB) = CTL − ATL. Positive = fresh, negative = building/fatigued.

Because CTL/ATL depend on weeks of prior load, daily TSS is persisted to
`TSS_HISTORY_FILE` and the first run seeds ~90 days of history automatically.

## Usage

```bash
# Default: last 4 complete weeks, weekly reports
python -m training_log.training_log

# Last 8 weeks
python -m training_log.training_log --weeks 8

# Specific date range
python -m training_log.training_log --from 2026-01-01 --to 2026-03-25

# Monthly reports instead of weekly
python -m training_log.training_log --format monthly

# Single combined report
python -m training_log.training_log --format single

# Custom output directory
python -m training_log.training_log --output ./reports

# Skip FIT download (faster; loses per-activity HR zone splits)
python -m training_log.training_log --no-fit

# Skip wellness (sleep/recovery) fetching
python -m training_log.training_log --no-wellness

# Quiet mode (no progress output)
python -m training_log.training_log --quiet
```

### CLI options

| Option | Default | Description |
|---|---|---|
| `--weeks N` | `4` | Fetch the last N complete weeks |
| `--from DATE` | — | Start date (`YYYY-MM-DD`), overrides `--weeks` |
| `--to DATE` | today | End date (`YYYY-MM-DD`) |
| `--format` | `weekly` | Report granularity: `weekly`, `monthly`, or `single` |
| `--output DIR` | `./training_logs` | Output directory |
| `--no-fit` | — | Skip FIT download/parsing (no HR zone splits) |
| `--no-wellness` | — | Skip wellness (sleep/recovery) fetching |
| `--quiet` | — | Suppress progress output |

## Project structure

```
training_log/
├── training_log.py  # CLI entry point
├── suunto.py        # suuntool CLI wrapper (workouts + wellness)
├── fit.py           # FIT-file HR stream parsing (fitparse)
├── tss_store.py     # rolling TSS history + CTL/ATL/Form computation
├── config.py        # .env loading and zone boundary calculation
├── process.py       # workout/wellness processing, day-centric aggregation
├── render.py        # day-centric Markdown report generation
└── requirements.txt
```

> **Implementation note:** suuntool's exact subcommands and JSON field names are
> undocumented and not yet verified against a live install. Subcommand construction is
> centralised in `suunto.py` and field extraction is deliberately tolerant (trying several
> candidate key names). See `SPEC.md` "Open Questions" for the fields that need confirming
> against real output — chiefly the TSS field name, where workout notes live, and whether
> raw nightly HRV is exposed.
