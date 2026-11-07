"""
excel_report.py - build the shareable Excel workbook.

One workbook, one sheet per question:

  Summary       the headline numbers, top weapons, best zones and quit advice
  Matches       every match, with its loadout highlight, zone and quit time
  Weapons       every classified weapon, ranked by shrunk K/D and win rate
  Utility       utility and unclassified loot, so nothing is silently dropped
  Drop Zones    yield, visits, quietness and score for every zone
  Quit Curve    chest velocity by minute and the chests/hour of each exit minute
  Mode Compare  solo vs duos vs squads, including the per-mode quit time
  About         method and honest caveats

Charts are native Excel charts (not images), so they stay editable.

Usage:
    python excel_report.py                 # write Fortnite_Chest_Report.xlsx
    python excel_report.py --out book.xlsx
    python excel_report.py --sync          # refresh from replays first
"""

import argparse
from pathlib import Path

import fc_core as core
import insights
import quit as quit_model
import spatial

try:
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, LineChart, Reference
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except Exception:                                  # pragma: no cover - friendly message
    HAS_OPENPYXL = False

DEFAULT_NAME = "Fortnite_Chest_Report.xlsx"

INK = "FF1F2937"
MUTED = "FF6B7280"
HEADER_FILL = PatternFill("solid", fgColor="FF111C3D")
HEADER_FONT = Font(color="FFFFFFFF", bold=True, size=10)
TITLE_FONT = Font(color="FF111C3D", bold=True, size=18)
SUB_FONT = Font(color="FF6B7280", size=9, italic=True)
SECTION_FONT = Font(color="FF111C3D", bold=True, size=12)
ACCENT_FILL = PatternFill("solid", fgColor="FFFFF4D6")
ZEBRA_FILL = PatternFill("solid", fgColor="FFF7F9FC")
THIN = Side(style="thin", color="FFD9DEE8")
BORDER = Border(bottom=THIN)


def _require():
    if not HAS_OPENPYXL:
        raise SystemExit(
            "openpyxl is required to write the Excel report.\n"
            "Install it with:  python -m pip install openpyxl"
        )


def _title(ws, text, sub=None):
    ws["A1"] = text
    ws["A1"].font = TITLE_FONT
    ws.merge_cells("A1:H1")
    if sub:
        ws["A2"] = sub
        ws["A2"].font = SUB_FONT
        ws.merge_cells("A2:H2")


def _section(ws, row, text):
    ws.cell(row=row, column=1, value=text).font = SECTION_FONT
    return row + 1


def _table(ws, row, headers, rows, widths=None, number_formats=None, autofilter=False):
    """Write a styled table and return the next free row."""
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=row, column=col, value=header)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER
    header_row = row
    row += 1
    for i, values in enumerate(rows):
        for col, value in enumerate(values, 1):
            cell = ws.cell(row=row, column=col, value=value)
            cell.border = BORDER
            if i % 2:
                cell.fill = ZEBRA_FILL
            if number_formats and col in number_formats:
                cell.number_format = number_formats[col]
        row += 1
    if widths:
        for col, width in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(col)].width = width
    if autofilter and rows:
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(len(headers))}{row - 1}"
    return row


def _metric(ws, row, label, value, fmt=None):
    ws.cell(row=row, column=1, value=label).font = Font(color=MUTED, size=10)
    cell = ws.cell(row=row, column=2, value=value)
    cell.font = Font(color=INK, bold=True, size=11)
    if fmt:
        cell.number_format = fmt
    return row + 1


