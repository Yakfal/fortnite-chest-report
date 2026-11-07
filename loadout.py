"""
loadout.py - Loadout & Combat Success Analyzer.

What comes from where:

  kills, deaths, survival, placement   -> replicated player state and the kill feed (exact)
  items you looted                     -> the contents of the chests you opened (exact)
  which weapon fired which kill        -> NOT in a modern replay, so it is never invented

Because a replay does not say "this kill was with that gun", the success rate of a weapon is
measured across the matches where you looted it: kills, deaths, K/D, win rate and how long you
lived. That is weaker than per-kill attribution and the report says so, but with enough
matches it still separates the guns you do well with from the ones you die holding.

Small samples are shrunk towards your overall K/D before ranking, so a lucky two-match weapon
does not beat a gun that has been good for twenty matches.

Usage:
    python loadout.py                 # recommended loadout + full tables
    python loadout.py --min 3         # only items seen in >= 3 matches
"""

import argparse

import fc_core as core

# How many "average deaths" of evidence a new item starts with. A 2-match item is pulled
# strongly towards the overall K/D; a 20-match item is mostly its own record.
PRIOR_DEATHS = 6.0


def _raw(item):
    """The most descriptive name we have for an item (raw class name when present)."""
    return item.get("raw") or item.get("item") or ""


def _name(item):
    """Normalise the display name so R/VR/SR variants of one gun merge into one row."""
    return core.clean_item(_raw(item))


def _kind(item):
    """Reclassify from the raw class name when present, so old bad data is fixed too."""
    return core.classify_loot(_raw(item))


def weapon_stats(matches):
    """One row per weapon/utility: how often looted, and how those matches went."""
    stats = {}
    for m in matches:
        seen = set()
        for item in m.get("loadout") or []:
            kind = _kind(item)
            name = _name(item)
            key = (name, kind)
            if key in seen:                       # one match counts once per item
                continue
            seen.add(key)
            row = stats.setdefault(key, {
                "item": name, "kind": kind, "raw": _raw(item),
                "matches": 0, "kills": 0, "deaths": 0, "chests": 0,
                "survival": 0, "placement": [], "cpm": 0.0, "wins": 0, "top5": 0,
            })
            row["matches"] += 1
            row["kills"] += m.get("kills", 0)
            row["deaths"] += m.get("deaths", 0)
            row["chests"] += m.get("total", 0)
            row["survival"] += m.get("alive", 0)
            row["cpm"] += m.get("cpm", 0.0)
            place = m.get("placement")
            if place:
                row["placement"].append(place)
                if place == 1:
                    row["wins"] += 1
                if place <= 5:
                    row["top5"] += 1

    baseline = combat_summary(matches)
    prior_kd = baseline["kd"]
    for row in stats.values():
        n = row["matches"]
        row["kd"] = row["kills"] / row["deaths"] if row["deaths"] else float(row["kills"])
        row["kd_shrunk"] = ((row["kills"] + prior_kd * PRIOR_DEATHS)
                            / (row["deaths"] + PRIOR_DEATHS))
        row["lift"] = row["kd_shrunk"] - prior_kd
        row["win_rate"] = row["wins"] / n if n else 0.0
        row["top5_rate"] = row["top5"] / n if n else 0.0
        row["avg_survival"] = row["survival"] / n
        row["avg_chests"] = row["chests"] / n
        row["avg_cpm"] = row["cpm"] / n
        row["avg_placement"] = (sum(row["placement"]) / len(row["placement"])
                                if row["placement"] else None)
        # Success, not popularity: shrunk K/D first, then wins, then how long the average
        # match lasted and how fast the loot came in.
        row["score"] = (row["kd_shrunk"] * 2.0
                        + row["win_rate"] * 6.0
                        + row["top5_rate"] * 2.0
                        + row["avg_survival"] / 600.0
                        + row["avg_cpm"] / 4.0)
    return stats


def combat_summary(matches):
    kills = sum(m.get("kills", 0) for m in matches)
    deaths = sum(m.get("deaths", 0) for m in matches)
    surv = [m.get("alive", 0) for m in matches]
    places = [m["placement"] for m in matches if m.get("placement")]
    return {
        "matches": len(matches),
        "kills": kills,
        "deaths": deaths,
        "kd": (kills / deaths) if deaths else float(kills),
        "avg_survival": (sum(surv) / len(surv)) if surv else 0,
        "avg_placement": (sum(places) / len(places)) if places else None,
        "wins": sum(1 for p in places if p == 1),
        "win_rate": (sum(1 for p in places if p == 1) / len(places)) if places else 0.0,
    }


