"""One-time backfill of SUUNTO_ACTIVITY_ID_MAP from real workout history.

Rather than trusting an external (unverified) Suunto activity-type table, this learns
the numeric activityId -> internal-key mapping from your own workouts: for every workout
in the range it reads the numeric ``activityId`` and the standardised FIT ``sport`` enum,
then resolves the FIT enum through the same logic the pipeline uses. Because the FIT
vocabulary is a fixed standard, this populates the numeric map in one pass — the map is
only needed as a fallback for ``--no-fit`` runs, so seeding it from FIT data is enough.

The script writes nothing; it prints a ready-to-paste ``SUUNTO_ACTIVITY_ID_MAP`` plus a
report of confirmations, conflicts, and ids the FIT file could not identify (those still
need a human, e.g. via ``inspect_workouts.py``).

Usage:
    python scripts/backfill_activity_ids.py 2024-01-01 2026-07-14
    python scripts/backfill_activity_ids.py 2024-01-01 2026-07-14 --no-fit   # (no-op: FIT required)
"""

import sys
from collections import Counter, defaultdict

sys.path.insert(0, ".")

from training_log.config import load_config
from training_log import suunto
from training_log.fit import parse_fit
from training_log.process import SUUNTO_ACTIVITY_ID_MAP, _map_fit_sport


def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/backfill_activity_ids.py <since> <until>")
        sys.exit(1)
    since, until = sys.argv[1], sys.argv[2]

    cfg = load_config()
    workouts = suunto.list_workouts(cfg, since, until, quiet=True)
    print(f"Scanning {len(workouts)} workout(s) {since} .. {until} for FIT sport data...\n")

    # activityId -> Counter of internal keys resolved from each workout's FIT sport.
    observed = defaultdict(Counter)
    # activityIds seen but whose FIT file yielded no usable sport.
    unidentified = Counter()

    for w in workouts:
        aid = suunto._first(w, "activityId", "activityType", "sport")
        if not isinstance(aid, int):
            continue  # only numeric activityIds are backfilled here
        key = suunto._first(w, "key", "id", "workoutId")
        if not key:
            unidentified[aid] += 1
            continue

        fit_path = suunto.download_fit(cfg, key, quiet=True)
        fit_sport = None
        if fit_path:
            _, _, _, _, fit_sport = parse_fit(fit_path)

        internal = _map_fit_sport(fit_sport)
        if internal:
            observed[aid][internal] += 1
        else:
            unidentified[aid] += 1

    # Build the proposed map: existing entries preserved, new ones from the FIT evidence.
    proposed = dict(SUUNTO_ACTIVITY_ID_MAP)
    confirmations, conflicts, additions = [], [], []

    for aid, counts in sorted(observed.items()):
        winner, _ = counts.most_common(1)[0]
        ambiguous = f"  (mixed: {dict(counts)})" if len(counts) > 1 else ""
        existing = SUUNTO_ACTIVITY_ID_MAP.get(aid)
        if existing is None:
            proposed[aid] = winner
            additions.append(f"  + {aid:>4}: {winner!r}{ambiguous}")
        elif existing == winner:
            confirmations.append(f"    {aid:>4}: {winner!r} (unchanged){ambiguous}")
        else:
            # Keep the existing hand-verified label; a human should adjudicate.
            conflicts.append(
                f"  ! {aid:>4}: map has {existing!r} but FIT suggests {winner!r}{ambiguous}"
            )

    def section(title, lines):
        print(f"{title} ({len(lines)}):")
        print("\n".join(lines) if lines else "  (none)")
        print()

    section("New mappings learned from FIT", additions)
    section("Confirmed existing mappings", confirmations)
    section("Conflicts — review by hand, left unchanged", conflicts)

    still_manual = sorted(a for a in unidentified if a not in proposed)
    if still_manual:
        print("activityIds with no FIT sport and no existing mapping (identify manually):")
        for aid in still_manual:
            print(f"  ? {aid:>4}  (seen {unidentified[aid]}x)")
        print()

    print("# ---- Proposed SUUNTO_ACTIVITY_ID_MAP (paste into training_log/process.py) ----")
    print("SUUNTO_ACTIVITY_ID_MAP = {")
    for aid in sorted(proposed):
        note = "" if aid in SUUNTO_ACTIVITY_ID_MAP else "  # backfilled from FIT"
        print(f"    {aid}: {proposed[aid]!r},{note}")
    print("}")


if __name__ == "__main__":
    main()
