"""
fc_core.py - shared core for the Fortnite Grind & Combat Efficiency Suite.

Everything that is not user interface lives here so the app (chest_counter.py) and the
five suite modules (efficiency.py, forecast.py, loadout.py, spatial.py, archive.py) all read the same
data the same way.

The one job of this module: turn Fortnite .replay files into small, comparable match
records, and keep those records in data.json.

A match record looks like:
    {
      "name": "UnsavedReplay-....replay", "when": "2026-11-07 00:53", "day": "2026-11-07",
      "ok": True, "schema": 4, "size": 16576205,
      "total": 26, "counts": {"normal": 23, "epic": 3, "sprite": 0},
      "chests": [{"t": 116, "tipo": "Tiered_Chest_Athena", "kind": "normal", "destroyed": False}],
      "duration": 1035,          # match length, seconds
      "alive": 816,              # seconds until my pawn was last seen (or the end)
      "cpm": 1.91,               # chests per minute while alive
      "cpm_match": 1.51,         # chests per minute of match
      "first_chest": 116,        # time to my first chest
      "kills": 9, "deaths": 1, "survival": 816, "placement": 2,
      "kills_detail": [{"t": 264, "victim": "", "bot": True, "distance": 572}],
      "loadout": [{"item": "Shotgun Pump FirePetal", "kind": "weapon", "n": 2, "t": 123}]
    }
"""


import datetime as dt
import json
import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

# When packaged by PyInstaller the code lives in a temp folder that is wiped on exit, so data
# files must sit next to the .exe instead of next to this module.
BASE = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent)
# Files bundled *inside* the exe land in _MEIPASS; files beside the exe stay in BASE.
BUNDLE = Path(getattr(sys, "_MEIPASS", BASE))
READER = BASE / "reader" / "ChestReplayReader.exe"
if not READER.exists():
    READER = BUNDLE / "reader" / "ChestReplayReader.exe"
KNOWN = BASE / "reader" / "cofres_conocidos.txt"
DATA = BASE / "data.json"
DEMOS = Path(os.environ.get("LOCALAPPDATA", "")) / "FortniteGame" / "Saved" / "Demos"
ARCHIVE = BASE / "archive"
STATS_CSV = BASE / "stats.csv"

QUIET_SECONDS = 45          # a replay left untouched this long is finished
READ_TIMEOUT = 180          # seconds allowed to read one replay
SCHEMA = 7                  # bump when analyze() gains fields: old records are re-read

DEFAULT_GOAL = 400          # chests per day
TOKEN_TARGET = 1_600_000    # the 90% threshold for a 1-in-700,000 token, rounded
EVENT_END = dt.date(2026, 12, 5)
TOKEN_MODELS = (
    ("Community estimate", 1 / 700_000, "1 in 700,000"),
    ("Early datamine", 1 / 2_000, "1 in 2,000"),
)

BUCKETS = ("normal", "epic", "sprite")
BUCKET_LABEL = {"normal": "NORMAL", "epic": "EPIC", "sprite": "SPRITE"}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

AMMO_MARKERS = ("ammo",)

# Loot classification.  The replay gives us item class names, not the in-game display
# name, so this is a name-based guess.  It matters because a medkit or a keycard that
# was previously counted as a "weapon" made the loadout report recommend nonsense.
#
# Two signals are used:
#   * utility words win first (a WID_ item can still be a bandage or a shield potion);
#   * a WID_ prefix or a weapon word means a weapon.
# Anything we cannot place stays "other" instead of polluting the weapon ranking.
UTILITY_WORDS = (
    "consumable", "shield", "medkit", "medbox", "slurp", "bandage", "grenade",
    "trap", "fish", "apple", "meat", "chug", "potion", "heal", "juice",
    "bush", "stimpulse", "zipline", "keycard", "relic", "portashack",
    "purplestuff", "extractor",
)
WEAPON_WORDS = (
    "shotgun", "pistol", "assault", "rifle", "smg", "sniper", "dmr", "bow",
    "launcher", "slingshot", "blazer", "flintlock", "machine", "minigun",
    "revolver", "cannon", "melee", "sword", "axe", "pickaxe",
    # Class names without an obvious gun word, confirmed from the reader's WID_ prefix.
    "purpledrool", "graftedcrunchy",
)