def _sheet_summary(wb, b):
    ws = wb.active
    ws.title = "Summary"
    o = b["overview"]
    _title(ws, "FORTNITE CHEST REPORT",
           f"Generated {o['generated']} from {o['matches']} local replays. "
           "Only chests you opened are counted.")
    row = 4
    row = _section(ws, row, "OVERVIEW")
    row = _metric(ws, row, "Matches", o["matches"])
    row = _metric(ws, row, "Chests", o["chests"], "#,##0")
    row = _metric(ws, row, "Normal / Epic / Sprite",
                  f"{o['normal']:,} / {o['epic']:,} / {o['sprite']:,}")
    row = _metric(ws, row, "Hours looted", round(o["hours"], 1), "0.0")
    row = _metric(ws, row, "Chests per hour", round(o["chests_per_hour"], 1), "0.0")
    row = _metric(ws, row, "Chests per minute (CPM)", round(o["cpm"], 2), "0.00")
    row = _metric(ws, row, "Chests per match", round(o["avg_chests"], 1), "0.0")
    row = _metric(ws, row, "K/D", round(o["kd"], 2), "0.00")
    row = _metric(ws, row, "Wins", f"{o['wins']}  ({o['win_rate'] * 100:.0f}%)")
    if o["avg_placement"]:
        row = _metric(ws, row, "Average placement", round(o["avg_placement"], 1), "0.0")
    row = _metric(ws, row, "Average survival", core.fmt_clock(o["avg_survival"]))
    row = _metric(ws, row, "First chest (median)", core.fmt_clock(o["median_first_chest"]))
    row = _metric(ws, row, "Days played", o["days_played"])
    if o["best_day"]:
        row = _metric(ws, row, "Best day",
                      f"{o['best_day']['day']} - {o['best_day']['chests']} chests")
    row += 1

    row = _section(ws, row, "BEST WEAPONS")
    top = b["weapons"][:5]
    if top:
        row = _table(
            ws, row,
            ["Rank", "Weapon", "Matches", "K/D", "Shrunk K/D", "Win %", "Avg survival", "Score"],
            [[i, r["item"], r["matches"], round(r["kd"], 2), round(r["kd_shrunk"], 2),
              round(r["win_rate"] * 100, 1), core.fmt_clock(r["avg_survival"]),
              round(r["score"], 2)] for i, r in enumerate(top, 1)],
            widths=[7, 32, 9, 8, 11, 8, 13, 8],
            number_formats={4: "0.00", 5: "0.00", 6: "0.0"},
        )
    else:
        ws.cell(row=row, column=1, value="No weapons classified yet.").font = Font(color=MUTED)
        row += 1
    row += 1

    row = _section(ws, row, "BEST DROP ZONES")
    zones = b["best_zones"][:5]
    if zones:
        row = _table(
            ws, row,
            ["Rank", "Zone", "Chests", "Visits", "Per visit", "Quiet %", "Score"],
            [[i, z["name"], z["chests"], z["visits"], z["per_visit"], z["quiet"],
              z["score"]] for i, z in enumerate(zones, 1)],
            widths=[7, 24, 9, 8, 10, 9, 8],
            number_formats={5: "0.0", 6: "0.0", 7: "0.0"},
        )
    else:
        ws.cell(row=row, column=1, value="No chest positions stored yet.").font = Font(color=MUTED)
        row += 1
    row += 1

    row = _section(ws, row, "WHEN TO QUIT")
    r = b["quit"]
    if r["quit_at"]:
        row = _metric(ws, row, "Best exit", f"{r['quit_clock']} of match time")
        row = _metric(ws, row, "Chests/hour at that exit", round(r["per_hour"], 0), "0")
        row = _metric(ws, row, "Chests/hour playing full matches",
                      round(r["full_per_hour"], 0), "0")
        row = _metric(ws, row, "Verdict",
                      f"{r['advice']} ({r['diff']:+.0f} chests/hour)")
        row = _metric(ws, row, "Drop-off (velocity halves)",
                      f"minute {r['knee']}" if r["knee"] else "not clear yet")
    else:
        ws.cell(row=row, column=1, value="Not enough chest timings yet.").font = Font(color=MUTED)
        row += 1

    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A4"
    return ws


