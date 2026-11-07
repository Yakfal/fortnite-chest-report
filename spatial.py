"""
spatial.py - Spatial Drop & Pathing Analyzer.

Every chest in a replay carries the map position (X, Y in Unreal world units, where
100 units = 1 metre) and the second it was opened, for the player who opened it. That is
enough to answer three questions:

  * where do I actually loot?          -> clusters of the chests I opened
  * which of those places are quiet?   -> how many chests OTHER players opened nearby
  * what is the fastest route through? -> the order I opened them, in my best run

"Uncontested" is measured, not guessed: for each zone the module counts the chests other
players opened inside the same radius. A zone where you take 7 chests a visit and the lobby
takes 1 is genuinely quiet; a zone where you take 7 and the lobby takes 20 is a hot drop.

A replay does not contain POI names, so zones are coordinate clusters with a stable label.
They line up with POIs on the map (60-80 m clusters), and the same spot keeps its label.

Usage:
    python spatial.py              # top 3 zones + paths
    python spatial.py --map        # plus a rough ASCII density map
    python spatial.py --radius 8000
"""

import argparse
import math
from collections import defaultdict

import fc_core as core

RADIUS = 12000         # 120 m: roughly one POI's loot cluster (chests at a POI span ~150 m)
MIN_VISITS = 2         # a zone needs to be seen more than once to be recommended


