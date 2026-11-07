"""
mode.py - Mode Comparison & Squad Estimator (Solo vs Squads).

Fortnite replicates a TeamIndex on every player state, and the replay knows which player
state recorded it. So the squad is not a guess: my team's player states are exactly the
people I queued with, and every chest the game credited to one of their pawns was opened by
a teammate.

That gives the "True Squad Chest Count": my chests plus every chest my squad opened in the
same match, with a proximity check (how close that teammate stayed during the drop) to show
whether they were actually looting beside me or across the map.

The comparison that matters:

  * squad chests/hour   - how much loot the team moves through together
  * MY chests/hour      - the only number that feeds my token odds

Those are different questions and the report keeps them apart. A squad can loot far more per
hour than a solo run while *I* personally open less, because three teammates are taking the
chests I would otherwise have had. That is the honest answer to "does squads help my odds",
and it is not always the one people expect.

Usage:
    python mode.py             # solo vs squads comparison
    python mode.py --detail    # plus every match with its teammate breakdown
"""

import argparse
from collections import defaultdict

import fc_core as core

MODES = ("solo", "duos", "squads", "unknown")


def bucket(matches):
    """Matches grouped by mode, newest kept inside each group."""
    out = defaultdict(list)
    for m in matches:
        out[m.get("mode") or "unknown"].append(m)
    return out


def summary(ms):
    """Aggregate numbers for one group of matches."""
    if not ms:
        return None
    hours = sum(m.get("alive", 0) for m in ms) / 3600
    mine = sum(m.get("total", 0) for m in ms)
    squad = sum(m.get("squad_chests", m.get("total", 0)) for m in ms)
    kills = sum(m.get("kills", 0) for m in ms)
    deaths = sum(m.get("deaths", 0) for m in ms)
    places = [m["placement"] for m in ms if m.get("placement")]
    return {
        "matches": len(ms),
        "chests": mine,
        "squad_chests": squad,
        "hours": hours,
        "mine_per_hour": (mine / hours) if hours else 0.0,
        "squad_per_hour": (squad / hours) if hours else 0.0,
        "mine_cpm": (mine / (sum(m.get("alive", 0) for m in ms) / 60)) if hours else 0.0,
        "squad_cpm": (squad / (sum(m.get("alive", 0) for m in ms) / 60)) if hours else 0.0,
        "kills": kills,
        "deaths": deaths,
        "kd": (kills / deaths) if deaths else float(kills),
        "avg_place": (sum(places) / len(places)) if places else None,
        "wins": sum(1 for p in places if p == 1),
        "survival": sum(m.get("alive", 0) for m in ms) / len(ms),
    }


def verdict(solo, squad):
    """A plain answer, with the numbers it is based on."""
    if not solo or not squad:
        return ["  Need at least one solo and one squad match to compare them."]
    lines = []
    mine_delta = squad["mine_per_hour"] - solo["mine_per_hour"]
    squad_delta = squad["squad_per_hour"] - solo["squad_per_hour"]
    better_mine = "SQUADS" if mine_delta >= 0 else "SOLO"
    lines.append(f"  My own chests per hour:  solo {solo['mine_per_hour']:.1f}   "
                 f"squads {squad['mine_per_hour']:.1f}   -> {better_mine} wins for my odds")
    lines.append(f"  Whole squad per hour:    solo {solo['mine_per_hour']:.1f}   "
                 f"squads {squad['squad_per_hour']:.1f}   "
                 f"({'+' if squad_delta >= 0 else ''}{squad_delta:.1f}/h)")
    share = (squad["chests"] / squad["squad_chests"] * 100) if squad["squad_chests"] else 0
    lines.append(f"  In squads I opened {share:.0f}% of the squad's chests.")
    if mine_delta < 0:
        lines.append("  Teammates take chests you would otherwise have had, so the extra squad")
        lines.append("  loot does not reach your account. Squads only help your odds if they")
        lines.append("  keep you alive longer or let you loot faster than you lose.")
    else:
        lines.append("  Squads are genuinely faster for you: more of your time turns into")
        lines.append("  chests, probably because someone else is fighting while you loot.")
    return lines


