"""
quit.py - Optimal Quit-Time & Velocity Analyzer.

Every chest carries the second it was opened, so a match is a sequence of loot events over
time. That sequence is the point: chests come thick and fast early in a drop and then dry up.
"when should I leave" is really "when does another minute here earn less than another minute
in a fresh match".

The model, in three steps:

  1. velocity curve  - chests opened per minute of match time, divided by the number of
                       matches still alive at that minute. The division matters: without it
                       the curve would fall just because players die, not because loot dries up.

  2. restart value   - chests per minute of a fresh match over its first Q minutes:
                       C(Q) / (Q + QUEUE). If your first five minutes are worth more per
                       minute than minute 12 of the current match, re-queueing wins.

  3. optimum         - the Q that maximises that fresh-match rate. That is the quit time, and
                       the report shows the chests/hour it implies against playing on.

It is a per-hour chests model and nothing else: leaving early throws away placement, XP and
your team's game. The report says so instead of pretending otherwise.
"""

import argparse
import statistics
from collections import defaultdict

import fc_core as core

QUEUE = 0.75       # minutes lost between matches: leave, queue, load (drop time is in the data)
MAX_MIN = 20       # how far into a match the curve is modelled
MIN_MIN = 3        # never recommend quitting inside the first three minutes


def velocity_curve(matches, max_min=MAX_MIN):
    """
    Chests per minute for each minute of match time, normalised by the matches still alive.
    Returns a list of dicts: minute, chests, alive, rate.
    """
    per_minute = defaultdict(int)
    for m in matches:
        for c in m.get("chests") or []:
            minute = int(c.get("t") or 0) // 60 + 1
            if 1 <= minute <= max_min:
                per_minute[minute] += 1

    curve = []
    for minute in range(1, max_min + 1):
        alive = sum(1 for m in matches if (m.get("alive") or 0) >= (minute - 1) * 60)
        if alive == 0:
            break
        curve.append({"minute": minute, "chests": per_minute[minute],
                      "alive": alive, "rate": per_minute[minute] / alive})
    return curve


def smooth(curve, window=3):
    """Moving average of the rate, so one quiet minute does not become a verdict."""
    out = []
    for i, row in enumerate(curve):
        chunk = curve[max(0, i - window + 1):i + 1]
        out.append({**row, "smooth": sum(c["rate"] for c in chunk) / len(chunk)})
    return out


def knee(curve, peak_window=5):
    """
    The inflection point: the first minute after the early peak where the smoothed rate drops
    below half the peak and stays below it the next minute.
    """
    early = curve[:peak_window]
    if not early:
        return None, 0.0
    peak_row = max(early, key=lambda r: r["smooth"])
    peak = peak_row["smooth"]
    if peak <= 0:
        return None, 0.0
    # Only look after the peak: before it the rate is still climbing, not falling off.
    start = next(i for i, r in enumerate(curve) if r["minute"] == peak_row["minute"])
    for i in range(start, len(curve) - 1):
        if curve[i]["smooth"] < peak * 0.5 and curve[i + 1]["smooth"] < peak * 0.5:
            return curve[i]["minute"], peak
    return None, peak


def cumulative(curve, q):
    """Expected chests in the first q minutes of a fresh match."""
    return sum(r["rate"] for r in curve if r["minute"] <= q)


def optimum(curve, queue=None, min_min=MIN_MIN):
    """
    Quit minute that maximises chests per minute of wall-clock time, counting the queue gap as
    dead time. Returns (best_minute, best_per_hour, table_of_candidates).
    """
    queue = QUEUE if queue is None else queue
    table, best_q, best_rate = [], None, 0.0
    for q in range(min_min, curve[-1]["minute"] + 1):
        per_minute = cumulative(curve, q) / (q + queue)     # chests per minute of wall clock
        table.append({"minute": q, "chests": cumulative(curve, q), "per_hour": per_minute * 60})
        if per_minute > best_rate:                          # compare like with like
            best_q, best_rate = q, per_minute
    if best_q is None:
        # Match history too short to reach the minimum window (a single early death).
        last = curve[-1]["minute"]
        per_minute = cumulative(curve, last) / (last + queue)
        return last, per_minute * 60, [{"minute": last, "chests": cumulative(curve, last),
                                        "per_hour": per_minute * 60}]
    return best_q, best_rate * 60, table


def analyse_matches(ms, queue=None):
    """Everything the report and the CSV need, for one list of matches."""
    queue = QUEUE if queue is None else queue
    if not ms:
        return None
    raw = velocity_curve(ms)
    if not raw:
        return None
    curve = smooth(raw)
    knee_min, peak = knee(curve)
    best_min, best_per_hour, table = optimum(curve, queue=queue)
    full_min = curve[-1]["minute"]
    full_per_hour = cumulative(curve, full_min) / (full_min + queue) * 60
    firsts = [m["first_chest"] for m in ms if m.get("first_chest")]
    return {
        "matches": len(ms),
        "curve": curve,
        "knee": knee_min,
        "peak": peak,
        "quit_at": best_min,
        "per_hour": best_per_hour,
        "full_per_hour": full_per_hour,
        "table": table,
        "median_first_chest": statistics.median(firsts) if firsts else 0,
        "avg_alive": sum(m.get("alive", 0) for m in ms) / len(ms),
        "avg_chests": sum(m.get("total", 0) for m in ms) / len(ms),
    }


def analyse(data=None, queue=None):
    """Everything the report and the CSV need."""
    data = data if data is not None else core.load_data()
    return analyse_matches(core.matches(data), queue=queue)


