# Fortnite Grind & Combat Efficiency Suite

A local toolkit built on the replays Fortnite already writes to your PC. It counts the
chests **you** open, and turns those replays into efficiency, goal, loadout, drop-zone, mode,
quit-time and archiving reports. Nothing touches the game or its memory.

```
Start Chest Counter.bat     the desktop app (goals, streak, token odds)
chest_counter.py            its source
suite.py                    runs every module at once
efficiency.py               module 1 - chests per minute
forecast.py                 module 2 - chests per day to reach the target
loadout.py                  module 3 - loadout & combat success
spatial.py                  module 4 - drop zones and looting paths
mode.py                     module 5 - solo vs squads comparison
quit.py                     module 6 - chest velocity and optimal quit time
archive.py                  module 7 - stats.csv + replay archiving
insights.py                 the short-answer layer: best gun, best zone, when to quit
excel_report.py             builds the shareable Excel workbook
overlay.py                  live HUD for the second monitor
test_sim.py                 simulated end-to-end test (no replays needed)
launcher.py                 one-click control panel
build.py                    packages everything into one .exe
deploy.py                   publishes the source + workbook to GitHub
fc_core.py                  shared replay reading, analysis and storage
requirements.txt            Python dependencies (openpyxl)
reader/ChestReplayReader.exe  replay decoder (self-contained, no .NET needed)
data.json                   every counted match, including chest positions
stats.csv                   the clean per-match log
Fortnite_Chest_Report.xlsx  the generated workbook (after python excel_report.py)
archive/                    zipped replay backups
```

## Requirements

Windows, Python 3 (tkinter included in the normal installer) — already present on this PC.
The replay reader is a single self-contained executable, so there is nothing else to install.

The Excel report needs `openpyxl`; everything else runs on the standard library:

```bat
python -m pip install -r requirements.txt
```

PyInstaller is only needed for `build.py`, and it installs itself if missing.

## Quick start

```bat
Start Chest Counter.bat          :: the app
python suite.py                  :: every report in one go
```

For the suite to have anything to read, replays must be on:

**Fortnite → Settings → Game → Replays → Record Replays (Battle Royale) = ON**

Replays land in `%LOCALAPPDATA%\FortniteGame\Saved\Demos`. The suite reads a replay once it
has been untouched for 45 seconds, so a match shows up about a minute after it ends.

## How a chest is counted

Each replay records which player searched each chest. A chest is credited to you only when
the opener is the recording player, so teammates' chests and anything opened while you are
dead and spectating are ignored. Chests you break open with a pickaxe or explosive count too.

Kinds: **normal** (wooden/buried chests), **epic** (the rare/higher-tier chest — your replays
contain no separate "epic" class), **sprite** (sprite and relic chests). New chest classes are
learned automatically the first time a match contains one.

## Module 1 — Efficiency Analyzer (chests per minute)

```
python efficiency.py            # every day, newest first, top 5 matches each
python efficiency.py --top 10   # longer leaderboard
python efficiency.py --all      # one flat list of every match
```

CPM = chests ÷ minutes **alive**, where "alive" is the last moment the replay saw your pawn.
Match length is shown too, but it keeps counting while you spectate, so dividing by it would
punish an early death twice. Each row also shows time-to-first-chest and K/D, which is what
actually tells you whether a drop is fast or you just got lucky with a long match.

Use it by repeating the same drop and comparing CPM: the fastest loot loop wins on numbers,
not vibes.

## Module 2 — Dynamic Goal Forecaster

```
python forecast.py
python forecast.py --target 1600000 --by 2026-12-05
```

Recomputes from your live total every run:

* chests still needed for the target,
* **required chests per day** over the days left,
* your real pace (last 7 days),
* the verdict, including how long the target would take at your actual pace,
* token odds now and projected.

The plan is saved in `data.json`, so the next run tells you whether today's number went up or
down ("yesterday's plan was 57,100, so it went down by 3").

The 1,600,000 target is the ~90% confidence point for a 1-in-700,000 drop. It is deliberately
brutal: the report says so plainly instead of pretending 57,000 chests a day is a plan. If you
want a goal you can keep, set a daily goal in the app and let the forecaster show what that
actually buys you.

## Module 3 — Loadout & Combat Success

```
python loadout.py
python loadout.py --min 3      # only items seen in 3+ matches
```

Combat numbers (kills, deaths, K/D, survival, placement, wins) come from replicated player
state and the kill feed — exact. The loadout comes from the **contents of the chests you
opened**: the replay clusters every item that came out of a chest with that chest, so the
weapons and utility are yours, not the lobby's.