def report(data=None, detail=False):
    data = data if data is not None else core.load_data()
    ms = core.matches(data)
    out = ["MODE COMPARISON - SOLO VS SQUADS", "=" * 60, ""]
    if not ms:
        return "\n".join(out) + "  No matches yet.\n"

    groups = bucket(ms)
    stats = {k: summary(v) for k, v in groups.items()}

    out.append(f"  {'MODE':8s} {'MTCH':>4s} {'HOURS':>6s} {'MINE/H':>7s} "
               f"{'SQUAD/H':>8s} {'K/D':>5s} {'SURV':>6s} {'WIN%':>5s}")
    for mode in MODES:
        s = stats.get(mode)
        if not s:
            continue
        win = (s["wins"] / s["matches"] * 100) if s["matches"] else 0
        out.append(f"  {mode:8s} {s['matches']:4d} {s['hours']:6.1f} {s['mine_per_hour']:7.1f} "
                   f"{s['squad_per_hour']:8.1f} {s['kd']:5.2f} "
                   f"{core.fmt_clock(s['survival']):>6s} {win:5.0f}")

    out.append("")
    out.append("VERDICT")
    out += verdict(stats.get("solo"), stats.get("squads"))
    if stats.get("unknown"):
        out.append("  (Matches with no replicated team index are listed as unknown and left")
        out.append("   out of the comparison.)")

    # Squad detail: teammates, what they opened, and how close they stayed.
    squad_ms = [m for m in ms if m.get("mode") in ("squads", "duos") and m.get("mates")]
    if squad_ms:
        total_squad = sum(m.get("squad_chests", 0) for m in squad_ms)
        total_mine = sum(m.get("total", 0) for m in squad_ms)
        mate_chests = total_squad - total_mine
        avg_mates = sum(len(m["mates"]) for m in squad_ms) / len(squad_ms)
        near = [mate["prox"] for m in squad_ms for mate in m["mates"]]
        out.append("")
        out.append("SQUAD ESTIMATE")
        out.append(f"  {len(squad_ms)} squad matches, {avg_mates:.1f} teammates on average")
        out.append(f"  My chests {total_mine}, teammates' chests {mate_chests}, "
                   f"true squad total {total_squad}")
        if near:
            out.append(f"  Teammates within 40 m of me during the drop: "
                       f"{sum(near) / len(near):.0f}% of the time on average")
        most = max(squad_ms, key=lambda m: m.get("squad_chests", 0))
        out.append(f"  Best squad match: {most.get('when', '')[:16]} - "
                   f"{most.get('total', 0)} mine + {most.get('squad_chests', 0) - most.get('total', 0)} "
                   f"from teammates")

    if detail:
        out.append("")
        out.append("MATCH DETAIL")
        for m in sorted(ms, key=lambda m: m.get("when", ""), reverse=True):
            mates = m.get("mates") or []
            extra = ""
            if mates:
                parts = [f"{mt['chests']}c/{mt['prox']}% near" for mt in mates]
                extra = "   mates: " + ", ".join(parts)
            out.append(f"  {m.get('when', '')[:16]}  {m.get('mode', '?'):7s} "
                       f"mine {m.get('total', 0):2d}  squad {m.get('squad_chests', m.get('total', 0)):2d}"
                       f"{extra}")

    out.append("")
    out.append("HOW THIS IS MEASURED")
    out.append("  Team membership comes from the replicated TeamIndex, so a teammate's chest is")
    out.append("  a chest the game itself credited to their pawn - not proximity guesswork.")
    out.append("  'Near' is the share of the first six minutes they spent within 40 m of me.")
    out.append("  Squad chests do NOT improve my token odds: only chests I open count, which")
    out.append("  is why the verdict leads with my own chests per hour.")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Compare solo and squad efficiency.")
    ap.add_argument("--detail", action="store_true", help="list every match")
    args = ap.parse_args()
    data = core.sync()
    print(report(data, detail=args.detail))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