# ---------------------------------------------------------------- chest kinds

def bucket(tipo):
    """Which of the three kinds a chest blueprint is, from its class name."""
    t = (tipo or "").lower()
    if "sprite" in t or "relic" in t:
        return "sprite"
    if "rarechest" in t or "epic" in t:
        return "epic"
    return "normal"


def empty_counts():
    return {b: 0 for b in BUCKETS}


# ---------------------------------------------------------------- storage

def load_data():
    if DATA.exists():
        try:
            with open(DATA, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data.get("replays"), dict):
                data.setdefault("settings", {})
                data.setdefault("version", SCHEMA)
                return data
        except (OSError, ValueError):
            pass
    return {"replays": {}, "settings": {}, "version": SCHEMA}


def save_data(data):
    """Write data.json atomically. Returns False instead of raising if the disk refuses."""
    tmp = DATA.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, DATA)
        return True
    except (OSError, TypeError, ValueError) as e:
        # A full disk or a read-only folder must not kill the watcher: the in-memory data is
        # still good, and the next save can succeed once the problem is gone.
        print(f"warning: could not save {DATA.name}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def known_types():
    if not KNOWN.exists():
        # First run of a packaged build: seed from the copy shipped inside the exe.
        bundled = BUNDLE / "reader" / "cofres_conocidos.txt"
        if bundled.exists() and bundled != KNOWN:
            try:
                KNOWN.parent.mkdir(parents=True, exist_ok=True)
                KNOWN.write_text(bundled.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:
                return []
        return []
    return [l.strip() for l in KNOWN.read_text(encoding="utf-8").splitlines()
            if l.strip().startswith("/")]


def remember_types(paths):
    """Fortnite adds chest classes every season; keep the ones we have seen."""
    try:
        have = set(known_types())
        new = [p for p in paths if p.startswith("/") and p not in have]
        if new:
            KNOWN.parent.mkdir(parents=True, exist_ok=True)
            with open(KNOWN, "a", encoding="utf-8") as f:
                for p in new:
                    f.write(p + "\n")
        return len(new)
    except OSError as e:
        print(f"warning: could not record chest types: {e}", file=sys.stderr)
        return 0


# ---------------------------------------------------------------- the reader

def read_replay(path):
    """Run the reader on one replay. Returns the parsed JSON object or an error dict."""
    try:
        proc = subprocess.run(
            [str(READER), str(path)],
            capture_output=True, timeout=READ_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    err = proc.stderr.decode("utf-8", "replace").strip()
    return {"ok": False, "error": err[-400:] or f"reader exit code {proc.returncode}"}


# ---------------------------------------------------------------- loot names

RARITY = ("C", "UC", "R", "VR", "SR", "UR")


def clean_item(name):
    """
    WID_Shotgun_Pump_FirePetal_Athena_UC -> 'Shotgun Pump FirePetal'.

    Rarity tokens are removed wherever they appear, so the R / VR / SR versions of one gun
    collapse into a single row instead of splitting the weapon ranking three ways.
    """
    s = name or ""
    for head in ("WID_", "AGID_"):
        if s.startswith(head):
            s = s[len(head):]
            break
    parts = [p for p in s.replace("_", " ").split() if p]
    drop = set(RARITY) | {"Athena"}
    parts = [p for p in parts if p not in drop]
    if parts and parts[-1] in ("A", "X"):
        parts.pop()
    return " ".join(parts) or name


def classify_loot(name):
    """
    weapon / utility / other, from a raw class name or a cleaned display name.

    Utility words are checked first because WID_Paprika_Bandage and WID_ZiplineDeployer
    are WID_ items that are not weapons.  A WID_ prefix then means a weapon.  Unknown
    items are "other" so they never appear in the recommended loadout.
    """
    n = (name or "").lower()
    if any(w in n for w in UTILITY_WORDS):
        return "utility"
    if n.startswith("wid_") or any(w in n for w in WEAPON_WORDS):
        return "weapon"
    return "other"


def loot_kind(name):
    """Backwards-compatible alias: utility or weapon, with other folded into utility."""
    kind = classify_loot(name)
    return "utility" if kind == "other" else kind


def chest_loot(chest_loot_list):
    """
    The loadout I actually looted: the contents of the chests I opened.

    The replay clusters every item that came out of a chest with that chest, so this is exact
    per-chest loot, not a guess. Ammo is dropped (it is not a loadout choice).
    """
    mine = {}
    for group in chest_loot_list or []:
        t = int(group.get("t") or 0)
        for raw in group.get("items") or []:
            if any(m in raw.lower() for m in AMMO_MARKERS):
                continue
            name = clean_item(raw)
            kind = classify_loot(raw)
            rec = mine.setdefault((name, kind),
                                  {"item": name, "kind": kind, "raw": raw, "n": 0, "t": t})
            rec["n"] += 1
            rec["t"] = min(rec["t"], t)
    return sorted(mine.values(), key=lambda r: (r["t"], r["item"]))


# ---------------------------------------------------------------- analysis

def chest_positions(raw):
    """
    Split the reader's chest list into mine and everyone else's, without double counting.

    A chest can appear twice in the replay (the actor record and the loot record), so the
    same (x, y, t) is collapsed to one entry and always kept as mine when either copy says so.
    """
    rows = []
    for c in raw or []:
        try:
            x, y = int(c["x"]), int(c["y"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append((x, y, int(c.get("t") or 0), bool(c.get("mine"))))

    cell = 150            # chests this close together are the same double-recorded chest
    mine, mine_keys, mine_cells = [], set(), set()
    for x, y, t, is_mine in rows:
        if not is_mine or (x, y, t) in mine_keys:
            continue
        mine_keys.add((x, y, t))
        mine_cells.add((x // cell, y // cell))
        mine.append([x, y, t])

    others, other_cells = [], set()
    for x, y, t, is_mine in rows:
        if is_mine:
            continue
        c = (x // cell, y // cell)
        if c in mine_cells or c in other_cells:
            continue
        other_cells.add(c)
        others.append([x, y])
    return mine, others


def analyze(path, learn=True):
    """
    Read one replay and boil it down to the record the suite stores.

    Never raises: a corrupt or half-written replay, a reader that crashes, or JSON in an
    unexpected shape all come back as {"ok": False, "error": ...}. One bad file must not
    take down the watcher or a batch of good ones.
    """
    try:
        return _analyze(path, learn)
    except Exception as e:                              # noqa: BLE001 - deliberate safety net
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:400]}


def _analyze(path, learn=True):
    result = read_replay(path)
    if not result or not result.get("ok"):
        return {"ok": False, "error": ((result or {}).get("error")
                                       or "could not read the replay")[:400]}

    if learn:
        tipos = result.get("grupos_cofre") or []
        if remember_types(tipos) and tipos:
            again = read_replay(path)          # re-read now that the classes are known
            if again and again.get("ok"):
                result = again

    chests = []
    for c in result.get("mis_cofres") or []:
        chests.append({"t": int(c.get("t") or 0), "tipo": c.get("tipo") or "?",
                       "kind": bucket(c.get("tipo")), "destroyed": bool(c.get("destr"))})
    counts = empty_counts()
    for c in chests:
        if not c["destroyed"]:            # only count chests actually opened, not destroyed
            counts[c["kind"]] += 1

    duration = int(result.get("duracion_seg") or 0)
    last_pos = float(result.get("mi_ultima_pos_seg") or 0)
    # Time my character was actually in the match: the last moment the replay saw my pawn.
    alive = int(min(duration, last_pos)) if last_pos >= 60 else duration
    alive = max(alive, 60)
    total = sum(counts.values())
    kills = int(result.get("mis_kills") or 0)
    muerte = result.get("mi_muerte")
    placement = result.get("mi_puesto")

    loadout = chest_loot(result.get("mis_cofre_loot"))
    mine_pos, other_pos = chest_positions(result.get("mis_cofres_pos"))

    # Squad structure: exact, from replicated team indexes (see mode.py).
    mates = [{"chests": int(m.get("cofres") or 0),
              "last": int(m.get("ultima_seg") or 0),
              "prox": int(m.get("conmigo_pct") or 0),
              "dist": m.get("dist_media")}
             for m in (result.get("companeros") or [])]
    team_size = result.get("mi_equipo_tamano")
    squad_chests = total + sum(m["chests"] for m in mates)

    return {
        "ok": True,
        "schema": SCHEMA,
        "chests": chests,
        "counts": counts,
        "total": total,
        "duration": duration,
        "alive": alive,
        "cpm": round(total / (alive / 60), 2),
        "cpm_match": round(total / (duration / 60), 2) if duration else 0.0,
        "first_chest": min((c["t"] for c in chests), default=0),
        "kills": kills,
        "deaths": 1 if muerte else 0,
        "survival": alive,
        "placement": placement,
        "kills_detail": [
            {"t": int(k.get("t") or 0), "bot": bool(k.get("bot")),
             "distance": k.get("distancia")}
            for k in (result.get("mis_kills_detalle") or [])
        ],
        "loadout": loadout,
        # [x, y, t] for each chest I opened, [x, y] for chests other players opened.
        # Used by spatial.py to find the drop zones I loot best.
        "chest_pos": mine_pos,
        "other_pos": other_pos,
        "mode": mode_of(team_size),
        "team_size": team_size,
        "lobby_mode": lobby_mode(result.get("tamanos_equipo")),
        "mates": mates,
        "squad_chests": squad_chests,
        "squad_cpm": round(squad_chests / (alive / 60), 2),
        "types": result.get("mios_por_tipo") or {},
    }


def mode_of(team_size):
    """Solo / duos / squads from my team's player count, unknown when it is not replicated."""
    if not team_size:
        return "unknown"
    if team_size <= 1:
        return "solo"
    if team_size == 2:
        return "duos"
    return "squads"


def lobby_mode(sizes):
    """What kind of lobby it was, from the most common team size of two or more."""
    grouped = {}
    for n in sizes or []:
        if n and n >= 2:
            grouped[n] = grouped.get(n, 0) + 1
    if not grouped:
        return "solo"
    biggest = max(grouped.items(), key=lambda kv: kv[1])[0]
    return "duos" if biggest == 2 else "squads"


def watch_list():
    """Replay files in the Fortnite folder, newest first."""
    if not DEMOS.is_dir():
        return []
    out = []
    for f in DEMOS.glob("*.replay"):
        try:
            st = f.stat()
        except OSError:
            continue
        out.append((f, st.st_size, st.st_mtime))
    out.sort(key=lambda x: x[2], reverse=True)
    return out


def stamp(mtime):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))


def day_of(mtime):
    return time.strftime("%Y-%m-%d", time.localtime(mtime))


MAX_ATTEMPTS = 3        # give up on a file that fails at the same size this many times


def needs_read(data, path, size):
    """True when this replay is new, was never read, or was read by an older schema."""
    rec = data["replays"].get(path.name)
    if not rec:
        return True
    if rec.get("schema") != SCHEMA:
        return True
    if rec.get("ok"):
        return False
    if rec.get("gave_up") and rec.get("size") == size:
        return False                        # corrupt for good: stop burning time on it
    return rec.get("size") != size          # retry once the file has grown


def sync(data=None, force=False, on_progress=None):
    """
    Read every finished replay that is new or stale. Returns the updated data dict.

    Shared by the app's background thread and by every suite module, so running
    `python suite.py` also refreshes the match history the GUI shows.
    """
    data = data if data is not None else load_data()
    files = watch_list()
    now = time.time()
    pending = [(p, s, m) for p, s, m in files
               if now - m > QUIET_SECONDS and (force or needs_read(data, p, s))]
    for i, (path, size, mtime) in enumerate(pending, 1):
        if on_progress:
            on_progress(i, len(pending), path.name)
        prev = data["replays"].get(path.name) or {}
        try:
            rec = analyze(path)
        except Exception as e:                          # belt and braces around one file
            rec = {"ok": False, "error": f"{type(e).__name__}: {e}"[:400]}
        rec.update(name=path.name, when=stamp(mtime), day=day_of(mtime), size=size)
        if rec.get("ok"):
            rec["attempts"] = 0
        else:
            attempts = int(prev.get("attempts") or 0) + 1
            rec["attempts"] = attempts
            rec["gave_up"] = attempts >= MAX_ATTEMPTS and prev.get("size") == size
        data["replays"][path.name] = rec
        save_data(data)
    return data


class Worker(threading.Thread):
    """Background watcher for the GUI: reads new replays and posts events."""

    def __init__(self, data, events):
        super().__init__(daemon=True)
        self.data = data
        self.events = events
        self.stop_flag = threading.Event()
        self.wake = threading.Event()

    def run(self):
        while not self.stop_flag.is_set():
            try:
                self.scan()
            except Exception as e:                      # never let the watcher die
                self.events.put(("status", ("error", f"{type(e).__name__}: {e}")))
            self.wake.wait(15)
            self.wake.clear()

    def scan(self):
        if not watch_list():
            self.events.put(("status", ("noreplays", None)))
            return
        pending = [x for x in watch_list()
                   if time.time() - x[2] > QUIET_SECONDS and needs_read(self.data, x[0], x[1])]
        if not pending:
            self.events.put(("status", ("watching", len(self.data["replays"]))))
            return
        for i, (path, size, mtime) in enumerate(pending, 1):
            self.events.put(("status", ("reading", (i, len(pending)))))
            prev = self.data["replays"].get(path.name) or {}
            try:
                rec = analyze(path)
            except Exception as e:                      # one bad file, not a dead watcher
                rec = {"ok": False, "error": f"{type(e).__name__}: {e}"[:400]}
            rec.update(name=path.name, when=stamp(mtime), day=day_of(mtime), size=size)
            if rec.get("ok"):
                rec["attempts"] = 0
            else:
                attempts = int(prev.get("attempts") or 0) + 1
                rec["attempts"] = attempts
                rec["gave_up"] = attempts >= MAX_ATTEMPTS and prev.get("size") == size
            self.data["replays"][path.name] = rec
            save_data(self.data)
            self.events.put(("replay", path.name))
        self.events.put(("status", ("watching", len(self.data["replays"]))))


# ---------------------------------------------------------------- aggregates

def totals(data):
    total = empty_counts()
    for rec in data["replays"].values():
        if rec.get("ok"):
            for b in BUCKETS:
                total[b] += rec["counts"].get(b, 0)
    return total


def matches(data):
    """Every readable match, oldest first."""
    return sorted((r for r in data["replays"].values() if r.get("ok")),
                  key=lambda r: r.get("when", ""))


def daily_totals(data):
    days = {}
    for rec in matches(data):
        if rec.get("day"):
            days[rec["day"]] = days.get(rec["day"], 0) + rec["total"]
    return days


def goal(data):
    try:
        return max(1, int(data.get("settings", {}).get("daily_goal") or DEFAULT_GOAL))
    except (TypeError, ValueError):
        return DEFAULT_GOAL


def streak(days, goal_n, today):
    """Consecutive days that reached the goal, counting back from today or yesterday."""
    if goal_n <= 0:
        return 0
    d = today
    if days.get(d.isoformat(), 0) < goal_n:
        d -= dt.timedelta(days=1)              # today is still in progress
    n = 0
    while days.get(d.isoformat(), 0) >= goal_n:
        n += 1
        d -= dt.timedelta(days=1)
    return n


# ---------------------------------------------------------------- maths

def chance(n, p):
    """Probability of at least one drop in n chests."""
    if n <= 0:
        return 0.0
    return 1.0 - math.pow(1.0 - p, n)


def fmt_chance(p):
    pct = p * 100
    if pct >= 99.95:
        return ">99.9%"
    if pct < 0.01:
        return "<0.01%"
    if pct < 1:
        return f"{pct:.2f}%"
    return f"{pct:.1f}%"


def fmt_int(n):
    return f"{int(round(n)):,}"


def fmt_clock(seconds):
    s = max(0, int(seconds))
    return f"{s // 60}:{s % 60:02d}"


def fmt_day(iso):
    try:
        d = dt.date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


# ---------------------------------------------------------------- console fallback

def scan_cli():
    data = sync()
    for rec in matches(data):
        c = rec["counts"]
        print(f"{rec['name']}  {rec['when']}  total={rec['total']:3d}  "
              f"normal={c['normal']:3d} epic={c['epic']:3d} sprite={c['sprite']:3d}  "
              f"cpm={rec.get('cpm', 0):.2f}")
    t = totals(data)
    print(f"\nALL MATCHES: {sum(t.values())} chests  "
          f"(normal {t['normal']}, epic {t['epic']}, sprite {t['sprite']})")
    print(f"Daily goal: {goal(data)}")
    return 0