def _sheet_matches(wb, b):
    import archive
    ws = wb.create_sheet("Matches")
    _title(ws, "MATCHES", "Newest first. Best weapon and zone are the most prominent in that match.")
    headers = ["When", "Mode", "Chests", "Normal", "Epic", "Sprite", "CPM",
               "Kills", "Deaths", "K/D", "Survival", "Placement", "Win",
               "Best weapon", "Best zone", "Quit at", "Replay"]
    rows = []
    for m in sorted(b["matches"], key=lambda m: m.get("when", ""), reverse=True):
        kd = (m.get("kills", 0) / m["deaths"]) if m.get("deaths") else m.get("kills", 0)
        rows.append([
            m.get("when", ""), m.get("mode", "unknown"), m.get("total", 0),
            m["counts"].get("normal", 0), m["counts"].get("epic", 0),
            m["counts"].get("sprite", 0), m.get("cpm", 0.0),
            m.get("kills", 0), m.get("deaths", 0), round(kd, 2),
            core.fmt_clock(m.get("alive", 0)), m.get("placement") or "",
            "yes" if m.get("placement") == 1 else "",
            archive.best_weapon(m),
            spatial.best_zone_for(m, b["zones_raw"]) if b["zones_raw"] else "-",
            quit_model.quit_time_for(m, b["quit_model"]), m.get("name", ""),
        ])
    _table(ws, 4, headers, rows,
           widths=[17, 9, 8, 8, 7, 7, 7, 7, 7, 7, 10, 10, 6, 30, 10, 9, 42],
           number_formats={7: "0.00", 10: "0.00"},
           autofilter=True)
    ws.freeze_panes = "A5"
    ws.sheet_view.showGridLines = False
    return ws


def _item_rows(rows):
    return [[r["item"], r["matches"], r["kills"], r["deaths"], round(r["kd"], 2),
             round(r["kd_shrunk"], 2), round(r["lift"], 2),
             round(r["win_rate"] * 100, 1), round(r["top5_rate"] * 100, 1),
             round(r["avg_placement"], 1) if r["avg_placement"] else "",
             core.fmt_clock(r["avg_survival"]), round(r["avg_chests"], 1),
             round(r["avg_cpm"], 2), round(r["score"], 2)] for r in rows]


ITEM_HEADERS = ["Item", "Matches", "Kills", "Deaths", "K/D", "Shrunk K/D", "Lift",
                "Win %", "Top 5 %", "Avg place", "Avg survival", "Chests/match",
                "CPM", "Score"]
ITEM_WIDTHS = [32, 9, 7, 7, 7, 11, 7, 8, 8, 9, 13, 12, 7, 8]


def _sheet_weapons(wb, b):
    ws = wb.create_sheet("Weapons")
    _title(ws, "WEAPONS",
           "Ranked by success in the matches where you looted them. "
           "Shrunk K/D pulls small samples towards your overall K/D.")
    row = _table(ws, 4, ITEM_HEADERS, _item_rows(b["weapons"]),
                 widths=ITEM_WIDTHS,
                 number_formats={5: "0.00", 6: "0.00", 7: "+0.00;-0.00", 8: "0.0",
                                 9: "0.0", 10: "0.0", 12: "0.0", 13: "0.00", 14: "0.00"},
                 autofilter=True)
    if b["weapons"]:
        chart = BarChart()
        chart.type = "bar"
        chart.title = "Top weapons by score"
        chart.y_axis.title = "Score"
        data = Reference(ws, min_col=14, min_row=4, max_row=4 + min(10, len(b["weapons"])))
        cats = Reference(ws, min_col=1, min_row=5, max_row=4 + min(10, len(b["weapons"])))
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        chart.height = 9
        chart.width = 18
        ws.add_chart(chart, "P4")
    ws.freeze_panes = "A5"
    ws.sheet_view.showGridLines = False
    return ws


def _sheet_utility(wb, b):
    ws = wb.create_sheet("Utility")
    _title(ws, "UTILITY & OTHER LOOT",
           "Utility items and anything the name-based classifier could not place. "
           "They never enter the weapon ranking.")
    rows = []
    for r in b["utility"]:
        rows.append(["utility"] + _item_rows([r])[0])
    for r in b["other"]:
        rows.append(["other"] + _item_rows([r])[0])
    _table(ws, 4, ["Type"] + ITEM_HEADERS, rows,
           widths=[10] + ITEM_WIDTHS,
           number_formats={6: "0.00", 7: "0.00", 8: "+0.00;-0.00", 9: "0.0",
                           10: "0.0", 11: "0.0", 13: "0.0", 14: "0.00", 15: "0.00"},
           autofilter=True)
    ws.freeze_panes = "A5"
    ws.sheet_view.showGridLines = False
    return ws