def zone_label(x, y):
    """Stable, human-sortable label for a map cell (~130 m across)."""
    col = int((x + 130000) // 13000)
    row = int((y + 130000) // 13000)
    return f"{chr(ord('A') + col % 26)}{row}"


def compass(x, y):
    """Which side of the island a point is on, in plain words."""
    if abs(x) < 20000 and abs(y) < 20000:
        return "centre"
    ns = "north" if y >= 0 else "south"
    ew = "east" if x >= 0 else "west"
    if abs(x) < 20000:
        return ns
    if abs(y) < 20000:
        return ew
    return f"{ns}-{ew}"


def zone_name(x, y):
    """'G11 (north-west)' - a label a human can say out loud."""
    return f"{zone_label(x, y)} ({compass(x, y)})"


def cluster(points, radius):
    """
    Group points into zones. Greedy, order-independent enough for a few hundred chests:
    a point joins the nearest existing zone whose members are within `radius` of it.
    Returns a list of dicts with centre and members.
    """
    zones = []
    for x, y, *rest in points:
        best, best_d = None, radius
        for z in zones:
            d = math.dist((x, y), z["centre"])
            if d <= best_d:
                best, best_d = z, d
        if best is None:
            zones.append({"centre": (x, y), "members": [(x, y, *rest)]})
        else:
            best["members"].append((x, y, *rest))
            n = len(best["members"])
            best["centre"] = (
                best["centre"][0] + (x - best["centre"][0]) / n,
                best["centre"][1] + (y - best["centre"][1]) / n,
            )
    return zones


def merge_zones(zones, radius):
    """
    Greedy clustering depends on the order points arrive, which leaves overlapping zones
    for one building. Merge any two zones whose centres are within the radius until fixed.
    """
    merged = True
    while merged:
        merged = False
        for i in range(len(zones)):
            for j in range(i + 1, len(zones)):
                if math.dist(zones[i]["centre"], zones[j]["centre"]) <= radius:
                    a, b = zones[i], zones[j]
                    members = a["members"] + b["members"]
                    cx = sum(p[0] for p in members) / len(members)
                    cy = sum(p[1] for p in members) / len(members)
                    zones[i] = {"centre": (cx, cy), "members": members}
                    del zones[j]
                    merged = True
                    break
            if merged:
                break
    return zones


def collect(matches):
    """Pull my chest positions per match and everyone else's, tagged with match info."""
    mine, others = [], []
    for idx, m in enumerate(matches):
        for x, y, t in m.get("chest_pos") or []:
            mine.append((x, y, t, idx))
        for x, y in m.get("other_pos") or []:
            others.append((x, y, idx))
    return mine, others


def analyse(data=None, radius=RADIUS):
    """Build zone statistics. Returns (zones, matches) where zones are sorted by score."""
    data = data if data is not None else core.load_data()
    ms = core.matches(data)
    mine, others = collect(ms)
    if not mine:
        return [], ms

    zones = cluster([(p[0], p[1], p[2], p[3]) for p in mine], radius)
    zones = merge_zones(zones, radius)

    for z in zones:
        cx, cy = z["centre"]
        z["mine"] = len(z["members"])
        z["matches_seen"] = len({p[3] for p in z["members"]})
        z["visits"] = z["matches_seen"]
        z["per_visit"] = z["mine"] / max(1, z["visits"])
        # Chests other players opened inside this zone - the contention signal.
        z["other"] = sum(1 for x, y, _ in others if math.dist((x, y), (cx, cy)) <= radius)
        total_here = z["mine"] + z["other"]
        z["contested"] = (z["other"] / total_here) if total_here else 0.0
        # How much of the area's loot is mine, weighted by how much I get per visit.
        z["score"] = z["per_visit"] * (1.0 - z["contested"])
        z["label"] = zone_label(int(cx), int(cy))
        z["compass"] = compass(int(cx), int(cy))
        z["name"] = zone_name(int(cx), int(cy))
        z["coords"] = f"{cx:,.0f}, {cy:,.0f}"

        # Best run through the zone: the match where I took the most chests here.
        per_match = defaultdict(list)
        for x, y, t, idx in z["members"]:
            per_match[idx].append((t, x, y))
        idx, run = max(per_match.items(), key=lambda kv: len(kv[1]))
        run.sort()
        z["best_match"] = ms[idx]
        z["best_when"] = ms[idx].get("when", "")
        z["best_run"] = run
        if len(run) > 1:
            span = max(45, run[-1][0] - run[0][0])
            z["run_cpm"] = (len(run) - 1) / (span / 60)
        else:
            z["run_cpm"] = 0.0

    zones.sort(key=lambda z: (z["score"], z["mine"]), reverse=True)
    # Two clusters can still round to the same 130 m label; keep the labels unique.
    seen = {}
    for z in zones:
        base = z["label"]
        seen[base] = seen.get(base, 0) + 1
        if seen[base] > 1:
            z["label"] = f"{base}-{seen[base]}"
        z["name"] = f"{z['label']} ({z['compass']})"
    return zones, ms


def zone_rows(zones, limit=None, min_visits=None):
    """Clean rows for the HUD, insights and Excel (no match objects or member lists)."""
    rows = []
    for z in zones:
        if min_visits is not None and z["visits"] < min_visits:
            continue
        rows.append({
            "zone": z["label"],
            "name": z["name"],
            "compass": z["compass"],
            "x": round(z["centre"][0]),
            "y": round(z["centre"][1]),
            "coords": z["coords"],
            "chests": z["mine"],
            "visits": z["visits"],
            "per_visit": round(z["per_visit"], 2),
            "other": z["other"],
            "quiet": round((1.0 - z["contested"]) * 100, 1),
            "score": round(z["score"], 2),
            "best_when": z.get("best_when", ""),
            "best_run": len(z.get("best_run") or []),
            "run_cpm": round(z.get("run_cpm", 0.0), 2),
        })
        if limit and len(rows) >= limit:
            break
    return rows


def best_zones(data=None, limit=3, min_visits=MIN_VISITS):
    """Top zones as clean rows, for the summary cards and Excel."""
    zones, _ = analyse(data)
    rows = zone_rows(zones, min_visits=min_visits)
    if not rows:
        rows = zone_rows(zones)
    return rows[:limit]


def path_steps(run, step=2000):
    """Collapse a chest-by-chest run into waypoints a human can follow."""
    steps = []
    for t, x, y in run:
        if not steps or math.dist((x, y), (steps[-1][1], steps[-1][2])) > step:
            steps.append((t, x, y))
    if len(steps) > 1 and (steps[-1][1], steps[-1][2]) != (run[-1][1], run[-1][2]):
        steps.append(run[-1])
    return steps


def ascii_map(mine, zones, width=46, height=20):
    """A rough density map: how much of my looting happens in each area."""
    if not mine:
        return ""
    xs = [p[0] for p in mine] or [0]
    ys = [p[1] for p in mine] or [0]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    sx = max(1, (x1 - x0) / (width - 1))
    sy = max(1, (y1 - y0) / (height - 1))
    grid = [[0] * width for _ in range(height)]
    for x, y, *_ in mine:
        grid[min(height - 1, int((y - y0) / sy))][min(width - 1, int((x - x0) / sx))] += 1
    top = zones[:3]
    letters = {z["label"]: "123"[i] for i, z in enumerate(top)}
    for i, z in enumerate(top):
        gx = min(width - 1, int((z["centre"][0] - x0) / sx))
        gy = min(height - 1, int((z["centre"][1] - y0) / sy))
        grid[gy][gx] = letters[z["label"]]
    shade = " .:-=+*#%@"
    out = ["  rough density map (bottom = low Y, # = most chests, 1-3 = top zones)"]
    for row in reversed(grid):
        out.append("  " + "".join(c if isinstance(c, str) else
                                  (shade[min(len(shade) - 1, c)] if c else " ")
                                  for c in row))
    return "\n".join(out)


def report(data=None, radius=RADIUS, min_visits=MIN_VISITS, show_map=False):
    zones, ms = analyse(data, radius=radius)
    out = ["SPATIAL DROP & PATHING", "=" * 60, ""]
    if not zones:
        return "\n".join(out) + "  No chest positions stored yet - play a match first.\n"

    mine_count = sum(z["mine"] for z in zones)
    out.append(f"{mine_count} of my chests grouped into {len(zones)} zones "
               f"(clusters of ~{radius / 100:.0f} m)")
    reliable = [z for z in zones if z["visits"] >= min_visits]
    if not reliable:
        out.append(f"  No zone visited {min_visits}+ times yet; showing everything.")
        reliable = zones

    out.append("")
    out.append("TOP 3 UNCONTESTED HIGH-YIELD ZONES")
    for i, z in enumerate(reliable[:3], 1):
        when = z["best_match"].get("when", "")[:16]
        out.append("")
        out.append(f" {i}. ZONE {z['name']}   around ({z['centre'][0]:,.0f}, {z['centre'][1]:,.0f})")
        out.append(f"    {z['mine']} chests in {z['visits']} visits "
                   f"({z['per_visit']:.1f} per visit)   score {z['score']:.1f}")
        out.append(f"    contested: {z['contested'] * 100:.0f}% of the chests here were "
                   f"opened by other players ({z['other']})")
        out.append(f"    best run {when}: {len(z['best_run'])} chests, "
                   f"{z['run_cpm']:.1f} chests/min once inside")
        steps = path_steps(z["best_run"])
        if len(steps) > 1:
            out.append("    path (in opening order, 100 units = 1 m):")
            for j, (t, x, y) in enumerate(steps[:6], 1):
                out.append(f"      {j}. ({x:,}, {y:,}){' - land here' if j == 1 else ''}"
                           f"   at {core.fmt_clock(t)}")
            if len(steps) > 6:
                out.append(f"      ... {len(steps) - 6} more waypoints")

    out.append("")
    out.append("ALL ZONES (by yield x quietness)")
    out.append(f"  {'ZONE':5s} {'CHESTS':>6s} {'VISITS':>6s} {'/VISIT':>6s} "
               f"{'OTHER':>6s} {'QUIET':>6s}")
    for z in zones[:12]:
        out.append(f"  {z['label']:5s} {z['mine']:6d} {z['visits']:6d} {z['per_visit']:6.1f} "
                   f"{z['other']:6d} {(1 - z['contested']) * 100:5.0f}%")

    if show_map:
        mine, _ = collect(ms)
        out.append("")
        out.append(ascii_map(mine, zones))

    out.append("")
    out.append("HOW TO READ THIS")
    out.append("  A zone is a cluster of chest positions, not a named POI - replays do not")
    out.append("  carry location names, but ~60 m clusters sit on one POI or building group.")
    out.append("  'Other' counts chests the rest of the lobby opened in that same area, which")
    out.append("  is the honest measure of how contested a drop is. A high score means you")
    out.append("  take a lot per visit without meeting many people doing the same.")
    return "\n".join(out) + "\n"


def best_zone_for(match, zones, radius=RADIUS):
    """Label of the zone holding most of this match's chests (for stats.csv), or '-'."""
    pts = match.get("chest_pos") or []
    if not pts or not zones:
        return "-"
    counts = defaultdict(int)
    for x, y, _ in pts:
        best, best_d = None, radius
        for z in zones:
            d = math.dist((x, y), z["centre"])
            if d <= best_d:
                best, best_d = z, d
        if best is not None:
            counts[best["label"]] += 1
    if not counts:
        return "-"
    return max(counts.items(), key=lambda kv: kv[1])[0]


def main():
    ap = argparse.ArgumentParser(description="Drop zone and looting path analysis.")
    ap.add_argument("--radius", type=int, default=RADIUS,
                    help="cluster radius in world units, 100 per metre (default 6000)")
    ap.add_argument("--min-visits", type=int, default=MIN_VISITS,
                    help="visits needed before a zone is recommended (default 2)")
    ap.add_argument("--map", action="store_true", help="show an ASCII density map")
    args = ap.parse_args()
    data = core.sync()
    print(report(data, radius=args.radius, min_visits=args.min_visits, show_map=args.map))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
