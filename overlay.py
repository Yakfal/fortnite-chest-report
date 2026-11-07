"""
overlay.py - Live desktop HUD for the second monitor.

A small, borderless, slightly transparent widget that stays on top and shows:

  * today's chest count and progress towards the daily goal
  * today's chests per minute
  * the live match clock, and a flashing QUIT MATCH NOW when you pass the minute the
    quit-time module calculated as your best exit

  * Page 2: Match strategy - optimal quit time, chests per match target, match length
  * Page 3: Loadout - top 3 recommended weapons with K/D and survival
  * Page 4: Drop zones - top 3 zones with contested %, chests/visit
  * Page 5: Map - the current Treasure Hunters chest map with your played zones marked

It reads data.json (never the replays) so it costs nothing and cannot disturb a match.
Chests from the match you are in only appear once Fortnite finishes writing the replay:
the file is still being recorded, so it is not readable until then. The HUD says so instead
of showing a frozen number and letting you think it is broken.

Drag it with the left mouse button, right-click for a menu. Position is remembered in
data.json under settings.overlay, so it stays on the same monitor between runs.

Navigation: Left/Right arrow keys or click to cycle pages. Auto-cycles every 8s.

Usage:
    python overlay.py            # real data
    python overlay.py --demo     # fake numbers, fast clock, to see the warning
    python overlay.py --list-monitors
"""

import argparse
import ctypes
import json
import re
import sys
import threading
import time
from pathlib import Path

import tkinter as tk

import fc_core as core
import quit as quit_model
import loadout
import spatial

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except Exception:
    HAS_PIL = False

BG = "#0a0e13"
CARD = "#111820"
TEXT = "#e8eef5"
MUTED = "#7f8f9f"
ACCENT = "#39d98a"
WARN = "#ff4d4d"
WARN_BG = "#3a0b0b"

REPLAY_RE = re.compile(r"UnsavedReplay-(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.(\d{2})")

# Where to find the chest map image. Works in both dev (python overlay.py) and frozen
# (FortniteGrindSuite.exe) modes: in frozen mode the .exe's directory is used, otherwise
# the overlay.py source file's directory.
def _map_resource_path() -> Path:
    if getattr(sys, "frozen", False):
        # Running from the compiled .exe — resources sit next to it.
        base = Path(sys.executable).resolve().parent
    else:
        # Running from source — resources sit next to overlay.py.
        base = Path(__file__).resolve().parent
    return base / "chest_map.webp"

MAP_IMAGE_PATH = _map_resource_path()

# Unreal world units for the Battle Royale island.  The map image is 1200x630 and is not
# georeferenced by the replay, so this is a linear fit to the island's usual bounds:
# 100 units = 1 metre, X grows east, Y grows north (image Y grows down, so it is flipped).
WORLD_MIN = -130000.0
WORLD_MAX = 130000.0

# ---------------------------------------------------------------------------- helpers

def monitor_geometry():
    """
    Virtual screen bounds and the primary screen width, via the Windows API.

    Tk only reports the primary monitor, which is exactly the wrong number for placing a HUD
    on a second screen, so the virtual desktop metrics are read directly.
    """
    try:
        user32 = ctypes.windll.user32
        vx = user32.GetSystemMetrics(76)      # SM_XVIRTUALSCREEN
        vy = user32.GetSystemMetrics(77)      # SM_YVIRTUALSCREEN
        vw = user32.GetSystemMetrics(78)      # SM_CXVIRTUALSCREEN
        vh = user32.GetSystemMetrics(79)      # SM_CYVIRTUALSCREEN
        primary = user32.GetSystemMetrics(0)  # SM_CXSCREEN
        if vw and vh:
            return vx, vy, vw, vh, primary
    except Exception:                          # non-Windows or locked-down shell
        pass
    return 0, 0, 1200, 800, 1200


def match_start(name):
    """Start time of a replay, parsed from its file name (that is when recording began)."""
    m = REPLAY_RE.search(name)
    if not m:
        return None
    y, mo, d, h, mi, s = (int(g) for g in m.groups())
    try:
        return time.mktime((y, mo, d, h, mi, s, 0, 0, -1))
    except (ValueError, OverflowError):
        return None


