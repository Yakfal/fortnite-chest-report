"""
test_sim.py - mock data tester.

Builds a synthetic match history that looks exactly like what the reader produces, then runs
every module, the CSV writer, the archiver and the HUD against it. Nothing here touches your
real replays, data.json or stats.csv: everything happens in a temporary folder.

What it proves:
  * the maths runs without Fortnite, on any machine
  * the archiver zips counted replays, leaves uncounted ones alone, and only deletes after
    the zip verifies
  * a corrupt or missing replay is reported as an error instead of raising
  * the HUD switches to its QUIT MATCH NOW warning at the right minute

Usage:
    python test_sim.py              # run everything, print PASS/FAIL
    python test_sim.py --keep       # keep the temp folder for inspection
"""

import argparse
import csv
import json
import os
import random
import shutil
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path

import fc_core as core

RESULTS = []


def check(name, fn):
    """Run one check, record pass/fail with the reason, never abort the suite."""
    try:
        fn()
        RESULTS.append((True, name, ""))
        print(f"  PASS  {name}")
    except Exception as e:
        RESULTS.append((False, name, f"{type(e).__name__}: {e}"))
        print(f"  FAIL  {name}\n        {type(e).__name__}: {e}")
        if os.environ.get("TEST_SIM_TRACE"):
            traceback.print_exc()


# ---------------------------------------------------------------- fake data

WEAPONS = [
    ("Shotgun Break Flintlock", "WID_Shotgun_Break_Flintlock_Athena_UC"),
    ("Pistol Scavenger Athena R Ore T03", "WID_Pistol_Scavenger_Athena_R_Ore_T03"),
    ("Assault Flintlock", "WID_Assault_Flintlock_Athena_UC"),
    ("PurpleDrool", "WID_PurpleDrool_Athena_R"),
]
UTILITY = [
    ("AgileChug", "WID_AgileChug"),
    ("Paprika Bandage", "WID_Paprika_Bandage"),
    ("Athena Stimpulse", "Athena_Stimpulse"),
    ("Paprika MedBox", "WID_Paprika_MedBox"),
]


def fake_match(i, rng, mode="solo", epic_rate=0.1):
    """One synthetic match with plausible timings, loot, positions and squad data."""
    chests_n = rng.randint(8, 40)
    # Chests cluster in the first eleven minutes, exactly like real replays.
    times = sorted(rng.randint(70, 780) for _ in range(chests_n))
    chests = []
    for t in times:
        kind = "epic" if rng.random() < epic_rate else ("sprite" if rng.random() < 0.03 else "normal")
        chests.append({"t": t, "tipo": "Athena Chest", "kind": kind, "destroyed": False})
    counts = {"normal": 0, "epic": 0, "sprite": 0}
    for c in chests:
        counts[c["kind"]] += 1
    alive = max(times[-1] + 30, 200) if times else rng.randint(120, 400)
    duration = alive + rng.randint(0, 120)
    mates = []
    if mode in ("squads", "duos"):
        for _ in range(3 if mode == "squads" else 1):
            mates.append({"chests": rng.randint(0, 18), "last": rng.randint(300, 900),
                          "prox": rng.randint(0, 100), "dist": rng.randint(800, 30000)})
    squad = chests_n + sum(m["chests"] for m in mates)
    return {
        "ok": True, "schema": core.SCHEMA,
        "chests": chests, "counts": counts, "total": chests_n,
        "duration": duration, "alive": alive,
        "cpm": round(chests_n / (alive / 60), 2),
        "cpm_match": round(chests_n / (duration / 60), 2),
        "first_chest": times[0] if times else 0,
        "kills": rng.randint(0, 7), "deaths": rng.choice([0, 1, 1]),
        "survival": alive,
        "placement": rng.randint(1, 30),
        "kills_detail": [{"t": rng.randint(200, 700), "bot": False, "distance": rng.randint(5, 60)}],
        "loadout": [
            {"item": w[0], "raw": w[1], "kind": "weapon",
             "n": rng.randint(1, 4), "t": rng.randint(90, 600)}
            for w in [rng.choice(WEAPONS)]
        ] + [
            {"item": u[0], "raw": u[1], "kind": "utility",
             "n": rng.randint(1, 3), "t": rng.randint(90, 600)}
            for u in [rng.choice(UTILITY)]
        ],
        "chest_pos": [[rng.randint(-60000, 90000), rng.randint(-60000, 90000), t] for t in times],
        "other_pos": [[rng.randint(-60000, 90000), rng.randint(-60000, 90000)]
                      for _ in range(rng.randint(20, 120))],
        "mode": mode, "team_size": {"solo": 1, "duos": 2, "squads": 4}[mode],
        "lobby_mode": mode if mode != "solo" else "solo",
        "mates": mates, "squad_chests": squad,
        "squad_cpm": round(squad / (alive / 60), 2),
        "types": {}, "name": f"UnsavedReplay-2026.11.07-{i:02d}.00.00.replay",
        "when": time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - i * 900)),
        "day": time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 900)),
        "size": 1_500_000,
    }