A modern replay does **not** record which weapon fired which shot. So instead of inventing
per-kill attribution, the report measures each item across the matches where you looted it:
K/D, average survival, average chests, average CPM. Small samples are called out, and items
seen in fewer than `--min` matches are kept out of the recommendation.

## Module 4 — Spatial Drop & Pathing Analyzer

```
python spatial.py                 # top 3 zones and the best path through each
python spatial.py --map           # plus a rough ASCII density map
python spatial.py --radius 8000   # tighter or looser zones (default 12000 = 120 m)
```

Every chest in the replay carries its map position (X, Y in Unreal units: 100 units = 1 m)
and the second it was opened, for the player who opened it. The module groups your chests
into zones and scores each one on two things it can actually measure:

* **yield** — chests you opened per visit to that area;
* **quietness** — how many chests *other* players opened in the same area.

```
 1. ZONE P15   around (65,750, 70,723)
    49 chests in 4 visits (12.2 per visit)   score 6.8
    contested: 56% of the chests here were opened by other players (63)
```

So "uncontested high-yield" is a measurement, not a guess: a zone where you take 12 chests a
visit while the lobby takes 3 is quiet; one where they take 60 is a hot drop. `score` is
yield × quietness, so it rewards both at once.

Each zone also reports **the path through it**: the order you actually opened the chests in
your best run there, collapsed into waypoints with the match clock. Re-land, follow the same
order, and compare CPM in module 1.