def in_progress_match():
    """
    The replay Fortnite is writing right now, if any.

    A file that keeps growing and is not yet in data.json is the match in progress. Using the
    file's start time means the clock is right even if the app was opened mid-match.
    """
    if not core.DEMOS.is_dir():
        return None
    newest = None
    for f in core.DEMOS.glob("*.replay"):
        try:
            st = f.stat()
        except OSError:
            continue
        if newest is None or st.st_mtime > newest[2]:
            newest = (f, st.st_size, st.st_mtime)
    if not newest:
        return None
    f, _, mtime = newest
    started = match_start(f.name)
    if started is None or time.time() - mtime > 120:
        return None                  # nothing written for two minutes: not a live match
    return {"file": f.name, "start": started, "size": newest[1]}


PAGE_NAMES = [
    "TODAY",           # Page 0: chest count, goal, CPM, clock
    "STRATEGY",        # Page 1: quit time, chests/match, match length
    "LOADOUT",         # Page 2: recommended weapons
    "DROP ZONES",      # Page 3: recommended drop zones
    "MAP",             # Page 4: current chest map with your zones
]


class Overlay:
    """The HUD window itself."""

    def __init__(self, root, demo=False):
        self.root = root
        self.demo = demo
        self.data = core.load_data()
        self.quitting = False
        self.flash_on = False
        self.demo_elapsed = 0
        self.last_data_check = 0.0
        self.model = None
        self.page = 0                              # current page (0-4)
        self.last_page_switch = 0.0                # for auto-cycle
        self.weapons = {}                          # cache for loadout page
        self.zones = []                            # cache for drop zone page
        self.strategy = {}                         # cache for strategy page
        self.map_image = None                      # PIL Image of the chest map
        self.map_thumb_photo = None                # PhotoImage for the tab indicator
        self.own_zone_positions = []               # own chest positions per zone
        self.all_own_positions = []                # all own chest positions
        self.hovered_marker = None                 # currently hovered marker

        root.title("Fortnite Grind HUD")
        root.configure(bg=BG)
        root.overrideredirect(True)                 # no title bar, no border
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.92)

        width, height = 320, 446
        self.place_window(width, height)

        self.frame = tk.Frame(root, bg=BG, highlightbackground="#1d2733",
                              highlightthickness=1)
        self.frame.pack(fill="both", expand=True)
        self.frame.bind("<Button-1>", self.drag_start)
        self.frame.bind("<B1-Motion>", self.drag)
        self.frame.bind("<ButtonRelease-1>", self.drag_end)

        # Page navigation
        self.frame.bind("<Left>", lambda e: self.switch_page(-1))
        self.frame.bind("<Right>", lambda e: self.switch_page(1))
        self.frame.bind("<Button-1>", self.handle_click)  # Click to advance page

        # Header with page name and navigation dots
        self.header = tk.Frame(self.frame, bg=BG)
        self.header.pack(fill="x", padx=14, pady=(8, 0))

        self.lbl_page_name = tk.Label(self.header, text="", bg=BG, fg=ACCENT,
                                       font=("Segoe UI", 8, "bold"))
        self.lbl_page_name.pack(side="left")

        self.page_dots = []
        for i in range(len(PAGE_NAMES)):
            dot = tk.Label(self.header, text="●", bg=BG, fg=MUTED if i > 0 else ACCENT,
                           font=("Segoe UI", 8))
            dot.pack(side="right")
            self.page_dots.append(dot)

        # Content area (scrollable conceptually, but we swap pages)
        self.content = tk.Frame(self.frame, bg=BG)
        self.content.pack(fill="both", expand=True, padx=14, pady=(4, 4))

        # Footer with page indicator
        self.footer = tk.Label(self.frame, text="", bg=BG, fg=MUTED,
                                font=("Segoe UI", 7))
        self.footer.pack(anchor="w", padx=14, pady=(0, 6))

        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_command(label="Refresh now", command=self.reload)
        self.menu.add_command(label="Reset position", command=self.reset_position)
        self.menu.add_separator()
        self.menu.add_command(label="Quit HUD", command=self.close)
        for w in (self.frame,) + tuple(self.frame.winfo_children()):
            w.bind("<Button-3>", self.show_menu)

        self.reload()
        if not demo:
            # Keep the HUD's numbers current on its own: a quiet background sync picks up new
            # replays, so the panel and the exe's own data.json never drift apart.
            threading.Thread(target=self._sync_loop, daemon=True).start()
        self.tick()

    def _sync_loop(self, every=90):
        while not self.quitting:
            time.sleep(every)
            if self.quitting:
                return
            try:
                core.sync()
            except Exception as e:              # a failed sync must not kill the HUD
                print(f"overlay: sync failed ({type(e).__name__}: {e})")

    # ---------------------------------------------------------------- window
    def place_window(self, width, height):
        """Put the HUD on the second monitor when there is one, top-left of it."""
        vx, vy, vw, vh, primary = monitor_geometry()
        saved = (self.data.get("settings", {}).get("overlay") or {})
        x = saved.get("x")
        y = saved.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            if vw > primary + 200:                  # second monitor to the right
                x, y = primary + 48, 48
            else:
                x, y = primary - width - 48, 48
        x = max(vx, min(x, vx + vw - width))
        y = max(vy, min(y, vy + vh - height))
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def drag_start(self, event):
        self._drag = {"x": event.x_root, "y": event.y_root,
                      "wx": self.root.winfo_x(), "wy": self.root.winfo_y()}

    def drag(self, event):
        if not hasattr(self, "_drag") or not isinstance(self._drag, dict):
            return
        dx = event.x_root - self._drag["x"]
        dy = event.y_root - self._drag["y"]
        if abs(dx) > 3 or abs(dy) > 3:
            self._drag["moved"] = True
        x = self._drag["wx"] + dx
        y = self._drag["wy"] + dy
        vx, vy, vw, vh, _ = monitor_geometry()
        x = max(vx - 60, min(x, vx + vw - 60))
        y = max(vy, min(y, vy + vh - 40))
        self.root.geometry(f"+{x}+{y}")

    def drag_end(self, event):
        # Clean up the drag state
        if hasattr(self, "_drag") and isinstance(self._drag, dict):
            del self._drag

    def handle_click(self, event):
        """Click to advance to next page (unless dragging)."""
        # If dragging happened, don't treat it as a click
        if hasattr(self, "_drag") and isinstance(self._drag, dict) and self._drag.get("moved"):
            return
        self.switch_page(1)

    def show_menu(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def reset_position(self):
        self.data.setdefault("settings", {}).pop("overlay", None)
        core.save_data(self.data)
        self.place_window(320, 446)

    def close(self):
        self.quitting = True
        try:
            settings = self.data.setdefault("settings", {})
            settings["overlay"] = {"x": self.root.winfo_x(), "y": self.root.winfo_y()}
            core.save_data(self.data)
        except Exception:                     # closing must never raise
            pass
        self.root.destroy()

    def switch_page(self, delta):
        """Switch to another page (delta = +1 or -1)."""
        self.page = (self.page + delta) % len(PAGE_NAMES)
        self.last_page_switch = time.time()
        self.render()

    # ---------------------------------------------------------------- data
    def reload(self):
        """Scan for new matches, then reload data.json and rebuild the models."""
        try:
            core.sync()                       # pick up any finished replays first
            self.data = core.load_data()
            self.model = quit_model.analyse(self.data)
            self.weapons = loadout.weapon_stats(core.matches(self.data))
            self.zones, _ = spatial.analyse(self.data)
            self._build_strategy_cache()
            self._build_marker_cache()
        except Exception as e:                # a half-written file must not kill the HUD
            print(f"overlay: could not refresh ({type(e).__name__}: {e})")
        self.last_data_check = time.time()
        self.render()

    def _build_strategy_cache(self):
        """Pre-compute strategy page data from the models."""
        ms = core.matches(self.data)
        if not ms:
            self.strategy = {"avg_chests_per_match": 0, "quit_at": 10, "per_hour": 0,
                              "full_per_hour": 0, "optimal_chests_per_match": 0,
                              "match_length_rec": "—"}
            return

        total_chests = sum(m.get("total", 0) for m in ms)
        avg_chests = total_chests / len(ms)
        avg_life = sum(m.get("survival", 0) for m in ms) / len(ms) / 60  # minutes

        self.strategy = {
            "avg_chests_per_match": avg_chests,
            "quit_at": self.model.get("quit_at", 10) if self.model else 10,
            "per_hour": self.model.get("per_hour", 0) if self.model else 0,
            "full_per_hour": self.model.get("full_per_hour", 0) if self.model else 0,
            "optimal_chests_per_match": avg_chests * (self.strategy["quit_at"] / max(1, avg_life)),
            "match_length_rec": f"{self.strategy['quit_at']}:00",
        }

    def _build_marker_cache(self):
        """Build coordinates for markers on the map page."""
        self.own_zone_positions = []
        self.all_own_positions = []

        if not HAS_PIL:
            return

        ms = core.matches(self.data)
        for m in ms:
            for pos in m.get("chest_pos", []):
                if len(pos) >= 3:
                    self.all_own_positions.append((pos[0], pos[1]))

        # Build per-zone markers (top 3 zones from cached self.zones)
        if self.zones:
            for z in self.zones[:3]:
                if z.get("centre") and len(z["centre"]) >= 2:
                    self.own_zone_positions.append({
                        "x": z["centre"][0],
                        "y": z["centre"][1],
                        "label": z.get("label", "?"),
                        "mine": z["mine"],
                        "score": z.get("score", 0),
                        "per_visit": z.get("per_visit", 0),
                        "contested": z.get("contested", 0),
                    })

    def today_stats(self):
        """Today's chests and CPM from the stored matches."""
        today = time.strftime("%Y-%m-%d")
        ms = [m for m in core.matches(self.data) if m.get("day") == today]
        if self.demo:
            ms = [{"total": 0, "alive": 0, "chests": []}]
        chests = sum(m.get("total", 0) for m in ms)
        alive = sum(m.get("alive", 0) for m in ms)
        cpm = (chests / (alive / 60)) if alive else 0.0
        goal = int((self.data.get("settings") or {}).get("daily_goal") or 400)
        return chests, cpm, goal, len(ms)

    def _page_header(self):
        """Update the header with current page name and dots."""
        name = PAGE_NAMES[self.page]
        self.lbl_page_name.config(text=name)
        for i, dot in enumerate(self.page_dots):
            dot.config(fg=ACCENT if i == self.page else MUTED)

    def _page_footer(self):
        """Update the footer with page indicator."""
        total = len(PAGE_NAMES)
        current = self.page + 1
        self.footer.config(text=f"page {current}/{total}  ·  ← → or click to switch")

    def render_page_0(self, elapsed=None):
        """Page 0: Today's stats - chest count, goal, CPM, clock, quit banner."""
        # Clear content
        for w in self.content.winfo_children():
            w.destroy()

        chests, cpm, goal, n = self.today_stats()
        if self.demo:
            chests, cpm, goal, n = 287, 2.41, 400, 9

        # Big chest counter
        tk.Label(self.content, text=f"{chests:,}", bg=BG, fg=TEXT,
                 font=("Segoe UI Semibold", 42)).pack(anchor="w")

        pct = min(100, int(chests / goal * 100)) if goal else 0
        tk.Label(self.content, text=f"of {goal:,} goal  ({pct}%)   {self.streak_text()}",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 8))

        # CPM
        row = tk.Frame(self.content, bg=BG)
        row.pack(fill="x", pady=(0, 4))
        tk.Label(row, text="CPM", bg=BG, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(side="left")
        tk.Label(row, text=f"{cpm:.2f}", bg=BG, fg=ACCENT,
                 font=("Segoe UI Semibold", 22)).pack(side="right")

        # Clock + quit banner
        quit_min = (self.model or {}).get("quit_at")
        exit_seconds = (quit_min - 1) * 60 if quit_min else 540

        if elapsed is None:
            live = None if self.demo else in_progress_match()
            if live:
                elapsed = max(0, time.time() - live["start"])
            elif self.demo:
                elapsed = self.demo_elapsed
        if elapsed is None:
            tk.Label(self.content, text="between matches", bg=BG, fg=MUTED,
                     font=("Segoe UI", 9)).pack(anchor="w")
            shown = quit_min or 10
            tk.Label(self.content, text=f"Best exit: {shown - 1}:00 into a match",
                     bg=CARD, fg=MUTED, font=("Segoe UI", 9), justify="left").pack(fill="x",
                                                                                      padx=10, pady=(6, 10))
            self.flash_on = False
            return

        tk.Label(self.content, text=core.fmt_clock(elapsed), bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w")

        if elapsed >= exit_seconds:
            tk.Label(self.content, text="QUIT MATCH NOW\ndiminishing returns", bg=WARN_BG, fg=WARN,
                     justify="left").pack(anchor="w", padx=10, pady=(6, 10))
            self.flash_on = True
        else:
            left = int(exit_seconds - elapsed)
            tk.Label(self.content, text=f"quit window in {core.fmt_clock(left)}",
                     bg=CARD, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=10, pady=(6, 10))
            self.flash_on = False

    def render_page_1(self):
        """Page 1: Strategy - optimal quit time, chests/match, match length."""
        for w in self.content.winfo_children():
            w.destroy()

        s = self.strategy
        if not s or s.get("avg_chests_per_match", 0) == 0:
            tk.Label(self.content, text="Not enough data yet.\nPlay more matches to get recommendations.",
                     bg=BG, fg=MUTED, font=("Segoe UI", 10), justify="left").pack(anchor="w")
            return

        # Optimal quit time
        quit_at = s["quit_at"]
        per_hour = s["per_hour"]
        full_per_hour = s["full_per_hour"]

        tk.Label(self.content, text="BEST EXIT", bg=BG, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(self.content, text=f"Quit at {quit_at}:00", bg=BG, fg=ACCENT,
                 font=("Segoe UI Semibold", 20)).pack(anchor="w", pady=(0, 2))
        tk.Label(self.content, text=f"{per_hour:.0f} chests/hour  (vs {full_per_hour:.0f} playing full match)",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 12))

        # Chests per match target
        optimal = s["optimal_chests_per_match"]
        tk.Label(self.content, text="CHESTS PER MATCH TARGET", bg=BG, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(self.content, text=f"~{optimal:.0f} chests", bg=BG, fg=TEXT,
                 font=("Segoe UI Semibold", 24)).pack(anchor="w", pady=(0, 2))
        tk.Label(self.content, text=f"at {quit_at}:00 quit, based on your {s['avg_chests_per_match']:.1f} avg",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 12))

        # Match length recommendation
        tk.Label(self.content, text="MATCH LENGTH RECOMMENDATION", bg=BG, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(self.content, text=f"{quit_at} minutes", bg=BG, fg=TEXT,
                 font=("Segoe UI Semibold", 16)).pack(anchor="w", pady=(0, 2))

        diff = per_hour - full_per_hour
        if diff > 0:
            tk.Label(self.content, text=f"Quitting early gets you +{diff:.0f} chests/hour vs playing all matches out",
                     bg=BG, fg=MUTED, font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=(0, 0))
        else:
            tk.Label(self.content, text="Playing full matches is currently optimal for you",
                     bg=BG, fg=MUTED, font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=(0, 0))

    def render_page_2(self):
        """Page 2: Loadout - recommended weapons."""
        for w in self.content.winfo_children():
            w.destroy()

        # Filter weapons with enough matches
        reliable = [w for w in self.weapons.values() if w["matches"] >= 2]
        if not reliable:
            tk.Label(self.content, text="Not enough weapon data yet.\nPlay more matches with different guns.",
                     bg=BG, fg=MUTED, font=("Segoe UI", 10), justify="left").pack(anchor="w")
            return

        top = sorted(reliable, key=lambda w: w["score"], reverse=True)[:3]

        tk.Label(self.content, text="RECOMMENDED WEAPONS", bg=BG, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 6))

        for i, w in enumerate(top, 1):
            frame = tk.Frame(self.content, bg=BG)
            frame.pack(fill="x", pady=(2, 2))

            # Rank number
            tk.Label(frame, text=f"{i}.", bg=BG, fg=ACCENT,
                     font=("Segoe UI Semibold", 12)).pack(side="left", padx=(0, 4))

            # Weapon name
            tk.Label(frame, text=w["item"][:25], bg=BG, fg=TEXT,
                     font=("Segoe UI", 10, "bold")).pack(side="left", fill="x", expand=True)

            # Stats on right
            stats_text = f"K/D {w['kd']:.1f}  ·  {w['matches']} matches"
            tk.Label(frame, text=stats_text, bg=BG, fg=MUTED,
                     font=("Segoe UI", 8)).pack(side="right")

        # Note about data quality
        tk.Label(self.content, text="Weapons rated by matches looted, not kills with that gun.\nFortnite replays don't record which weapon fired which shot.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 7), justify="left").pack(anchor="w", pady=(8, 0))

    def render_page_3(self):
        """Page 3: Drop zones - recommended locations."""
        for w in self.content.winfo_children():
            w.destroy()

        reliable = [z for z in self.zones if z["visits"] >= 2]
        if not reliable:
            tk.Label(self.content, text="Not enough zone data yet.\nPlay more matches so we can find your best drops.",
                     bg=BG, fg=MUTED, font=("Segoe UI", 10), justify="left").pack(anchor="w")
            return

        top = sorted(reliable, key=lambda z: z["score"], reverse=True)[:3]

        tk.Label(self.content, text="BEST DROP ZONES", bg=BG, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 6))

        for i, z in enumerate(top, 1):
            frame = tk.Frame(self.content, bg=BG)
            frame.pack(fill="x", pady=(2, 2))

            # Zone label + name
            tk.Label(frame, text=f"{i}. ZONE {z['label']}", bg=BG, fg=ACCENT,
                     font=("Segoe UI Semibold", 10)).pack(side="left", padx=(0, 4))

            # Stats
            stats = f"{z['mine']} chests  ·  {z['per_visit']:.1f}/visit  ·  {(1-z['contested'])*100:.0f}% quiet"
            tk.Label(frame, text=stats, bg=BG, fg=MUTED,
                     font=("Segoe UI", 8)).pack(side="right")

        # Note about what "quiet" means
        tk.Label(self.content, text="Higher quiet % = fewer other players looting there.\nZones are coordinate clusters (~120m), not named POIs.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 7), justify="left").pack(anchor="w", pady=(8, 0))

    # ---------------------------------------------------------------- map page

    def _load_map_image(self):
        """Load the chest map image into a PIL Image, once."""
        if self.map_image is not None:
            return
        if not HAS_PIL:
            self.map_thumb_photo = None
            return
        try:
            self.map_image = Image.open(MAP_IMAGE_PATH)
            self.map_image = self.map_image.convert("RGB")
            # Create a small thumbnail for debugging
            self.map_thumb_photo = ImageTk.PhotoImage(
                self.map_image.resize((14, 14), Image.Resampling.LANCZOS)
            )
        except Exception as e:
            print(f"overlay: could not load map image ({type(e).__name__}: {e})")
            self.map_image = None
            self.map_thumb_photo = None

    def _world_to_map_px(self, x, y):
        """Unreal world (x, y) -> pixel position inside the map image."""
        if self.map_image is None:
            return 0.0, 0.0
        mw, mh = self.map_image.size
        fx = (float(x) - WORLD_MIN) / (WORLD_MAX - WORLD_MIN)
        fy = (WORLD_MAX - float(y)) / (WORLD_MAX - WORLD_MIN)   # Y is north-up in Unreal
        return fx * mw, fy * mh

    def _world_to_screen(self, x, y, canvas_w, canvas_h):
        """Unreal world (x, y) -> canvas screen position (image fitted and centred)."""
        px, py = self._world_to_map_px(x, y)
        return self._map_coords_to_screen(px, py, canvas_w, canvas_h)

    def _map_coords_to_screen(self, px, py, canvas_w, canvas_h):
        """
        Convert a map pixel coordinate (px, py) into canvas screen coordinates.

        Map: 1200x630. Canvas is the smaller area inside the HUD.
        We want to fit the whole map in the canvas, centered.
        """
        if self.map_image is None:
            return 0, 0
        mw, mh = self.map_image.size
        scale = min(canvas_w / mw, canvas_h / mh)
        offset_x = (canvas_w - mw * scale) / 2
        offset_y = (canvas_h - mh * scale) / 2
        return px * scale + offset_x, py * scale + offset_y

    def _screen_coords_to_map(self, sx, sy, canvas_w, canvas_h):
        """Inverse of _map_coords_to_screen."""
        if self.map_image is None:
            return 0, 0
        mw, mh = self.map_image.size
        scale = min(canvas_w / mw, canvas_h / mh)
        offset_x = (canvas_w - mw * scale) / 2
        offset_y = (canvas_h - mh * scale) / 2
        return max(0, min((sx - offset_x) / scale, mw - 1)), max(0, min((sy - offset_y) / scale, mh - 1))

    def render_page_4(self):
        """Page 4: Map - chest map with markers for your best zones and own positions."""
        for w in self.content.winfo_children():
            w.destroy()

        self._load_map_image()

        if self.map_image is None:
            tk.Label(self.content, text="Map image not found.\nPlace chest_map.webp next to the exe",
                     bg=BG, fg=MUTED, font=("Segoe UI", 10), justify="left").pack(anchor="w", pady=20)
            return

        # Create a canvas to hold the map image + markers
        canvas_width = 290
        canvas_height = 150
        self.map_canvas = tk.Canvas(self.content, width=canvas_width, height=canvas_height,
                                     bg=BG, highlightthickness=0, cursor="hand2")
        self.map_canvas.pack(fill="both", expand=True, pady=(0, 6))

        # Draw the map image centered in the canvas
        mw, mh = self.map_image.size
        scale = min(canvas_width / mw, canvas_height / mh)
        new_w, new_h = int(mw * scale), int(mh * scale)
        img_scaled = self.map_image.resize((new_w, new_h), Image.Resampling.LANCZOS)
        self.map_photo = ImageTk.PhotoImage(img_scaled)
        self.map_canvas.create_image(
            (canvas_width - new_w) / 2, (canvas_height - new_h) / 2,
            anchor="nw", image=self.map_photo
        )

        # Draw zone markers (top 3 zones)
        self._draw_zone_markers(canvas_width, canvas_height)

        # Draw own position markers (all own chest positions, if not too many)
        if len(self.all_own_positions) <= 200:
            self._draw_own_position_markers(canvas_width, canvas_height)
        else:
            # Show count of own positions as text
            count_lbl = tk.Label(self.content, text=f"{len(self.all_own_positions)} own chests mapped",
                                 bg=BG, fg=MUTED, font=("Segoe UI", 7))
            count_lbl.pack(anchor="w")

        # Legend
        tk.Label(self.content, text="Red = your best zones", bg=BG, fg=WARN,
                 font=("Segoe UI", 7)).pack(anchor="w")
        tk.Label(self.content, text="Green dots = chests you opened", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 7)).pack(anchor="w")
        tk.Label(self.content, text=f"Source: games.gg Treasure Chest map (Crossbones Isle)",
                 bg=BG, fg=MUTED, font=("Segoe UI", 6)).pack(anchor="w", pady=(2, 0))

        # Bind click on canvas to show tooltip with zone info
        self.map_canvas.bind("<Button-1>", lambda e: self._on_map_click(e, canvas_width, canvas_height))

    def _draw_zone_markers(self, canvas_w, canvas_h):
        """Draw markers for the top 3 drop zones."""
        if not self.own_zone_positions:
            return

        for z in self.own_zone_positions:
            sx, sy = self._world_to_screen(z["x"], z["y"], canvas_w, canvas_h)
            # Draw a red circle marker
            radius = 6
            self.map_canvas.create_oval(
                sx - radius, sy - radius, sx + radius, sy + radius,
                fill="#ff4d4d", outline="#ff4d4d", tags=("zone", f"zone_{z['score']:.0f}")
            )
            # Add tooltip text on hover
            self.map_canvas.tag_bind(
                f"zone_{z['score']:.0f}", "<Enter>",
                lambda e, z=z: self._show_zone_tooltip(z)
            )
            self.map_canvas.tag_bind(
                f"zone_{z['score']:.0f}", "<Leave>",
                lambda e: self._hide_tooltip()
            )

    def _draw_own_position_markers(self, canvas_w, canvas_h):
        """Draw small green dots for each own chest position."""
        max_markers = 100
        step = max(1, len(self.all_own_positions) // max_markers)

        for i, (px, py) in enumerate(self.all_own_positions):
            if i % step != 0 and i != len(self.all_own_positions) - 1:
                continue
            sx, sy = self._world_to_screen(px, py, canvas_w, canvas_h)
            self.map_canvas.create_oval(
                sx - 2, sy - 2, sx + 2, sy + 2,
                fill="#39d98a", outline="#39d98a", tags="own_pos"
            )

    def _on_map_click(self, event, canvas_w, canvas_h):
        """Handle click on map canvas - could show popup with zone info."""
        mx, my = self._screen_coords_to_map(event.x, event.y, canvas_w, canvas_h)
        # Find nearest zone
        nearest = None
        min_dist = float("inf")
        for z in self.own_zone_positions:
            sx, sy = self._world_to_screen(z["x"], z["y"], canvas_w, canvas_h)
            dist = ((event.x - sx)**2 + (event.y - sy)**2) ** 0.5
            if dist < min_dist and dist < 20:
                min_dist = dist
                nearest = z

        if nearest:
            self._show_zone_tooltip(nearest)
            # Flash the marker
            self.map_canvas.itemconfig(
                f"zone_{nearest['score']:.0f}", fill="#39d98a", outline="#39d98a"
            )
            self.root.after(800, lambda: self.map_canvas.itemconfig(
                f"zone_{nearest['score']:.0f}", fill="#ff4d4d", outline="#ff4d4d"
            ))

    def _show_zone_tooltip(self, zone):
        """Show a tooltip with zone info."""
        # Remove old tooltip if any
        self._hide_tooltip()

        tooltip = tk.Toplevel(self.root)
        tooltip.wm_overrideredirect(True)
        tooltip.configure(bg=BG)
        tooltip.attributes("-topmost", True)

        # Position near the marker (approximate)
        tooltip.geometry(f"+{self.root.winfo_x() + 10}+{self.root.winfo_y() + 10}")

        text = (
            f"Zone {zone.get('label', '?')}  (score {zone['score']:.1f})\n"
            f"{zone['mine']} chests · {zone['per_visit']:.1f}/visit\n"
            f"{(1-zone['contested'])*100:.0f}% quiet"
        )
        lbl = tk.Label(tooltip, text=text, bg=BG, fg=TEXT, font=("Segoe UI", 8),
                       justify="left", padx=8, pady=4)
        lbl.pack()

        self.hovered_marker = tooltip
        self.root.after(2000, lambda: self._hide_tooltip())

    def _hide_tooltip(self):
        """Remove the tooltip if shown."""
        if self.hovered_marker:
            try:
                self.hovered_marker.destroy()
            except Exception:
                pass
            self.hovered_marker = None

    def render(self, elapsed=None):
        """Render the current page."""
        self._page_header()
        self._page_footer()

        # Clear content and render current page
        for w in self.content.winfo_children():
            w.destroy()

        if self.page == 0:
            self.render_page_0(elapsed)
        elif self.page == 1:
            self.render_page_1()
        elif self.page == 2:
            self.render_page_2()
        elif self.page == 3:
            self.render_page_3()
        elif self.page == 4:
            self.render_page_4()

        # Flash effect for quit warning (only on page 0)
        if self.page == 0 and self.flash_on:
            colour = WARN if int(time.time() * 2) % 2 else WARN_BG
            for w in self.content.winfo_children():
                if w.cget("bg") == CARD:  # only flash the banner-like elements
                    w.config(bg=colour)

    def streak_text(self):
        try:
            days = core.streak(self.data)
            return f"{days}d streak" if days else ""
        except Exception:
            return ""

    # ---------------------------------------------------------------- loop
    def tick(self):
        if self.quitting:
            return
        try:
            if time.time() - self.last_data_check > 5:
                self.reload()                       # left column refreshes after each match
            else:
                # Update clock on page 0 if a match is in progress
                if self.page == 0:
                    live = in_progress_match()
                    if live:
                        elapsed = max(0, time.time() - live["start"])
                        self.render_page_0(elapsed)
                    else:
                        self.render_page_0(None)
                # Auto-cycle pages every 8 seconds
                if time.time() - self.last_page_switch > 8:
                    self.switch_page(1)
            # Flash the whole card between two reds; otherwise keep the normal background.
            if self.flash_on and self.page == 0:
                colour = WARN if int(time.time() * 2) % 2 else WARN_BG
                for w in (self.frame, self.lbl_page_name) + tuple(self.content.winfo_children()):
                    w.config(bg=colour)
        except Exception as e:                      # the HUD must survive anything
            print(f"overlay: tick failed ({type(e).__name__}: {e})")
        self.demo_elapsed += 25 if self.demo else 0     # demo clock: 1s on screen = 100s in match
        self.root.after(1000 if not self.demo else 250, self.tick)


def main():
    ap = argparse.ArgumentParser(description="Fortnite grind HUD.")
    ap.add_argument("--demo", action="store_true", help="fake numbers, fast clock")
    ap.add_argument("--list-monitors", action="store_true", help="print monitor geometry")
    args = ap.parse_args()

    if args.list_monitors:
        vx, vy, vw, vh, primary = monitor_geometry()
        print(f"virtual screen: {vw}x{vh} at ({vx},{vy}); primary width {primary}")
        print("second monitor detected" if vw > primary + 200 else "single monitor")
        return 0

    root = tk.Tk()
    hud = Overlay(root, demo=args.demo)
    root.protocol("WM_DELETE_WINDOW", hud.close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
