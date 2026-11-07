"""
insights.py - the short answer layer.

The seven analysis modules each explain one thing in detail. This module answers the three
questions a player actually asks after a session, in one place:

  * which weapons go best for me?          -> loadout.ranked(), with shrunk K/D and win rate
  * where do I get the most chests?        -> spatial zones, ranked by yield x quietness
  * how long should I stay in a match?     -> quit.analyse(), overall and per mode

It also rolls up the overview numbers and the daily history. Everything is computed from
data.json, so the desktop app, the HUD, the text report and the Excel workbook all show the
same numbers.

Usage:
    python insights.py             # short text report
    python insights.py --full      # include the longer tables
"""

import argparse
import datetime as dt
import statistics

import fc_core as core
import loadout
import mode as mode_mod
import quit as quit_model
import spatial


def _daily(ms):
    days = {}
    for m in ms:
        day = m.get("day") or ""
        if not day:
            continue
        row = days.setdefault(day, {"day": day, "matches": 0, "chests": 0,
                                    "alive": 0, "kills": 0, "deaths": 0, "wins": 0})
        row["matches"] += 1
        row["chests"] += m.get("total", 0)
        row["alive"] += m.get("alive", 0)
        row["kills"] += m.get("kills", 0)
        row["deaths"] += m.get("deaths", 0)
        if m.get("placement") == 1:
            row["wins"] += 1
    out = []
    for row in days.values():
        row["cpm"] = round(row["chests"] / (row["alive"] / 60), 2) if row["alive"] else 0.0
        out.append(row)
    out.sort(key=lambda r: r["day"])
    return out


