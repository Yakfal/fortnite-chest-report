"""
efficiency.py - Efficiency Analyzer (chests per minute).

Chests Per Minute (CPM) = chests opened / minutes my character was actually in the match.

Why "alive" minutes and not the whole match: the replay keeps running after you die while
the match is still being recorded, so dividing by match length punishes an early death
twice. The reader reports the last moment your pawn was seen (`alive`), which is the time
you could actually loot. Both numbers are kept, so you can compare either way.

Usage:
    python efficiency.py            # leaderboard for every day, newest first
    python efficiency.py --top 10   # more rows per day
    python efficiency.py --all      # every match, one flat list
"""

import argparse

import fc_core as core


def efficiency_rows(matches):
    """One comparable row per match, sorted by CPM (best first)."""
    rows = []
    for m in matches:
        alive = m.get("alive") or 0
        rows.append({
            "when": m.get("when", ""),
            "day": m.get("day", ""),
            "name": m.get("name", ""),
            "chests": m.get("total", 0),
            "alive": alive,
            "duration": m.get("duration", 0),
            "cpm": m.get("cpm", 0.0),
            "cpm_match": m.get("cpm_match", 0.0),
            "first_chest": m.get("first_chest", 0),
            "kills": m.get("kills", 0),
            "deaths": m.get("deaths", 0),
            "placement": m.get("placement"),
        })
    rows.sort(key=lambda r: r["cpm"], reverse=True)
    return rows


def day_summary(rows):
    """Day totals: chests, minutes alive, and the resulting CPM."""
    chests = sum(r["chests"] for r in rows)
    minutes = sum(r["alive"] for r in rows) / 60
    return {
        "matches": len(rows),
        "chests": chests,
        "minutes": minutes,
        "cpm": round(chests / minutes, 2) if minutes else 0.0,
        "best": rows[0] if rows else None,
    }


def report(data=None, top=5, flat=False):
    """Build the text report. Returns a string so any caller can print or show it."""
    data = data if data is not None else core.load_data()
    rows = efficiency_rows(core.matches(data))
    if not rows:
        return "EFFICIENCY\n  No matches counted yet - play a match first.\n"

    out = ["EFFICIENCY - chests per minute", "=" * 60, ""]

    if flat:
        out.append(f"{'MATCH':17s} {'CHESTS':>6s} {'ALIVE':>7s} {'CPM':>5s}  {'K/D':>5s}  PLACE")
        for r in rows:
            out.append(f"{r['when']:17s} {r['chests']:6d} {core.fmt_clock(r['alive']):>7s} "
                       f"{r['cpm']:5.2f}  {r['kills']}/{r['deaths']:<3d}  "
                       f"{r['placement'] if r['placement'] else '-'}")
        return "\n".join(out) + "\n"

    # Group by day, newest day first; inside a day, most efficient match first.
    by_day = {}
    for r in rows:
        by_day.setdefault(r["day"], []).append(r)

    for day in sorted(by_day, reverse=True):
        day_rows = by_day[day]                     # already CPM-sorted
        s = day_summary(day_rows)
        out.append(f"{core.fmt_day(day)}   {s['chests']} chests in "
                   f"{s['minutes']:.0f} min of looting  =  {s['cpm']:.2f} CPM")
        for i, r in enumerate(day_rows[:top], 1):
            out.append(f"  #{i}  {r['when'][-5:]}  {r['cpm']:5.2f} CPM   "
                       f"{r['chests']:2d} chests in {core.fmt_clock(r['alive'])} "
                       f"(match {core.fmt_clock(r['duration'])})   "
                       f"first chest {core.fmt_clock(r['first_chest'])}   "
                       f"K/D {r['kills']}/{r['deaths']}")
        out.append("")

    best = rows[0]
    out.append(f"FASTEST LOOT RUN EVER: {best['when']}  {best['cpm']:.2f} CPM "
               f"({best['chests']} chests in {core.fmt_clock(best['alive'])})")
    out.append("Run the same drop a few times and compare CPM to see which route is faster.")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Chests-per-minute leaderboard.")
    ap.add_argument("--top", type=int, default=5, help="rows per day (default 5)")
    ap.add_argument("--all", action="store_true", help="one flat list of every match")
    args = ap.parse_args()
    data = core.sync()                             # pick up new replays first
    print(report(data, top=args.top, flat=args.all))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
