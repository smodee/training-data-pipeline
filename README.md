# training-data-pipeline

Personal training data pipeline — pulls activities from the Strava API, computes HR zone distributions, and generates structured Markdown reports suitable for LLM analysis and coaching.

## What it does

- Fetches all your Strava activities for a given date range
- Computes per-activity HR zone splits using your personal VT1/VT2 thresholds
- Calculates pace (runs), speed (rides), and VAM (climbing)
- Includes activity descriptions and private notes from Strava
- Outputs clean Markdown reports (weekly, monthly, or single combined)

### Example output

```
# Training log — week 2026-W12 (2026-03-16 – 2026-03-22)

## Week summary

- Total time: 6:45
- Total distance: 58.3 km (32.1 km running, 26.2 km trail run)
- Total elevation: 1,230 m
- Activities: 4 (2 runs, 1 trail run, 1 weight training)
- HR zone distribution (% of tracked time): Z1 12% · Z2 45% · Z3 30% · Z4 13%
- Avg suffer score: 87.5

## Activities

### 2026-03-16 — Easy long run (run)

- Distance: 18.2 km | Moving time: 1:32 | Elevation: +85 m
- Avg pace: 5:04 /km
- HR: avg 142 bpm / max 158 bpm
- Zone split: Z1 25% · Z2 60% · Z3 15%
- Suffer score: 72
- Description: Progressive build to marathon pace last 5k
- Private note: Left calf felt tight after km 12
```

## Setup

### 1. Create a Strava API application

1. Go to [Strava API Settings](https://www.strava.com/settings/api)
2. Create an application (any name/website is fine)
3. Set the **Authorization Callback Domain** to `localhost`
4. Note your **Client ID** and **Client Secret**

### 2. Clone and install

```bash
git clone <repo-url>
cd training-data-pipeline
pip install -r strava_log/requirements.txt
```

### 3. Configure environment

Copy the example env file and fill in your Strava credentials:

```bash
cp .env.example .env
```

Edit `.env`:

```
STRAVA_CLIENT_ID=12345
STRAVA_CLIENT_SECRET=abc123def456...
```

#### Optional settings

| Variable | Default | Description |
|---|---|---|
| `VT1_BPM` | `145` | Ventilatory threshold 1 (aerobic) heart rate |
| `VT2_BPM` | `171` | Ventilatory threshold 2 (anaerobic) heart rate |
| `MAX_HR` | `191` | Maximum heart rate |
| `OUTPUT_DIR` | `./training_logs` | Directory for generated reports |
| `STRAVA_TOKEN_FILE` | `.strava_tokens.json` | Path to cached OAuth tokens |

HR zones are derived from VT1 and VT2:

| Zone | Range |
|---|---|
| Z0 | Below 80% of VT1 (recovery) |
| Z1 | 80–90% of VT1 (easy aerobic) |
| Z2 | 90% of VT1 to VT1 (moderate aerobic) |
| Z3 | VT1 to VT2 (threshold) |
| Z4 | Above VT2 (high intensity) |

### 4. Authorize

On first run, the tool opens a browser-based OAuth flow. It starts a local server on port 8080 to capture the callback:

```bash
python -m strava_log.strava_log
```

Follow the URL printed in the terminal, authorize in your browser, and tokens are saved automatically to `.strava_tokens.json`.

## Usage

```bash
# Default: last 4 complete weeks, weekly reports
python -m strava_log.strava_log

# Last 8 weeks
python -m strava_log.strava_log --weeks 8

# Specific date range
python -m strava_log.strava_log --from 2026-01-01 --to 2026-03-25

# Monthly reports instead of weekly
python -m strava_log.strava_log --format monthly

# Single combined report
python -m strava_log.strava_log --format single

# Custom output directory
python -m strava_log.strava_log --output ./reports

# Force re-authentication
python -m strava_log.strava_log --auth

# Quiet mode (no progress output)
python -m strava_log.strava_log --quiet
```

### CLI options

| Option | Default | Description |
|---|---|---|
| `--weeks N` | `4` | Fetch the last N complete weeks |
| `--from DATE` | — | Start date (`YYYY-MM-DD`), overrides `--weeks` |
| `--to DATE` | today | End date (`YYYY-MM-DD`) |
| `--format` | `weekly` | Report granularity: `weekly`, `monthly`, or `single` |
| `--output DIR` | `./training_logs` | Output directory |
| `--auth` | — | Force re-authentication |
| `--quiet` | — | Suppress progress output |

## Project structure

```
strava_log/
├── strava_log.py   # CLI entry point
├── auth.py         # OAuth2 flow and token management
├── api.py          # Strava API calls (activities, streams, details)
├── config.py       # .env loading and zone boundary calculation
├── process.py      # Activity processing, zone distribution, aggregation
├── render.py       # Markdown report generation
└── requirements.txt
```