def build(data=None):
    """Every number the app, HUD, text report and Excel workbook need, in one dict."""
    data = data if data is not None else core.load_data()
    ms = core.matches(data)
    totals = core.totals(data)
    hours = sum(m.get("alive", 0) for m in ms) / 3600
    combat = loadout.combat_summary(ms)
    firsts = [m["first_chest"] for m in ms if m.get("first_chest")]

    zones, _ = spatial.analyse(data)
    zone_list = spatial.zone_rows(zones)
    best_zone_list = spatial.zone_rows(zones, min_visits=spatial.MIN_VISITS) or zone_list

    quit_all = quit_model.analyse(data)
    quit_rec = quit_model.recommendation(quit_all)
    quit_modes = quit_model.by_mode(data)

    weapons = loadout.ranked(ms, "weapon")
    utility = loadout.ranked(ms, "utility")
    other = loadout.ranked(ms, "other")
    modes = {k: v for k, v in
             ((name, mode_mod.summary(group))
              for name, group in mode_mod.bucket(ms).items()) if v}

    daily = _daily(ms)
    best_day = max(daily, key=lambda r: r["chests"]) if daily else None
    last = max(ms, key=lambda m: m.get("when", "")) if ms else None

    overview = {
        "matches": len(ms),
        "chests": sum(totals.values()),
        "normal": totals["normal"],
        "epic": totals["epic"],
        "sprite": totals["sprite"],
        "hours": hours,
        "chests_per_hour": (sum(totals.values()) / hours) if hours else 0.0,
        "cpm": (sum(totals.values()) / (hours * 60)) if hours else 0.0,
        "kills": combat["kills"],
        "deaths": combat["deaths"],
        "kd": combat["kd"],
        "wins": combat["wins"],
        "win_rate": combat["win_rate"],
        "avg_survival": combat["avg_survival"],
        "avg_placement": combat["avg_placement"],
        "avg_chests": (sum(totals.values()) / len(ms)) if ms else 0.0,
        "median_first_chest": statistics.median(firsts) if firsts else 0,
        "days_played": len(daily),
        "best_day": best_day,
        "last_match": last.get("when", "") if last else "",
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    return {
        "overview": overview,
        "weapons": weapons,
        "utility": utility,
        "other": other,
        "zones": zone_list,
        "zones_raw": zones,
        "best_zones": best_zone_list,
        "quit": quit_rec,
        "quit_model": quit_all,
        "quit_by_mode": quit_modes,
        "modes": modes,
        "daily": daily,
        "matches": ms,
    }


def _weapon_line(row):
    return (f"{row['item']}  -  {row['matches']} matches, K/D {row['kd']:.2f} "
            f"(shrunk {row['kd_shrunk']:.2f}), win {row['win_rate'] * 100:.0f}%, "
            f"survival {core.fmt_clock(row['avg_survival'])}")


def _zone_line(row):
    return (f"Zone {row['name']}  -  {row['chests']} chests in {row['visits']} visits "
            f"({row['per_visit']:.1f}/visit), {row['quiet']:.0f}% quiet, "
            f"score {row['score']:.1f}")


def sections(data=None):
    """Tab-ready text, one string per topic. Used by the Insights window."""
    b = build(data)
    o = b["overview"]
    out = {}

    ov = ["OVERVIEW", "=" * 60, ""]
    if not o["matches"]:
        ov.append("  No matches counted yet. Play one and rescan.")
    else:
        ov.append(f"  {o['matches']} matches on {o['days_played']} days")
        ov.append(f"  {o['chests']:,} chests  ({o['normal']:,} normal, {o['epic']:,} epic, "
                  f"{o['sprite']:,} sprite)")
        ov.append(f"  {o['chests_per_hour']:.0f} chests/hour   {o['cpm']:.2f} CPM   "
                  f"{o['avg_chests']:.1f} chests/match")
        ov.append(f"  K/D {o['kd']:.2f}   {o['wins']} wins ({o['win_rate'] * 100:.0f}%)   "
                  f"avg placement {o['avg_placement']:.1f}"
                  if o["avg_placement"] else f"  K/D {o['kd']:.2f}   {o['wins']} wins")
        ov.append(f"  average survival {core.fmt_clock(o['avg_survival'])}   "
                  f"first chest at {core.fmt_clock(o['median_first_chest'])} (median)")
        if o["best_day"]:
            d = o["best_day"]
            ov.append(f"  best day: {core.fmt_day(d['day'])} - {d['chests']} chests "
                      f"in {d['matches']} matches")
        ov.append(f"  last match: {o['last_match']}")
    out["Overview"] = "\n".join(ov) + "\n"

    wp = ["BEST WEAPONS", "=" * 60, ""]
    if not b["weapons"]:
        wp.append("  No weapons classified yet.")
    else:
        wp.append("  Ranked by success in the matches where you looted them.")
        wp.append("  (A replay cannot say which gun fired which shot - see About.)")
        wp.append("")
        for i, row in enumerate(b["weapons"][:10], 1):
            wp.append(f"  {i:2d}. {_weapon_line(row)}")
    if b["utility"]:
        wp.append("")
        wp.append("BEST UTILITY")
        for row in b["utility"][:6]:
            wp.append(f"   - {_weapon_line(row)}")
    if b["other"]:
        wp.append("")
        wp.append("UNCLASSIFIED LOOT")
        for row in b["other"][:6]:
            wp.append(f"   - {row['item']}  ({row['matches']} matches)")
    out["Weapons"] = "\n".join(wp) + "\n"

    zn = ["DROP ZONES", "=" * 60, ""]
    if not b["zones"]:
        zn.append("  No chest positions stored yet.")
    else:
        zn.append("  Ranked by chests per visit x quietness (how little of the area")
        zn.append("  the rest of the lobby looted).")
        zn.append("")
        for i, row in enumerate(b["best_zones"][:10], 1):
            zn.append(f"  {i:2d}. {_zone_line(row)}")
        zn.append("")
        zn.append("  Zones are coordinate clusters (~120 m), not named POIs: replays")
        zn.append("  carry positions but no place names. The grid label stays stable.")
    out["Drop zones"] = "\n".join(zn) + "\n"

    q = ["WHEN TO QUIT", "=" * 60, ""]
    if not b["quit_model"]:
        q.append("  No chest timings recorded yet.")
    else:
        r = b["quit"]
        q.append(f"  Best exit: {r['quit_clock']} of match time "
                 f"(minute {r['quit_at']})")
        q.append(f"  {r['per_hour']:.0f} chests/hour quitting there, vs "
                 f"{r['full_per_hour']:.0f} playing every match out")
        q.append(f"  {r['advice']} is the better chests-per-hour trade "
                 f"by {abs(r['diff']):.0f} chests/hour")
        if b["quit_by_mode"]:
            q.append("")
            q.append("  BY MODE")
            for name in ("solo", "duos", "squads", "unknown"):
                m = b["quit_by_mode"].get(name)
                if not m:
                    continue
                mr = quit_model.recommendation(m)
                q.append(f"   {name:7s} {mr['matches']:2d} matches   quit {mr['quit_clock']:>5s}   "
                         f"{mr['per_hour']:5.0f} chests/h vs {mr['full_per_hour']:5.0f} full")
        q.append("")
        q.append("  This is a chests-per-hour model only. Leaving early costs placement,")
        q.append("  XP, quest progress and - in squads - your teammates' game.")
    out["Quit time"] = "\n".join(q) + "\n"

    md = ["MODE COMPARISON", "=" * 60, ""]
    if not b["modes"]:
        md.append("  No matches yet.")
    else:
        md.append(f"  {'MODE':8s} {'MTCH':>4s} {'HOURS':>6s} {'MINE/H':>7s} "
                  f"{'SQUAD/H':>8s} {'K/D':>5s} {'WIN%':>5s}")
        for name in ("solo", "duos", "squads", "unknown"):
            s = b["modes"].get(name)
            if not s:
                continue
            win = (s["wins"] / s["matches"] * 100) if s["matches"] else 0
            md.append(f"  {name:8s} {s['matches']:4d} {s['hours']:6.1f} "
                      f"{s['mine_per_hour']:7.1f} {s['squad_per_hour']:8.1f} "
                      f"{s['kd']:5.2f} {win:5.0f}")
        md.append("")
        md.append("  Mine/hour is the number that feeds your token odds. Squad/hour is")
        md.append("  the whole team's loot and does not transfer to your account.")
    out["Modes"] = "\n".join(md) + "\n"

    about = ["ABOUT THESE NUMBERS", "=" * 60, ""]
    about.append("  Source: the .replay files Fortnite writes locally. No game memory,")
    about.append("  no overlays injected, nothing that can affect a match.")
    about.append("  Chests are credited only when the replay says YOU opened them.")
    about.append("  Kills, deaths, survival and placement come from replicated player state.")
    about.append("  Weapon success is measured across matches where you looted the item,")
    about.append("  because a modern replay does not record which weapon fired which shot.")
    about.append("  Small samples are shrunk towards your overall K/D before ranking.")
    about.append("  Zone names are stable grid labels, not POI names: replays do not carry")
    about.append("  location names. 'Quiet' is measured from chests other players opened.")
    about.append(f"  Generated {o['generated']} by the Fortnite Grind & Combat Efficiency Suite.")
    out["About"] = "\n".join(about) + "\n"
    return out


def report(data=None, full=False):
    """One text report with every section."""
    parts = []
    for title, text in sections(data).items():
        if not full and title == "About":
            continue
        parts.append(text)
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser(description="Short answer insights report.")
    ap.add_argument("--full", action="store_true", help="include the About section")
    args = ap.parse_args()
    data = core.sync()
    print(report(data, full=args.full))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
