"""
suite.py - run the whole Fortnite Grind & Combat Efficiency Suite at once.

Order matters:
  1. refresh the match history from the replays in Saved/Demos (shared by everything)
  2. efficiency  - chests per minute, per match and per day
  3. forecast    - how many chests per day to reach the target
  4. loadout     - recommended loadout and combat success
  5. spatial     - best drop zones and looting paths
  6. mode        - solo vs squads comparison
  7. quit        - chest velocity and the optimal time to leave
  8. archive     - stats.csv, and optionally zip/delete old replays

Usage:
    python suite.py                      # report only, safe
    python suite.py --archive            # also zip old counted replays
    python suite.py --archive --delete   # zip, verify, then remove the originals
    python suite.py --out report.txt     # also save the report to a file
"""

import argparse
import sys

import archive
import efficiency
import fc_core as core
import forecast
import insights
import loadout
import mode
import quit as quit_model
import spatial


def refresh_data(verbose=True):
    """Read new/stale replays once, so every report sees the same numbers."""
    data = core.load_data()
    before = sum(1 for r in data["replays"].values() if r.get("ok"))

    def progress(i, total, name):
        if verbose:
            print(f"  reading {i}/{total}: {name}", file=sys.stderr)

    data = core.sync(data, on_progress=progress)
    after = sum(1 for r in data["replays"].values() if r.get("ok"))
    if verbose:
        print(f"  {after} matches counted ({after - before} new)", file=sys.stderr)
    return data


def main():
    ap = argparse.ArgumentParser(description="Fortnite Grind & Combat Efficiency Suite.")
    ap.add_argument("--archive", action="store_true", help="zip counted replays older than --days")
    ap.add_argument("--delete", action="store_true", help="delete originals after archiving")
    ap.add_argument("--days", type=int, default=3, help="archive age in days (default 3)")
    ap.add_argument("--top", type=int, default=5, help="leaderboard rows per day")
    ap.add_argument("--min", type=int, default=2, help="min matches for a loadout pick")
    ap.add_argument("--map", action="store_true", help="include the ASCII drop map")
    ap.add_argument("--queue", type=float, default=0.75,
                    help="minutes lost between matches, for the quit model (default 0.75)")
    ap.add_argument("--out", help="write the report to this file too")
    ap.add_argument("--excel", action="store_true",
                    help="also write Fortnite_Chest_Report.xlsx")
    args = ap.parse_args()

    data = refresh_data()

    parts = [
        insights.report(data),
        efficiency.report(data, top=args.top),
        forecast.report(data),
        loadout.report(data, min_matches=args.min),
        spatial.report(data, show_map=args.map),
        mode.report(data),
        quit_model.report(data, queue=args.queue),
    ]

    rows, csv_path = archive.write_csv(data)
    parts.append(f"DATA LOGGER\n{'=' * 60}\n{rows} matches written to {csv_path}\n")

    if args.archive:
        summary = archive.archive_replays(data, days=args.days, delete=args.delete)
        parts.append(archive.report(summary))

    if args.excel:
        import excel_report
        path = excel_report.build(data)
        parts.append(f"EXCEL REPORT\n{'=' * 60}\n{path}\n")

    text = "\n".join(parts)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"(saved to {args.out})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