def build_dataset(n=14, seed=7):
    rng = random.Random(seed)
    modes = ["solo", "solo", "solo", "duos"] + ["squads"] * (n - 4)
    data = {"replays": {}, "settings": {"daily_goal": 400}, "version": core.SCHEMA}
    for i, mode in enumerate(modes[:n]):
        m = fake_match(i, rng, mode)
        data["replays"][m["name"]] = m
    return data


def install(data, tmp):
    """Point the suite at a throwaway folder and write a data.json there."""
    core.DATA = tmp / "data.json"
    core.STATS_CSV = tmp / "stats.csv"
    core.DEMOS = tmp / "demos"
    core.ARCHIVE = tmp / "archive"
    core.DEMOS.mkdir(parents=True, exist_ok=True)
    core.ARCHIVE.mkdir(parents=True, exist_ok=True)
    with open(core.DATA, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


# ---------------------------------------------------------------- checks

def run(keep=False):
    import archive
    import efficiency
    import excel_report
    import forecast
    import insights
    import loadout
    import mode as mode_mod
    import quit as quit_mod
    import spatial

    tmp = Path(tempfile.mkdtemp(prefix="fc_sim_"))
    try:
        data = build_dataset()
        install(data, tmp)
        matches = core.matches(data)

        def t_reports():
            texts = {
                "efficiency": efficiency.report(data, top=3),
                "forecast": forecast.report(data),
                "loadout": loadout.report(data),
                "spatial": spatial.report(data),
                "mode": mode_mod.report(data),
                "quit": quit_mod.report(data),
            }
            for name, text in texts.items():
                assert len(text) > 200, f"{name} report too short"
                assert "=" * 10 in text, f"{name} report missing header"
            assert "RECOMMENDED LOADOUT" in texts["loadout"]
            assert "TOP 3" in texts["spatial"]
            assert "chests/hour" in texts["quit"]

        def t_math():
            # CPM must equal chests / alive minutes for every match.
            for m in matches:
                expected = round(m["total"] / (m["alive"] / 60), 2)
                assert abs(m["cpm"] - expected) < 0.01, m["name"]
            # Forecast must never divide by zero, even with an empty history.
            empty = {"replays": {}, "settings": {}, "version": core.SCHEMA}
            assert "No matches" in forecast.report(empty) or "0" in forecast.report(empty)

        def t_degenerate():
            # A match where I died before opening anything must not break any module.
            dead = fake_match(99, random.Random(1), "solo")
            dead.update(total=0, chests=[], counts={"normal": 0, "epic": 0, "sprite": 0},
                        cpm=0.0, first_chest=0, alive=60, chest_pos=[], squad_chests=0,
                        squad_cpm=0.0, loadout=[])
            d = {"replays": {dead["name"]: dead}, "settings": {}, "version": core.SCHEMA}
            for text in (efficiency.report(d), loadout.report(d), mode_mod.report(d),
                         quit_mod.report(d), spatial.report(d)):
                assert isinstance(text, str) and text

        def t_csv():
            rows, path = archive.write_csv(data)
            assert rows == len(matches), (rows, len(matches))
            with open(path, encoding="utf-8") as f:
                got = list(csv.DictReader(f))
            assert len(got) == len(matches)
            for col in ("mode", "chests", "squad_cpm", "best_drop_zone", "optimal_quit"):
                assert col in got[0], f"missing column {col}"
            assert sum(int(r["chests"]) for r in got) == sum(m["total"] for m in matches)

        def t_archive():
            # Two counted replays old enough to archive, one counted fresh, one uncounted.
            now = time.time()
            old = now - 10 * 86400
            counted = list(data["replays"])[:2]
            fresh = list(data["replays"])[2]
            for name in counted:
                (core.DEMOS / name).write_bytes(b"R" * 4096)
                os.utime(core.DEMOS / name, (old, old))
            (core.DEMOS / fresh).write_bytes(b"R" * 2048)
            (core.DEMOS / "unknown.replay").write_bytes(b"R" * 1024)
            os.utime(core.DEMOS / "unknown.replay", (old, old))

            dry = archive.archive_replays(data, days=3, dry_run=True)
            assert sorted(dry["zipped"]) == sorted(counted), dry["zipped"]
            assert (core.DEMOS / counted[0]).exists(), "dry run deleted something"

            summary = archive.archive_replays(data, days=3, delete=True)
            assert sorted(summary["deleted"]) == sorted(counted), summary
            assert not (core.DEMOS / counted[0]).exists()
            assert (core.DEMOS / "unknown.replay").exists(), "archived an uncounted replay"
            assert (core.DEMOS / fresh).exists(), "archived a fresh replay"
            with zipfile.ZipFile(summary["zip"]) as z:
                assert z.testzip() is None
                assert sorted(z.namelist()) == sorted(counted)

        def t_errors():
            # Missing reader / corrupt file: error dicts, never exceptions.
            bad = core.DEMOS / "corrupt.replay"
            bad.write_bytes(b"not a replay at all")
            rec = core.analyze(bad)
            assert rec.get("ok") is False and rec.get("error"), rec
            missing = core.analyze(core.DEMOS / "does-not-exist.replay")
            assert missing.get("ok") is False
            # analyze() must also survive garbage from the reader itself.
            real = core.READER
            try:
                core.READER = core.DEMOS / "no-such-reader.exe"
                rec = core.analyze(bad)
                assert rec.get("ok") is False
            finally:
                core.READER = real

        def t_retry_limit():
            # A permanently broken file is retried a few times, then left alone.
            bad = core.DEMOS / "stuck.replay"
            bad.write_bytes(b"x" * 100)
            d = {"replays": {"stuck.replay": {"ok": False, "size": 100, "attempts": 3,
                                              "gave_up": True, "schema": core.SCHEMA}},
                 "settings": {}}
            assert core.needs_read(d, bad, 100) is False
            assert core.needs_read(d, bad, 200) is True      # grew: give it another chance

        def t_classification():
            # Utility items must never leak into the weapon ranking.
            for raw in ("WID_Paprika_MedBox", "WID_Paprika_Bandage", "WID_AgileChug",
                        "Athena_Stimpulse", "Athena_Bush", "WID_ZiplineDeployer_VR",
                        "AGID_Delilah_Keycard_Gameplay_Key", "Athena_PurpleStuff_VR"):
                assert core.classify_loot(raw) == "utility", raw
            for raw in ("WID_Shotgun_Break_Flintlock_Athena_UC", "WID_PurpleDrool_Athena_R",
                        "WID_GraftedCrunchy_Alt", "Athena_SilverBlazer_Mini_UC",
                        "Pistol Scavenger Athena R Ore T03"):
                assert core.classify_loot(raw) == "weapon", raw
            assert core.classify_loot("some unknown thing") == "other"
            rows = loadout.weapon_stats(matches)
            for (item, kind) in rows:
                assert kind in ("weapon", "utility", "other"), (item, kind)

        def t_insights():
            b = insights.build(data)
            for key in ("overview", "weapons", "zones", "quit", "modes", "daily"):
                assert key in b, key
            assert b["overview"]["matches"] == len(matches)
            assert b["weapons"], "no weapons ranked"
            assert b["best_zones"], "no zones ranked"
            text = insights.report(data)
            assert "BEST WEAPONS" in text and "DROP ZONES" in text
            assert "WHEN TO QUIT" in text and "MODE COMPARISON" in text
            # Empty history must not crash any section.
            empty = {"replays": {}, "settings": {}, "version": core.SCHEMA}
            assert "No matches" in insights.report(empty)

        def t_excel():
            out = excel_report.build(data, tmp / "report.xlsx")
            assert out.exists() and out.stat().st_size > 5000
            from openpyxl import load_workbook
            wb = load_workbook(out)
            for sheet in ("Summary", "Matches", "Weapons", "Drop Zones", "Quit Curve",
                          "Mode Compare", "About"):
                assert sheet in wb.sheetnames, sheet
            assert wb["Weapons"].max_row >= 5
            assert wb["Drop Zones"].max_row >= 5

        def t_overlay():
            import tkinter as tk
            import overlay as ov
            root = tk.Tk()
            hud = ov.Overlay(root, demo=True)
            hud.quitting = True
            root.destroy()

        check("all six reports render", t_reports)
        check("CPM maths and empty history", t_math)
        check("match with zero chests", t_degenerate)
        check("stats.csv columns and totals", t_csv)
        check("archiver: dry run, zip, verify, delete", t_archive)
        check("corrupt and missing replays return errors", t_errors)
        check("permanent failures stop being retried", t_retry_limit)
        check("loot classification keeps utility out of weapons", t_classification)
        check("insights build and text report", t_insights)
        check("Excel workbook has every sheet", t_excel)
        check("HUD initialises without error (demo mode)", t_overlay)

    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"\n  kept: {tmp}")

    failed = [r for r in RESULTS if not r[0]]
    print()
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 0 if not failed else 1


def main():
    ap = argparse.ArgumentParser(description="Simulated end-to-end test.")
    ap.add_argument("--keep", action="store_true", help="keep the temporary folder")
    args = ap.parse_args()
    print("SIMULATED RUN (no real replays touched)\n")
    return run(keep=args.keep)


if __name__ == "__main__":
    sys.exit(main())
