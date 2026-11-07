"""
forecast.py - Dynamic Goal Forecaster.

Reads your accumulated chests, works out how many you still need for the 1,600,000-chest
target by 5 December 2026 (the ~90% confidence point for a 1-in-700,000 token), and turns
that into a chests-per-day number.

It re-runs the maths from your live total every time, so the daily requirement moves as
you fall behind or pull ahead. The plan it printed last time is stored in data.json, so it
can tell you whether today's number went up or down.

Usage:
    python forecast.py
    python forecast.py --target 1600000 --by 2026-12-05
"""

import argparse
import datetime as dt

import fc_core as core


def human_rate(n):
    """A readable chests/day figure: 57,133 is easier to grasp than 57133.0."""
    if n < 10:
        return f"{n:.1f}"
    return core.fmt_int(n)


def forecast(data=None, target=None, by=None):
    """Everything the report needs, as a dict (also used by suite.py)."""
    data = data if data is not None else core.load_data()
    target = int(target or core.TOKEN_TARGET)
    by = by or core.EVENT_END
    today = dt.date.today()

    total = sum(core.totals(data).values())
    days_left = max(1, (by - today).days)          # whole days from tomorrow to the deadline
    remaining = max(0, target - total)
    per_day = remaining / days_left

    days = core.daily_totals(data)
    # Pace over the last 7 days of play (days with no match count as zero).
    window = [(today - dt.timedelta(days=i)).isoformat() for i in range(7)]
    pace = sum(days.get(d, 0) for d in window) / 7

    projected = total + pace * days_left
    days_needed_at_pace = (remaining / pace) if pace > 0 else None

    plan = data.get("settings", {}).get("plan") or {}
    prev = plan.get("per_day")

    return {
        "total": total,
        "target": target,
        "by": by,
        "days_left": days_left,
        "remaining": remaining,
        "per_day": per_day,
        "pace": pace,
        "projected": projected,
        "days_needed_at_pace": days_needed_at_pace,
        "previous_plan": prev,
        "today": today,
        "chance_now": {name: core.chance(total, p) for name, p, _ in core.TOKEN_MODELS},
        "chance_projected": {name: core.chance(projected, p) for name, p, _ in core.TOKEN_MODELS},
    }


def report(data=None, target=None, by=None, save_plan=True):
    f = forecast(data, target, by)
    data = data if data is not None else core.load_data()
    out = ["DYNAMIC GOAL FORECASTER", "=" * 60, ""]
    out.append(f"Chests counted .......... {core.fmt_int(f['total'])}")
    out.append(f"Target .................. {core.fmt_int(f['target'])} by "
               f"{f['by'].day} {core.MONTHS[f['by'].month - 1]} {f['by'].year} "
               f"({f['days_left']} days left)")
    out.append(f"Still needed ............ {core.fmt_int(f['remaining'])}")
    out.append("")
    out.append(f"REQUIRED PER DAY ........ {human_rate(f['per_day'])} chests/day")
    if f["previous_plan"]:
        delta = f["per_day"] - f["previous_plan"]
        if abs(delta) < 0.5:
            out.append(f"   unchanged since the last check "
                       f"({human_rate(f['previous_plan'])}).")
        else:
            arrow = "up" if delta > 0 else "down"
            out.append(f"   yesterday's plan was {human_rate(f['previous_plan'])}, so it went "
                       f"{arrow} by {human_rate(abs(delta))}.")
    out.append(f"YOUR CURRENT PACE ....... {human_rate(f['pace'])} chests/day (last 7 days)")
    out.append("")

    if f["per_day"] > 5000:
        out.append("VERDICT: this target is not reachable by playing.")
        out.append(f"   At {human_rate(f['per_day'])} chests a day for {f['days_left']} days you "
                   "would need to loot non-stop, around the clock.")
        if f["days_needed_at_pace"]:
            out.append(f"   At your real pace you would need "
                       f"{core.fmt_int(f['days_needed_at_pace'])} more days "
                       f"({f['days_needed_at_pace'] / 365:.1f} years).")
        out.append("   Use the per-day number as a grind goal you can actually keep, and")
        out.append("   treat the token as a lottery ticket that might drop on any chest.")
    elif f["per_day"] > 800:
        out.append(f"VERDICT: hard. {human_rate(f['per_day'])} a day is well above normal play;")
        out.append("   only worth it if you are going for the token on purpose.")
    else:
        out.append(f"VERDICT: reachable. {human_rate(f['per_day'])} chests a day is a real goal.")

    out.append("")
    out.append("TOKEN ODDS")
    for name, _, pretty in core.TOKEN_MODELS:
        out.append(f"   {name:22s} ({pretty:12s})  now {core.fmt_chance(f['chance_now'][name]):>7s}"
                   f"   at the end {core.fmt_chance(f['chance_projected'][name]):>7s}")
    out.append(f"   Projected total at your current pace: {core.fmt_int(f['projected'])}")

    if save_plan:
        data.setdefault("settings", {})["plan"] = {
            "date": f["today"].isoformat(),
            "per_day": round(f["per_day"], 1),
            "total": f["total"],
        }
        core.save_data(data)
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description="How many chests per day to reach a target.")
    ap.add_argument("--target", type=int, default=core.TOKEN_TARGET)
    ap.add_argument("--by", type=lambda s: dt.date.fromisoformat(s), default=core.EVENT_END)
    ap.add_argument("--no-save", action="store_true", help="do not store today's plan")
    args = ap.parse_args()
    data = core.sync()
    print(report(data, args.target, args.by, save_plan=not args.no_save))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