def _sheet_zones(wb, b):
    ws = wb.create_sheet("Drop Zones")
    _title(ws, "DROP ZONES",
           "Zones are coordinate clusters (~120 m), not named POIs. "
           "Quiet % is the share of chests in the area that you opened.")
    headers = ["Zone", "Where", "X", "Y", "Chests", "Visits", "Per visit", "Other players",
               "Quiet %", "Score", "Best run", "Run CPM", "Best match"]
    rows = [[z["zone"], z["compass"], z["x"], z["y"], z["chests"], z["visits"],
             z["per_visit"], z["other"], z["quiet"], z["score"], z["best_run"],
             z["run_cpm"], z["best_when"]] for z in b["zones"]]
    _table(ws, 4, headers, rows,
           widths=[8, 14, 10, 10, 8, 8, 10, 13, 9, 8, 10, 9, 17],
           number_formats={7: "0.0", 9: "0.0", 10: "0.0", 12: "0.0"},
           autofilter=True)
    if b["zones"]:
        chart = BarChart()
        chart.type = "bar"
        chart.title = "Top zones by chests per visit"
        chart.y_axis.title = "Chests per visit"
        data = Reference(ws, min_col=7, min_row=4, max_row=4 + min(10, len(b["zones"])))
        cats = Reference(ws, min_col=1, min_row=5, max_row=4 + min(10, len(b["zones"])))
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        chart.height = 9
        chart.width = 18
        ws.add_chart(chart, "O4")
    ws.freeze_panes = "A5"
    ws.sheet_view.showGridLines = False
    return ws


def _sheet_quit(wb, b):
    ws = wb.create_sheet("Quit Curve")
    _title(ws, "QUIT CURVE",
           "Chests per minute of match time, and the chests/hour of repeating a match "
           "that exits at each minute (queue time included).")
    model = b["quit_model"]
    if not model:
        ws.cell(row=4, column=1, value="Not enough chest timings yet.").font = Font(color=MUTED)
        return ws
    by_min = {row["minute"]: row for row in model["table"]}
    rows = []
    for row in model["curve"]:
        q = by_min.get(row["minute"], {})
        rows.append([row["minute"], core.fmt_clock((row["minute"] - 1) * 60),
                     row["chests"], row["alive"], round(row["rate"], 3),
                     round(row["smooth"], 3), round(q.get("chests", 0), 1),
                     round(q.get("per_hour", 0), 1)])
    row = _table(ws, 4,
                 ["Minute", "Clock", "Chests that minute", "Matches alive", "Rate",
                  "Smoothed rate", "Chests by then", "Chests/hour if repeated"],
                 rows,
                 widths=[8, 9, 17, 13, 9, 13, 14, 20],
                 number_formats={5: "0.00", 6: "0.00", 7: "0.0", 8: "0.0"})
    chart = LineChart()
    chart.title = "Chest velocity by minute"
    chart.y_axis.title = "Chests per minute"
    chart.x_axis.title = "Minute"
    data = Reference(ws, min_col=6, min_row=4, max_row=4 + len(rows))
    cats = Reference(ws, min_col=1, min_row=5, max_row=4 + len(rows))
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    chart.height = 9
    chart.width = 20
    ws.add_chart(chart, "J4")
    ws.freeze_panes = "A5"
    ws.sheet_view.showGridLines = False
    return ws


def _sheet_modes(wb, b):
    ws = wb.create_sheet("Mode Compare")
    _title(ws, "MODE COMPARISON",
           "Mine/hour is the only number that feeds your token odds. "
           "Squad/hour is the whole team's loot.")
    headers = ["Mode", "Matches", "Hours", "Mine/hour", "Squad/hour", "K/D", "Win %",
               "Avg place", "Survival", "Quit at", "Chests/hour", "Full-match/hour"]
    rows = []
    for name in ("solo", "duos", "squads", "unknown"):
        s = b["modes"].get(name)
        if not s:
            continue
        qm = b["quit_by_mode"].get(name)
        qr = quit_model.recommendation(qm) if qm else {}
        win = (s["wins"] / s["matches"] * 100) if s["matches"] else 0
        rows.append([name, s["matches"], round(s["hours"], 1),
                     round(s["mine_per_hour"], 1), round(s["squad_per_hour"], 1),
                     round(s["kd"], 2), round(win, 1),
                     round(s["avg_place"], 1) if s["avg_place"] else "",
                     core.fmt_clock(s["survival"]),
                     qr.get("quit_clock", "-"), round(qr.get("per_hour", 0), 0),
                     round(qr.get("full_per_hour", 0), 0)])
    _table(ws, 4, headers, rows,
           widths=[10, 9, 8, 11, 12, 7, 8, 10, 10, 9, 12, 17],
           number_formats={3: "0.0", 4: "0.0", 5: "0.0", 6: "0.00", 7: "0.0",
                           8: "0.0", 11: "0", 12: "0"},
           autofilter=True)
    ws.freeze_panes = "A5"
    ws.sheet_view.showGridLines = False
    return ws