def ranked(matches, kind="weapon", min_matches=2):
    """Rows for one kind, best first. Used by insights, the HUD and the Excel report."""
    rows = [r for r in weapon_stats(matches).values() if r["kind"] == kind]
    reliable = [r for r in rows if r["matches"] >= min_matches] or rows
    return sorted(reliable, key=lambda r: (r["score"], r["matches"]), reverse=True)


def best_weapon(matches, min_matches=2):
    """The single most successful weapon, or None when nothing is classified yet."""
    rows = ranked(matches, "weapon", min_matches)
    return rows[0] if rows else None


def report(data=None, min_matches=2):
    """Recommended loadout plus the tables behind it."""
    data = data if data is not None else core.load_data()
    ms = core.matches(data)
    if not ms:
        return "LOADOUT & COMBAT\n  No matches yet - play a match first.\n"

    c = combat_summary(ms)
    stats = weapon_stats(ms)
    weapons = [r for r in stats.values() if r["kind"] == "weapon"]
    utility = [r for r in stats.values() if r["kind"] == "utility"]
    other = [r for r in stats.values() if r["kind"] == "other"]

    out = ["LOADOUT & COMBAT SUCCESS", "=" * 60, ""]
    out.append(f"Matches {c['matches']}   kills {c['kills']}   deaths {c['deaths']}   "
               f"K/D {c['kd']:.2f}   wins {c['wins']} ({c['win_rate'] * 100:.0f}%)")
    out.append(f"Average survival {core.fmt_clock(c['avg_survival'])}"
               + (f"   average placement {c['avg_placement']:.1f}"
                  if c["avg_placement"] else ""))
    out.append("")

    reliable = [r for r in weapons if r["matches"] >= min_matches]
    if reliable:
        top = sorted(reliable, key=lambda r: r["score"], reverse=True)[:3]
        out.append("RECOMMENDED LOADOUT (most successful weapons, not just most looted)")
        for i, r in enumerate(top, 1):
            out.append(f"  {i}. {r['item']}")
            out.append(f"     {r['matches']} matches   K/D {r['kd']:.2f} "
                       f"(shrunk {r['kd_shrunk']:.2f})   win {r['win_rate'] * 100:.0f}%   "
                       f"avg survival {core.fmt_clock(r['avg_survival'])}   "
                       f"avg chests {r['avg_chests']:.1f}")
    else:
        out.append("RECOMMENDED LOADOUT: not enough data yet.")
        out.append(f"   No weapon appears in {min_matches}+ matches. Play more matches, or")
        out.append(f"   run with --min 1 to see everything anyway.")

    if utility:
        best_u = sorted([r for r in utility if r["matches"] >= min_matches] or utility,
                        key=lambda r: r["score"], reverse=True)[:3]
        out.append("")
        out.append("BEST UTILITY")
        for r in best_u:
            out.append(f"  - {r['item']:28s} {r['matches']} matches   K/D {r['kd']:.2f}   "
                       f"win {r['win_rate'] * 100:.0f}%   "
                       f"avg survival {core.fmt_clock(r['avg_survival'])}")

    def table(title, rows):
        rows = sorted(rows, key=lambda r: r["score"], reverse=True)
        if not rows:
            return
        out.append("")
        out.append(title)
        out.append(f"  {'ITEM':30s} {'MTCH':>4s} {'K/D':>5s} {'SHRNK':>5s} "
                   f"{'WIN%':>5s} {'SURV':>6s} {'CHEST':>5s} {'CPM':>5s}")
        for r in rows[:12]:
            out.append(f"  {r['item'][:30]:30s} {r['matches']:4d} {r['kd']:5.2f} "
                       f"{r['kd_shrunk']:5.2f} {r['win_rate'] * 100:5.0f} "
                       f"{core.fmt_clock(r['avg_survival']):>6s} {r['avg_chests']:5.1f} "
                       f"{r['avg_cpm']:5.2f}")

    table("WEAPONS", weapons)
    table("UTILITY", utility)
    if other:
        out.append("")
        out.append("UNCLASSIFIED LOOT (not counted as weapons)")
        for r in sorted(other, key=lambda r: r["matches"], reverse=True)[:8]:
            out.append(f"  - {r['item']:30s} {r['matches']} matches")

    out.append("")
    out.append("HOW TO READ THIS")
    out.append("  A modern Fortnite replay does not record which weapon fired which shot, so")
    out.append("  these are the matches in which you looted that item, not kills scored with it.")
    out.append("  K/D is shrunk towards your overall K/D by a few matches of evidence, so a")
    out.append("  two-match fluke cannot top the table. Small samples still lie; treat the")
    out.append("  top of the list as a hypothesis to test by dropping the same way again.")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Loadout and combat success report.")
    ap.add_argument("--min", type=int, default=2,
                    help="minimum matches for an item to be recommended (default 2)")
    args = ap.parse_args()
    data = core.sync()
    print(report(data, min_matches=args.min))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