def by_mode(data=None, queue=None, min_matches=2):
    """
    One quit model per mode, so solo and squads get their own recommendation.

    A mode with too few matches is left out: a curve built on one game is noise.
    """
    data = data if data is not None else core.load_data()
    groups = {}
    for m in core.matches(data):
        groups.setdefault(m.get("mode") or "unknown", []).append(m)
    out = {}
    for mode, ms in groups.items():
        if len(ms) < min_matches:
            continue
        model = analyse_matches(ms, queue=queue)
        if model:
            out[mode] = model
    return out


def recommendation(model):
    """The headline numbers, in a shape the UI and Excel can use directly."""
    if not model:
        return {"quit_at": None, "quit_clock": "-", "per_hour": 0.0,
                "full_per_hour": 0.0, "diff": 0.0, "advice": "not enough data"}
    diff = model["per_hour"] - model["full_per_hour"]
    return {
        "quit_at": model["quit_at"],
        "quit_clock": core.fmt_clock((model["quit_at"] - 1) * 60),
        "per_hour": model["per_hour"],
        "full_per_hour": model["full_per_hour"],
        "diff": diff,
        "advice": "Leaving early" if diff > 0.5 else "Playing it out",
        "knee": model.get("knee"),
        "peak": model.get("peak", 0.0),
        "matches": model.get("matches", 0),
    }


def quit_time_for(match, model):
    """
    Per-match quit time for stats.csv: the model's optimum, or the moment I died if that came
    first. Formatted mm:ss.
    """
    if not model or not model.get("quit_at"):
        return "-"
    seconds = model["quit_at"] * 60
    alive = match.get("alive") or 0
    if alive:
        seconds = min(seconds, alive)
    return core.fmt_clock(seconds)


def report(data=None, queue=None):
    queue = QUEUE if queue is None else queue
    model = analyse(data, queue=queue)
    out = ["QUIT-TIME & CHEST VELOCITY", "=" * 60, ""]
    if not model:
        return "\n".join(out) + "  No chests recorded yet.\n"

    curve = model["curve"]
    top = max(curve, key=lambda r: r["smooth"])
    out.append(f"{model['matches']} matches, {model['avg_chests']:.0f} chests each, "
               f"{core.fmt_clock(model['avg_alive'])} alive on average")
    out.append(f"First chest at {core.fmt_clock(model['median_first_chest'])} (median)")
    out.append("")
    out.append("CHEST VELOCITY (chests per minute of match time)")
    scale = max((r["smooth"] for r in curve), default=1) or 1
    for r in curve:
        bar = "#" * int(round(r["smooth"] / scale * 22))
        out.append(f"  {r['minute']:2d}:00  {bar:<22s} {r['smooth']:.2f}")

    out.append("")
    out.append(f"PEAK VELOCITY: {top['smooth']:.2f} chests/min at about "
               f"{core.fmt_clock((top['minute'] - 1) * 60)} into the match")
    if model["knee"]:
        out.append(f"DROP-OFF: velocity halves by minute {model['knee']} "
                   f"({core.fmt_clock((model['knee'] - 1) * 60)})")
    else:
        out.append("DROP-OFF: no clear halving yet - more matches will sharpen this.")

    out.append("")
    out.append("OPTIMAL QUIT")
    out.append(f"  Restarting costs about {queue * 60:.0f}s of queue and loading.")
    out.append(f"  Best quit minute: {model['quit_at']} "
               f"({core.fmt_clock((model['quit_at'] - 1) * 60)} of match time)")
    out.append(f"  {model['per_hour']:.0f} chests/hour quitting there, vs "
               f"{model['full_per_hour']:.0f} playing every match out")
    diff = model["per_hour"] - model["full_per_hour"]
    advice = "leaving early" if diff > 0.5 else "staying"
    out.append(f"  {advice} is the better trade by {abs(diff):.0f} chests/hour")

    out.append("")
    out.append("  QUIT   CHESTS BY THEN   CHESTS/HOUR IF REPEATED")
    for row in model["table"]:
        if row["minute"] % 2 == 1 or row["minute"] == model["quit_at"]:
            mark = "  <-- best" if row["minute"] == model["quit_at"] else ""
            out.append(f"  {row['minute']:2d}:00  {row['chests']:14.1f}   "
                       f"{row['per_hour']:20.0f}{mark}")

    modes = by_mode(data, queue=queue)
    if modes:
        out.append("")
        out.append("BEST EXIT BY MODE")
        out.append(f"  {'MODE':8s} {'MTCH':>4s} {'QUIT':>6s} {'CHESTS/H':>8s} "
                   f"{'FULL/H':>7s} {'DIFF':>6s}")
        for mode in ("solo", "duos", "squads", "unknown"):
            m = modes.get(mode)
            if not m:
                continue
            r = recommendation(m)
            out.append(f"  {mode:8s} {r['matches']:4d} {r['quit_clock']:>6s} "
                       f"{r['per_hour']:8.0f} {r['full_per_hour']:7.0f} "
                       f"{r['diff']:+6.0f}")
        out.append("  A mode needs at least two matches before it gets its own curve.")

    out.append("")
    out.append("WHAT THIS DOES AND DOES NOT SAY")
    out.append(f"  It says: past roughly minute {model['quit_at']}, another minute here earns")
    out.append("  fewer chests than another minute in a fresh match, queue included.")
    out.append("  It does not say leaving is free. You give up placement, XP, quest progress")
    out.append("  and your squad's game. Leaving early in squads costs teammates too.")
    out.append("  The curve comes from your own replays, so it sharpens as you play more.")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Chest velocity and optimal quit time.")
    ap.add_argument("--queue", type=float, default=QUEUE,
                    help="minutes lost between matches (default 0.75)")
    args = ap.parse_args()
    data = core.sync()
    print(report(data, queue=args.queue))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