def _sheet_daily(wb, b):
    ws = wb.create_sheet("Daily")
    _title(ws, "DAILY HISTORY", "One row per day with at least one counted match.")
    rows = [[d["day"], d["matches"], d["chests"], round(d["cpm"], 2),
             d["kills"], d["deaths"], d["wins"]] for d in b["daily"]]
    _table(ws, 4, ["Day", "Matches", "Chests", "CPM", "Kills", "Deaths", "Wins"],
           rows, widths=[13, 9, 9, 8, 8, 8, 7],
           number_formats={4: "0.00"}, autofilter=True)
    if rows:
        chart = BarChart()
        chart.title = "Chests per day"
        chart.y_axis.title = "Chests"
        data = Reference(ws, min_col=3, min_row=4, max_row=4 + len(rows))
        cats = Reference(ws, min_col=1, min_row=5, max_row=4 + len(rows))
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        chart.height = 8
        chart.width = 20
        ws.add_chart(chart, "I4")
    ws.freeze_panes = "A5"
    ws.sheet_view.showGridLines = False
    return ws


def _sheet_about(wb, b):
    ws = wb.create_sheet("About")
    _title(ws, "ABOUT THESE NUMBERS",
           "What the data can and cannot tell you.")
    lines = [
        ("Source", "The .replay files Fortnite writes locally. No game memory, no injection."),
        ("Chests", "Credited only when the replay says YOU opened the chest. Teammates' "
                   "chests and spectated chests are excluded."),
        ("Kills / deaths", "From replicated player state and the kill feed."),
        ("Survival", "The last moment the replay saw your pawn."),
        ("Weapon success", "Measured across matches where you looted the item, because a "
                           "modern replay does not record which weapon fired which shot."),
        ("Shrunk K/D", "A few matches of your overall K/D are added as prior evidence, so a "
                       "lucky two-match item cannot top the table."),
        ("Drop zones", "Coordinate clusters (~120 m). Replays carry positions but no POI names. "
                       "Quiet % is the share of chests in the area that you opened."),
        ("Quit time", "A chests-per-hour model only. Leaving early costs placement, XP, quest "
                      "progress and, in squads, your teammates' game."),
        ("Privacy", "This workbook contains only aggregate match statistics. It does not "
                    "contain your account name, raw replays or data.json."),
        ("Generated", b["overview"]["generated"]),
    ]
    row = 4
    for label, text in lines:
        ws.cell(row=row, column=1, value=label).font = Font(bold=True, color=INK, size=10)
        cell = ws.cell(row=row, column=2, value=text)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[row].height = 30
        row += 1
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 100
    ws.sheet_view.showGridLines = False
    return ws


def build(data=None, path=None):
    """Write the workbook and return its path."""
    _require()
    data = data if data is not None else core.load_data()
    b = insights.build(data)
    wb = Workbook()
    _sheet_summary(wb, b)
    _sheet_matches(wb, b)
    _sheet_weapons(wb, b)
    _sheet_utility(wb, b)
    _sheet_zones(wb, b)
    _sheet_quit(wb, b)
    _sheet_modes(wb, b)
    _sheet_daily(wb, b)
    _sheet_about(wb, b)
    wb.properties.title = "Fortnite Chest Report"
    wb.properties.creator = "Fortnite Grind & Combat Efficiency Suite"
    out = Path(path) if path else core.BASE / DEFAULT_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


def main():
    ap = argparse.ArgumentParser(description="Build the Excel report.")
    ap.add_argument("--out", help=f"output path (default: {DEFAULT_NAME})")
    ap.add_argument("--sync", action="store_true", help="read new replays first")
    args = ap.parse_args()
    data = core.sync() if args.sync else core.load_data()
    out = build(data, args.out)
    print(f"Excel report written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