Replays do not carry POI names, so a zone is a coordinate cluster (~120 m, one POI's loot)
with a stable grid label. Two clusters in the same 130 m cell get a `-2` suffix, so labels
stay unique.

Caveat worth knowing: the position a chest is credited with is where it was opened, and about
4% of chests (11 of 293 here) have no replicated position at all. They still count; they just
do not appear on the map.

## Module 5 — Mode Comparison & Squad Estimator

```
python mode.py              # solo vs squads, squad estimate
python mode.py --detail     # plus every match with its teammate breakdown
```

Fortnite replicates a **TeamIndex** on every player state, and the replay knows which player
state recorded it. So the squad is not inferred from proximity: my team's player states are
the people I queued with, and a teammate's chest is one the game itself credited to their
pawn. "True Squad Chest Count" = my chests + every chest my squad opened that match.

Live result on this account:

```
  MODE     MTCH  HOURS  MINE/H  SQUAD/H   K/D   SURV  WIN%
  solo        4    0.7   173.5    173.5  1.25  10:48     0
  squads      9    1.9    89.1    204.7  6.67  12:34    33

  My own chests per hour:  solo 173.5   squads 89.1   -> SOLO wins for my odds
  Whole squad per hour:    solo 173.5   squads 204.7   (+31.2/h)
  In squads I opened 44% of the squad's chests.
```

That is the whole point of the module: the squad collectively loots **more** per hour than
you do solo (204.7 vs 173.5), yet **you** personally loot half as much (89.1 vs 173.5),
because three teammates are taking chests you would otherwise have had. Chests opened by
teammates do not touch your token odds, so squads only help your odds if they keep you alive
longer or let you loot faster than you lose — and the table shows which way it went.

Each teammate also gets a proximity figure: the share of the first six minutes they spent
within 40 m of you. 100% means they looted beside you; 6% means they dropped across the map,
which makes their chest count irrelevant to your route.

## Module 6 — Optimal Quit-Time & Velocity Analyzer

```
python quit.py                 # velocity curve, drop-off, best quit minute
python quit.py --queue 1.5     # if queueing takes you longer than 45s
```

Every chest has a timestamp, so a match is a sequence of loot events. This module builds the
**velocity curve** — chests per minute of match time, divided by the number of matches still
alive at that minute, so the curve reflects loot drying up rather than players dying.

Your live curve, which does **not** look like the usual "fast first five minutes":

```
   4:00  ###############        1.87
   7:00  ###################    2.36
   9:00  ###################### 2.65
  10:00  ###################### 2.70   <- peak
  13:00  ###############        1.78
  15:00  #########              1.11
  17:00  ####                   0.50
```

Peak velocity is 2.70 chests/min around **9:00**, and it halves by **14:00**. You are not
front-loading: you loot steadily and best in the middle of a match.

The quit recommendation comes from comparing two rates:

* **stay** — chests per minute from here to the end of the match;
* **restart** — chests in the first Q minutes of a fresh match, divided by `Q + 0.75`
  (the queue and loading gap, since a new match's drop time is already in the data).

Maximising the restart rate gives the quit minute:

```
Best quit minute: 10 (9:00 of match time)
116 chests/hour quitting there, vs 87 playing every match out
leaving early is the better trade by 29 chests/hour
```

So quitting at roughly **9:00** is worth about 29 chests/hour more than playing matches out.
That is a chests-per-hour statement only: leaving costs placement, XP, quest progress, and in
squads your teammates' game. The report prints that caveat next to the number instead of
burying it.

## Module 7 — Data Logger & Auto-Archiver

```
python archive.py --csv                        # rewrite stats.csv
python archive.py --archive --days 3           # zip counted replays older than 3 days
python archive.py --archive --days 3 --delete  # zip, verify, then delete the originals
python archive.py --archive --days 0 --dry-run # show what would happen
```

`stats.csv` has one row per match: date, match id, **mode**, chests, CPM, **squad chests**,
**true squad CPM**, kills, deaths, survival, placement, best weapon, **best drop zone** (the
zone holding most of that match's chests), **optimal quit time** (the model's exit point, or
the moment you died if that came first), and the replay file.

Archiving is conservative on purpose:

1. only replays the app has **already counted** are considered — unread or failed ones are
   never touched;
2. only files older than `--days` are considered;
3. they are compressed into `archive/replays-YYYY-MM.zip` (replays are OGG-compressed already,
   so expect modest savings);
4. the zip is re-opened and its CRCs checked;
5. **only with `--delete`** are the originals removed, and only if they are in the verified
   zip. A locked file (Fortnite still has it open) is skipped, not forced.

Deleting is irreversible; the default is to keep the originals. Your counts live in
`data.json`, so deleting a replay never changes your totals.

## Running everything together

```
python suite.py                     # refresh, then every report
python suite.py --map               # include the drop map
python suite.py --excel             # also write Fortnite_Chest_Report.xlsx
python suite.py --archive           # also archive, keep originals
python suite.py --archive --delete  # also archive and delete originals
python suite.py --out report.txt    # keep a copy
```

`suite.py` refreshes the match history once, then feeds the same data to every module, so the
numbers in all sections agree. It also rewrites `stats.csv`, prints the short insights report
and, with `--excel`, writes the workbook.

## Short answers — best gun, best zone, when to quit

```
python insights.py             # the three answers, plus the mode and daily rollups
python insights.py --full      # include the About / method section
```

The seven modules each explain one thing in detail. `insights.py` is the layer that answers
the three questions you actually ask after a session, using the same numbers:

* **Which gun works for me?** `loadout.ranked()` — weapons ranked by shrunk K/D and win rate
  across the matches where you looted them. Rarity variants (R / VR / SR) are merged into one
  gun, so `Pistol Scavenger Ore T03` is a single row instead of three.
* **Where do I get the most chests?** `spatial.zone_rows()` — zones ranked by chests per visit
  × quietness, with a stable grid label and compass direction.
* **How long should I stay?** `quit.recommendation()` — the exit minute that maximises chests
  per hour, overall and per mode.

The desktop app shows the same three answers as cards at the top, an **Insights** button opens
them in tabs, and the HUD reads the same data.

## Excel report (for sharing)

```
python excel_report.py                 # write Fortnite_Chest_Report.xlsx
python excel_report.py --out book.xlsx # choose the filename
python excel_report.py --sync          # read new replays first
```

One workbook, one sheet per question: **Summary** (headline numbers, top weapons, best zones,
quit advice), **Matches**, **Weapons**, **Utility**, **Drop Zones**, **Quit Curve**,
**Mode Compare**, **Daily** and **About**. Charts are native Excel charts, so they stay
editable. The app's **Export Excel** button builds the same file and offers to open it.

The workbook contains aggregate statistics only — no account name, no raw replays and no
`data.json` — so it is safe to send to friends.

## Publishing to GitHub

```
python deploy.py --public --push --repo yourname/fortnite-efficiency --no-exe --browser
```

`deploy.py` builds a clean `dist/` tree and pushes it. It publishes the source and the
generated workbook, and deliberately skips `data.json`, `stats.csv`, raw replays, `reader/`
and any nested `dist/` copies. `--no-exe` skips the PyInstaller build (much faster); drop it
if you also want the packaged exe. The token is read from `GITHUB_PERSONAL_ACCESS_TOKEN` (or
`GH_TOKEN` / `GITHUB_TOKEN`) and is never written to the repository.

## Live HUD (second monitor)

```
python overlay.py              # the real HUD
python overlay.py --demo       # fake numbers and a fast clock, to see the warning
python overlay.py --list-monitors
```

A 300×176 borderless, slightly transparent panel that stays on top and lands on your second
monitor automatically. It shows today's chests, progress to the daily goal, and today's CPM.

During a match it also runs a live clock from the replay file's start time and, at the minute
`quit.py` calculated as your best exit, the whole card starts flashing red:

```
QUIT MATCH NOW
diminishing returns
```

Drag it with the left mouse button; right-click for Refresh, Reset position and Quit. Its
position is saved in `data.json`, so it comes back to the same monitor.

One honest limitation: Fortnite is still writing the replay during a match, so the chest
total cannot update until the match ends. The HUD reads `data.json` only and says "between
matches" / starts the clock, rather than showing a number that looks live but is not.

## Testing without Fortnite

```
python test_sim.py             # 11 checks, all in a temp folder
python test_sim.py --keep      # keep the temporary data for inspection
```

`test_sim.py` builds a synthetic history that matches what the reader produces — chests with
timestamps, positions, squad mates, loadouts — then runs every module, `stats.csv`, the
archiver, the insights layer, the Excel builder and the HUD logic against it. It never touches
`data.json`, `stats.csv` or your replays.

What it covers: all six reports render; CPM maths matches `chests / alive minutes`; a match
where you die before opening anything does not crash anything; the CSV has the right columns
and totals; the archiver dry-runs, zips, verifies and only then deletes, and leaves uncounted
and fresh replays alone; a corrupt replay and a missing reader return errors instead of
raising; a permanently broken file stops being retried; loot classification keeps utility out
of the weapon ranking; the insights report builds; the Excel workbook has every sheet; and the
HUD switches to its warning at the right minute.

It earned its keep immediately: the zero-chest case crashed `quit.py` with a
`None` quit minute, which is now fixed and covered.

## Building the .exe

```
python build.py                # console build -> dist\FortniteGrindSuite.exe
python build.py --windowed     # no console window
```

Produces a single 13 MB `dist\FortniteGrindSuite.exe` containing every module, plus copies
beside it:

* `reader\ChestReplayReader.exe` — the 70 MB replay decoder, kept outside the exe
* `reader\cofres_conocidos.txt` — the learned chest classes
* `data.json` (if you have one) and `README.md`

Double-click the exe for a control panel: **Open HUD**, **Run full report**, **Refresh
stats.csv**, **Archive old replays (keeps originals)**. The HUD is started as its own process,
so closing the panel leaves it running.

The exe keeps its own `data.json`, `stats.csv` and `report.txt` in `dist\`, which is why the
reader and its files must stay in that folder — they are not inside the exe. `--windowed`
makes report mode silent (it still writes `report.txt`).

## When things go wrong

These are handled, most of them tested by `test_sim.py`:

| Situation | Behaviour |
| --- | --- |
| Replay still being recorded | Not read until the file has been quiet for 45 s |
| Corrupt / truncated replay | Stored as an error; retried only if the file grows |
| Replay fails 3× at the same size | Marked `gave_up`, skipped instead of retried forever |
| Reader missing or crashes | `analyze()` returns `{ok: false, error}` — never raises |
| One bad file in a batch | The rest still get read; the watcher keeps running |
| Disk full / read-only folder | `save_data()` returns False and warns on stderr; data stays in memory |
| Malformed reader JSON | Caught by `analyze()`, recorded as an error |
| Died before opening a chest | Counts as 0 chests, alive clamped to 60 s, no divide-by-zero |
| Half-written `data.json` | Falls back to an empty history rather than crashing |

## Notes and limits

* A replay is read once it has been quiet for 45s. If Fortnite is still holding the file, the
  read fails and is retried once the file grows.
* Chest positions are stored in `data.json` so the spatial module works offline; that is why
  `data.json` is bigger than a plain chest tally.
* "Alive" time is the last replicated position of your pawn, which is within a few seconds of
  the real death.
* Placement, kills and deaths come from the replay's player state; a match you left early can
  report no placement.
* New chest classes are learned into `reader/cofres_conocidos.txt`; existing matches are then
  re-read automatically (`SCHEMA` in `fc_core.py`).
* Deleting `data.json` starts the history over.

Replay parsing by FortniteReplayDecompressor (Shiqan / Cristiano3120), MIT licence.
Not affiliated with or endorsed by Epic Games. Fortnite is a trademark of Epic Games, Inc.
