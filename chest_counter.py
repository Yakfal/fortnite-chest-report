"""
Fortnite Chest Counter - desktop app.

Counts every chest YOU open in Fortnite (normal / epic / sprite), tracks a daily goal with
a streak, and shows your Pirate Shop Access Token odds. All the replay reading, match
analysis and storage lives in fc_core.py, which the suite modules share.

Run with --scan to print the totals in the console instead of opening the window.
"""

import datetime as dt
import os
import queue
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import ttk, messagebox

from fc_core import (BUCKETS, DAYS, DEFAULT_GOAL, DEMOS, EVENT_END, MONTHS, READER,
                     TOKEN_MODELS, Worker, chance, daily_totals, fmt_chance, fmt_int,
                     known_types, load_data, save_data, scan_cli, streak, totals)
import insights

BG, PANEL, PANEL2 = "#080d20", "#111c3d", "#16234b"
LINE, INK, MUTED = "#26345f", "#eef2ff", "#8d9ac9"
GOLD, SKY, PURPLE, GREEN, WARN = "#ffc93c", "#4fd6ff", "#b98cff", "#4ce6a1", "#ff9f6b"

BUCKET_LABEL = {"normal": "NORMAL", "epic": "EPIC", "sprite": "SPRITE"}
BUCKET_COLOR = {"normal": SKY, "epic": PURPLE, "sprite": GREEN}


def bucket(tipo):
    from fc_core import bucket as _bucket
    return _bucket(tipo)


def day_label(d, today):
    if d == today:
        return "TODAY"
    if d == today + dt.timedelta(days=1):
        return "TOMORROW"
    return DAYS[d.weekday()].upper()


# ---------------------------------------------------------------- UI helpers

class UI:
    scale = 1.0
    family = "Segoe UI"

    @classmethod
    def px(cls, n):
        return int(round(n * cls.scale))

    @classmethod
    def font(cls, size, weight="normal", family=None):
        return (family or cls.family, size, weight)


def panel(master, accent=None):
    """A flat panel with an optional accent strip down its left edge."""
    outer = tk.Frame(master, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
    if accent:
        tk.Frame(outer, bg=accent, width=UI.px(3)).pack(side="left", fill="y")
    body = tk.Frame(outer, bg=PANEL)
    body.pack(side="left", fill="both", expand=True, padx=UI.px(12), pady=UI.px(10))
    outer.body = body
    return outer


class App:
    def __init__(self, root):
        self.root = root
        self.data = load_data()
        self.events = queue.Queue()
        self.shown_total = 0
        self.anim = None
        self.chips = []
        UI.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        UI.family = self.pick_font()
        self.black = self.pick_font(["Segoe UI Black", "Arial Black"])
        root.configure(bg=BG)
        root.title("Fortnite Chest Counter")
        self.style()
        self.build()
        self.worker = Worker(self.data, self.events)
        self.worker.start()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        root.after(150, self.pump)
        root.after(30000, self.minute)

    # ---------------------------------------------------------------- setup
    def pick_font(self, wanted=("Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans")):
        fams = {f.lower(): f for f in tkfont.families(self.root)}
        for w in wanted:
            if w.lower() in fams:
                return fams[w.lower()]
        return "TkDefaultFont"

    def style(self):
        px = UI.px
        s = ttk.Style(self.root)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure("TButton", background=PANEL2, foreground=INK, bordercolor=LINE,
                    lightcolor=PANEL2, darkcolor=PANEL2, focusthickness=0, padding=(px(11), px(6)),
                    font=UI.font(10))
        s.map("TButton", background=[("active", LINE)])
        s.configure("Tiny.TButton", padding=(px(7), px(1)), font=UI.font(10, "bold"))
        s.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=INK,
                    rowheight=px(25), bordercolor=LINE, font=UI.font(10))
        s.configure("Treeview.Heading", background=PANEL, foreground=MUTED,
                    font=UI.font(8, "bold"), relief="flat", padding=(px(6), px(4)))
        s.map("Treeview", background=[("selected", LINE)])
        s.configure("Vertical.TScrollbar", background=LINE, troughcolor=PANEL, bordercolor=PANEL,
                    arrowcolor=MUTED, gripcount=0)

    def build(self):
        px = UI.px
        m = tk.Frame(self.root, bg=BG)
        m.pack(fill="both", expand=True, padx=px(18), pady=px(14))

        # header ---------------------------------------------------------
        head = tk.Frame(m, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="FORTNITE", bg=BG, fg=GOLD,
                 font=UI.font(15, "bold", self.black)).pack(side="left")
        tk.Label(head, text=" CHEST COUNTER", bg=BG, fg=INK,
                 font=UI.font(15, "bold", self.black)).pack(side="left")
        self.lbl_state = tk.Label(head, text="", bg=BG, fg=MUTED, font=UI.font(9, "bold"))
        self.lbl_state.pack(side="right")

        # hero: total + today's ring --------------------------------------
        hero = tk.Frame(m, bg=BG)
        hero.pack(fill="x", pady=(px(10), 0))
        left = tk.Frame(hero, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        tk.Label(left, text="CHESTS OPENED", bg=BG, fg=MUTED,
                 font=UI.font(9, "bold")).pack(anchor="w")
        self.lbl_total = tk.Label(left, text="0", bg=BG, fg=GOLD,
                                  font=UI.font(52, "bold", self.black))
        self.lbl_total.pack(anchor="w")
        self.lbl_sub = tk.Label(left, text="", bg=BG, fg=MUTED, font=UI.font(9))
        self.lbl_sub.pack(anchor="w")

        self.ring = tk.Canvas(hero, width=px(120), height=px(120), bg=BG, highlightthickness=0)
        self.ring.pack(side="right")
        self.ring.bind("<Configure>", lambda e: self.draw_ring())

        # chest kinds -----------------------------------------------------
        cards = tk.Frame(m, bg=BG)
        cards.pack(fill="x", pady=(px(8), 0))
        self.cards = {}
        for i, b in enumerate(BUCKETS):
            cards.columnconfigure(i, weight=1, uniform="c")
            p = panel(cards, accent=BUCKET_COLOR[b])
            p.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else px(5), 0))
            tk.Label(p.body, text=BUCKET_LABEL[b], bg=PANEL, fg=BUCKET_COLOR[b],
                     font=UI.font(8, "bold")).pack(anchor="w")
            num = tk.Label(p.body, text="0", bg=PANEL, fg=INK,
                           font=UI.font(22, "bold", self.black))
            num.pack(anchor="w")
            self.cards[b] = num

        # daily goals -----------------------------------------------------
        goals = tk.Frame(m, bg=BG)
        goals.pack(fill="x", pady=(px(12), px(4)))
        tk.Label(goals, text="DAILY GOAL", bg=BG, fg=INK,
                 font=UI.font(10, "bold")).pack(side="left")
        self.lbl_streak = tk.Label(goals, text="", bg=BG, fg=GREEN, font=UI.font(9, "bold"))
        self.lbl_streak.pack(side="left", padx=(px(10), 0))
        ttk.Button(goals, text="+", style="Tiny.TButton", width=2,
                   command=lambda: self.change_goal(25)).pack(side="right")
        self.lbl_goal = tk.Label(goals, text="", bg=BG, fg=GOLD,
                                 font=UI.font(11, "bold", self.black))
        self.lbl_goal.pack(side="right", padx=px(8))
        ttk.Button(goals, text="\u2212", style="Tiny.TButton", width=2,
                   command=lambda: self.change_goal(-25)).pack(side="right")

        chips = tk.Frame(m, bg=BG)
        chips.pack(fill="x", pady=(px(2), 0))
        for i in range(7):
            chips.columnconfigure(i, weight=1, uniform="chip")
            c = tk.Frame(chips, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
            c.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else px(4), 0))
            top = tk.Label(c, text="", bg=PANEL, fg=MUTED, font=UI.font(7, "bold"))
            top.pack(anchor="w", padx=px(7), pady=(px(5), 0))
            day = tk.Label(c, text="", bg=PANEL, fg=INK, font=UI.font(10, "bold"))
            day.pack(anchor="w", padx=px(7))
            tgt = tk.Label(c, text="", bg=PANEL, fg=MUTED, font=UI.font(8))
            tgt.pack(anchor="w", padx=px(7), pady=(0, px(6)))
            self.chips.append({"frame": c, "top": top, "day": day, "tgt": tgt})

        # insights at a glance ---------------------------------------------
        quick = tk.Frame(m, bg=BG)
        quick.pack(fill="x", pady=(px(10), 0))
        self.insight_cards = {}
        for i, (key, title, accent) in enumerate((
                ("gun", "BEST GUN", GOLD),
                ("zone", "BEST DROP ZONE", SKY),
                ("quit", "QUIT A MATCH AT", PURPLE))):
            quick.columnconfigure(i, weight=1, uniform="ins")
            p = panel(quick, accent=accent)
            p.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else px(5), 0))
            tk.Label(p.body, text=title, bg=PANEL, fg=accent,
                     font=UI.font(8, "bold")).pack(anchor="w")
            value = tk.Label(p.body, text="—", bg=PANEL, fg=INK,
                             font=UI.font(13, "bold", self.black), anchor="w")
            value.pack(anchor="w", fill="x")
            sub = tk.Label(p.body, text="", bg=PANEL, fg=MUTED,
                           font=UI.font(8), anchor="w")
            sub.pack(anchor="w", fill="x")
            self.insight_cards[key] = (value, sub)

        # token odds ------------------------------------------------------
        tok = panel(m, accent=GOLD)
        tok.pack(fill="x", pady=(px(10), 0))
        row = tk.Frame(tok.body, bg=PANEL)
        row.pack(fill="x")
        tk.Label(row, text="PIRATE SHOP ACCESS TOKEN", bg=PANEL, fg=GOLD,
                 font=UI.font(9, "bold")).pack(side="left")
        self.lbl_event = tk.Label(row, text="", bg=PANEL, fg=MUTED, font=UI.font(8))
        self.lbl_event.pack(side="right")
        self.lbl_token_head = tk.Label(tok.body, text="", bg=PANEL, fg=INK, font=UI.font(9))
        self.lbl_token_head.pack(anchor="w", pady=(px(2), px(4)))
        grid = tk.Frame(tok.body, bg=PANEL)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(2, weight=1)
        tk.Label(grid, text="", bg=PANEL, fg=MUTED, font=UI.font(7, "bold")).grid(row=0, column=0)
        for j, t in enumerate(("CHANCE SO FAR", "IF YOU HIT THE GOAL")):
            tk.Label(grid, text=t, bg=PANEL, fg=MUTED, font=UI.font(7, "bold")).grid(
                row=0, column=j + 1, sticky="e", padx=(px(8), 0))
        self.token_rows = []
        for i, (name, _, pretty) in enumerate(TOKEN_MODELS, start=1):
            tk.Label(grid, text=f"{name}  ({pretty})", bg=PANEL, fg=INK,
                     font=UI.font(9)).grid(row=i, column=0, sticky="w", pady=(px(2), 0))
            now = tk.Label(grid, text="", bg=PANEL, fg=SKY, font=UI.font(10, "bold"))
            now.grid(row=i, column=1, sticky="e", padx=(px(8), 0))
            later = tk.Label(grid, text="", bg=PANEL, fg=GOLD, font=UI.font(10, "bold"))
            later.grid(row=i, column=2, sticky="e", padx=(px(8), 0))
            self.token_rows.append((now, later))
        self.lbl_token_foot = tk.Label(tok.body, text="", bg=PANEL, fg=MUTED,
                                       font=UI.font(8), justify="left", anchor="w",
                                       wraplength=UI.px(600))
        self.lbl_token_foot.pack(fill="x", pady=(px(4), 0))

        # history chart ---------------------------------------------------
        tk.Label(m, text="LAST 14 DAYS", bg=BG, fg=INK, font=UI.font(10, "bold")).pack(
            anchor="w", pady=(px(12), px(2)))
        self.chart = tk.Canvas(m, height=px(78), bg=PANEL, highlightthickness=1,
                               highlightbackground=LINE)
        self.chart.pack(fill="x")
        self.chart.bind("<Configure>", lambda e: self.draw_chart())

        # actions, pinned to the bottom so a short screen never clips them -----
        self.lbl_info = tk.Label(m, text="", bg=BG, fg=MUTED, font=UI.font(8),
                                 justify="center", wraplength=UI.px(640))
        self.lbl_info.pack(side="bottom", fill="x", pady=(px(6), 0))
        btns = tk.Frame(m, bg=BG)
        btns.pack(side="bottom", fill="x", pady=(px(10), 0))
        for i in range(5):
            btns.columnconfigure(i, weight=1, uniform="b")
        ttk.Button(btns, text="Rescan  (F5)", command=self.rescan).grid(
            row=0, column=0, sticky="ew", padx=(0, px(3)))
        ttk.Button(btns, text="Insights", command=self.show_insights).grid(
            row=0, column=1, sticky="ew", padx=px(3))
        ttk.Button(btns, text="Export Excel", command=self.export_excel).grid(
            row=0, column=2, sticky="ew", padx=px(3))
        ttk.Button(btns, text="Replays", command=self.open_demos).grid(
            row=0, column=3, sticky="ew", padx=px(3))
        ttk.Button(btns, text="Chest types", command=self.show_types).grid(
            row=0, column=4, sticky="ew", padx=(px(3), 0))

        # matches ---------------------------------------------------------
        tk.Label(m, text="MATCHES  ·  double-click one for its chests", bg=BG, fg=INK,
                 font=UI.font(10, "bold")).pack(anchor="w", pady=(px(12), px(3)))
        tf = tk.Frame(m, bg=BG)
        tf.pack(fill="both", expand=True)
        cols = ("when", "normal", "epic", "sprite", "total")
        heads = ("MATCH", "NORMAL", "EPIC", "SPRITE", "TOTAL")
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", height=5)
        for c, h in zip(cols, heads):
            self.tree.heading(c, text=h, anchor="w" if c == "when" else "e")
            self.tree.column(c, width=UI.px(210 if c == "when" else 78),
                             anchor="w" if c == "when" else "e", stretch=(c == "when"))
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.match_detail)
        self.root.bind("<F5>", lambda e: self.rescan())

        self.root.update_idletasks()
        w = UI.px(700)
        h = min(UI.px(960), int(self.root.winfo_screenheight() * 0.95))
        self.root.geometry(f"{w}x{h}")
        self.root.minsize(UI.px(620), min(UI.px(700), h))

    # ---------------------------------------------------------------- actions
    def rescan(self):
        self.worker.wake.set()
        self.lbl_info.config(text="Looking for new matches…", fg=MUTED)

    def goal(self):
        try:
            return max(1, int(self.data["settings"].get("daily_goal") or DEFAULT_GOAL))
        except (TypeError, ValueError):
            return DEFAULT_GOAL

    def change_goal(self, delta):
        goal = max(25, min(5000, self.goal() + delta))
        self.data["settings"]["daily_goal"] = goal
        save_data(self.data)
        self.refresh()

    def open_demos(self):
        if DEMOS.is_dir():
            os.startfile(DEMOS)                      # noqa: S606 - Windows shell open
        else:
            messagebox.showinfo(
                "Fortnite Chest Counter",
                f"The replays folder does not exist yet:\n\n{DEMOS}\n\n"
                "In Fortnite turn on: Settings > Game > Replays > Record Replays "
                "(Battle Royale), then play a match.", parent=self.root)

    def match_detail(self, event=None):
        sel = self.tree.focus()
        rec = self.data["replays"].get(sel)
        if not rec or not rec.get("ok"):
            return
        win = tk.Toplevel(self.root)
        win.title(sel)
        win.configure(bg=BG)
        win.geometry(f"{UI.px(380)}x{UI.px(460)}")
        head = tk.Frame(win, bg=BG)
        head.pack(fill="x", padx=UI.px(16), pady=(UI.px(14), UI.px(6)))
        tk.Label(head, text=rec.get("when", ""), bg=BG, fg=INK,
                 font=UI.font(12, "bold", self.black)).pack(anchor="w")
        c = rec["counts"]
        tk.Label(head, text=f"{rec['total']} chests   ·   {c['normal']} normal   ·   "
                            f"{c['epic']} epic   ·   {c['sprite']} sprite",
                 bg=BG, fg=MUTED, font=UI.font(9)).pack(anchor="w")
        txt = tk.Text(win, bg=PANEL, fg=INK, relief="flat", highlightthickness=0,
                      font=("Consolas", 9), padx=UI.px(10), pady=UI.px(8))
        lines = []
        for i, ch in enumerate(rec.get("chests") or [], 1):
            mm, ss = divmod(ch["t"], 60)
            tag = " (broken)" if ch.get("destroyed") else ""
            lines.append(f"{i:2d}.  {mm:02d}:{ss:02d}   {ch['kind'].upper():6s} "
                         f"{ch['tipo']}{tag}")
        txt.insert("1.0", "\n".join(lines) or "No chests in this match.")
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True, padx=UI.px(16), pady=(0, UI.px(12)))
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, UI.px(14)))
        win.bind("<Escape>", lambda e: win.destroy())

    def show_types(self):
        tipos = known_types()
        win = tk.Toplevel(self.root)
        win.title("Chest types counted")
        win.configure(bg=BG)
        win.geometry(f"{UI.px(640)}x{UI.px(430)}")
        tk.Label(win, text="CHEST CLASSES THE COUNTER RECOGNISES", bg=BG, fg=INK,
                 font=UI.font(11, "bold")).pack(anchor="w", padx=UI.px(16),
                                                pady=(UI.px(14), UI.px(2)))
        tk.Label(win, text="New ones are added automatically the first time a match contains them.",
                 bg=BG, fg=MUTED, font=UI.font(9)).pack(anchor="w", padx=UI.px(16))
        txt = tk.Text(win, bg=PANEL, fg=MUTED, relief="flat", highlightthickness=0,
                      font=("Consolas", 9), padx=UI.px(8), pady=UI.px(8))
        txt.insert("1.0", "\n".join(f"[{bucket(t.split('.')[-1][:-2]).upper()}] {t}"
                                    for t in tipos) or "(none yet — play a match)")
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True, padx=UI.px(16), pady=UI.px(12))
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, UI.px(14)))
        win.bind("<Escape>", lambda e: win.destroy())

    def show_insights(self):
        """A tabbed window with the short-answer report for each topic."""
        win = tk.Toplevel(self.root)
        win.title("Insights")
        win.configure(bg=BG)
        win.geometry(f"{UI.px(760)}x{UI.px(560)}")
        head = tk.Frame(win, bg=BG)
        head.pack(fill="x", padx=UI.px(16), pady=(UI.px(12), UI.px(4)))
        tk.Label(head, text="WHAT YOUR REPLAYS SAY", bg=BG, fg=GOLD,
                 font=UI.font(13, "bold", self.black)).pack(anchor="w")
        tk.Label(head, text="Weapons, drop zones, quit time and modes - all from your own matches.",
                 bg=BG, fg=MUTED, font=UI.font(9)).pack(anchor="w")
        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=UI.px(16), pady=UI.px(8))
        try:
            texts = insights.sections(self.data)
        except Exception as e:
            texts = {"Overview": f"Could not build insights:\n{type(e).__name__}: {e}\n"}
        for title, text in texts.items():
            frame = tk.Frame(nb, bg=PANEL)
            nb.add(frame, text=title)
            body = tk.Text(frame, bg=PANEL, fg=INK, relief="flat", highlightthickness=0,
                           font=("Consolas", 9), padx=UI.px(12), pady=UI.px(10),
                           wrap="word")
            body.insert("1.0", text)
            body.configure(state="disabled")
            sb = ttk.Scrollbar(frame, orient="vertical", command=body.yview)
            body.configure(yscrollcommand=sb.set)
            body.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
        foot = tk.Frame(win, bg=BG)
        foot.pack(fill="x", padx=UI.px(16), pady=(0, UI.px(12)))
        ttk.Button(foot, text="Export Excel", command=self.export_excel).pack(side="left")
        ttk.Button(foot, text="Close", command=win.destroy).pack(side="right")
        win.bind("<Escape>", lambda e: win.destroy())

    def export_excel(self):
        """Write the shareable workbook beside the app and offer to open it."""
        try:
            import excel_report
            path = excel_report.build(self.data)
        except Exception as e:
            messagebox.showerror("Export Excel",
                                 f"Could not write the workbook:\n\n{type(e).__name__}: {e}",
                                 parent=self.root)
            return
        self.lbl_info.config(text=f"Excel report written: {path}", fg=GREEN)
        if messagebox.askyesno("Export Excel",
                               f"Workbook written to:\n\n{path}\n\nOpen it now?",
                               parent=self.root):
            try:
                os.startfile(path)              # noqa: S606 - Windows shell open
            except OSError:
                pass

    def close(self):
        self.worker.stop_flag.set()
        self.root.destroy()

    # ---------------------------------------------------------------- events
    def pump(self):
        redraw = False
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "status":
                    self.status(*data)
                elif kind == "replay":
                    redraw = True
        except queue.Empty:
            pass
        if redraw:
            self.refresh(flash=True)
        self.root.after(300, self.pump)

    def status(self, kind, data):
        color, label, text = MUTED, "", ""
        if kind == "reading":
            color, label = GOLD, "Reading"
            text = f"Reading replays… {data[0]}/{data[1]}"
        elif kind == "watching":
            color, label = GREEN, "Watching"
            text = (f"{data} matches counted. New matches are added about a minute "
                    "after they end.")
        elif kind == "noreplays":
            color, label = WARN, "No replays"
            text = ("No replays found. In Fortnite turn on Settings > Game > Replays > "
                    "Record Replays (Battle Royale).")
        elif kind == "error":
            color, label, text = WARN, "Error", str(data)[:300]
        self.lbl_state.config(text="● " + label if label else "", fg=color)
        self.lbl_info.config(text=text, fg=WARN if color == WARN else MUTED)

    def minute(self):
        self.refresh()
        self.root.after(30000, self.minute)

    # ---------------------------------------------------------------- drawing
    def draw_ring(self, today_n=None, goal=None):
        px = UI.px
        cv = self.ring
        cv.delete("all")
        size = min(cv.winfo_width() or px(120), cv.winfo_height() or px(120))
        if size < 30:
            return
        if today_n is None:
            today_n = self._today_n
            goal = self._goal_now
        frac = 0 if goal <= 0 else min(1.0, today_n / goal)
        pad = px(9)
        box = (pad, pad, size - pad, size - pad)
        width = max(6, px(11))
        cv.create_oval(*box, outline=PANEL2, width=width)
        if frac > 0:
            cv.create_arc(*box, start=90, extent=-359.9 * frac, style="arc",
                          outline=GOLD, width=width)
        cv.create_text(size / 2, size / 2 - px(9), text=fmt_int(today_n),
                       fill=INK, font=UI.font(21, "bold", self.black))
        cv.create_text(size / 2, size / 2 + px(14), text=f"of {fmt_int(goal)} today",
                       fill=MUTED, font=UI.font(8))

    def draw_chart(self, days=None, goal=None):
        px = UI.px
        cv = self.chart
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 60 or h < 30:
            return
        if days is None:
            days = self._days
            goal = self._goal_now
        today = dt.date.today()
        seq = [today - dt.timedelta(days=i) for i in range(13, -1, -1)]
        vals = [days.get(d.isoformat(), 0) for d in seq]
        mx = max(vals + [goal, 1])
        pad_x, top, bottom = px(8), px(12), px(16)
        slot = (w - 2 * pad_x) / 14
        bw = slot * 0.58
        for i, (d, v) in enumerate(zip(seq, vals)):
            cx = pad_x + slot * i + slot / 2
            bh = (h - top - bottom) * v / mx
            met = goal > 0 and v >= goal
            color = GOLD if i == 13 else (GREEN if met else "#3d6fd6")
            if v:
                cv.create_rectangle(cx - bw / 2, h - bottom - bh, cx + bw / 2, h - bottom,
                                    fill=color, width=0)
            cv.create_text(cx, h - px(7), text=str(d.day), fill=MUTED, font=UI.font(7))
        # goal line
        gy = h - bottom - (h - top - bottom) * goal / mx
        cv.create_line(pad_x, gy, w - pad_x, gy, fill=GOLD, dash=(3, 3), width=1)
        cv.create_text(w - pad_x, gy - px(7), text=f"goal {fmt_int(goal)}", anchor="e",
                       fill=GOLD, font=UI.font(7, "bold"))

    def update_insights(self):
        """Fill the three summary cards: best gun, best zone, quit time."""
        try:
            b = insights.build(self.data)
        except Exception:
            return
        gun_lbl, gun_sub = self.insight_cards["gun"]
        zone_lbl, zone_sub = self.insight_cards["zone"]
        quit_lbl, quit_sub = self.insight_cards["quit"]

        gun = b["weapons"][0] if b["weapons"] else None
        if gun:
            name = gun["item"]
            gun_lbl.config(text=name if len(name) <= 24 else name[:23] + "…")
            gun_sub.config(text=f"K/D {gun['kd']:.2f}  ·  win {gun['win_rate'] * 100:.0f}%"
                                f"  ·  {gun['matches']} matches")
        else:
            gun_lbl.config(text="—")
            gun_sub.config(text="play a match to rank weapons")

        zone = b["best_zones"][0] if b["best_zones"] else None
        if zone:
            zone_lbl.config(text=f"ZONE {zone['zone']}")
            zone_sub.config(text=f"{zone['per_visit']:.1f}/visit  ·  {zone['quiet']:.0f}% quiet"
                                 f"  ·  {zone['chests']} chests")
        else:
            zone_lbl.config(text="—")
            zone_sub.config(text="no chest positions yet")

        q = b["quit"]
        if q.get("quit_at"):
            quit_lbl.config(text=q["quit_clock"])
            quit_sub.config(text=f"{q['per_hour']:.0f}/h vs {q['full_per_hour']:.0f} full"
                                 f"  ·  {q['advice']}")
        else:
            quit_lbl.config(text="—")
            quit_sub.config(text="not enough chest timings")

    def animate(self, target):
        if self.anim:
            self.root.after_cancel(self.anim)
            self.anim = None
        start = self.shown_total
        if start == target:
            return
        steps = 9

        def step(i):
            self.shown_total = int(start + (target - start) * i / steps)
            self.lbl_total.config(text=fmt_int(self.shown_total))
            if i < steps:
                self.anim = self.root.after(28, lambda: step(i + 1))
            else:
                self.shown_total = target
                self.lbl_total.config(text=fmt_int(target))
                self.anim = None

        step(0)

    def refresh(self, flash=False):
        goal = self.goal()
        t = totals(self.data)
        total = sum(t.values())
        days = daily_totals(self.data)
        today = dt.date.today()
        today_n = days.get(today.isoformat(), 0)
        self._today_n, self._goal_now, self._days = today_n, goal, days

        self.lbl_goal.config(text=fmt_int(goal))
        self.animate(total)
        for b in BUCKETS:
            self.cards[b].config(text=fmt_int(t[b]))
        if flash:
            self.lbl_total.config(fg="#ffffff")
            self.root.after(260, lambda: self.lbl_total.config(fg=GOLD))

        ok = [r for r in self.data["replays"].values() if r.get("ok")]
        if ok:
            last = max(ok, key=lambda r: r.get("when", ""))
            self.lbl_sub.config(
                text=f"{len(ok)} matches  ·  last ({last['when'][-5:]}): {last['total']} chests")
        else:
            self.lbl_sub.config(text="No matches counted yet")

        st = streak(days, goal, today)
        self.lbl_streak.config(text=f"{st}-DAY STREAK" if st else "", fg=GREEN)

        self.draw_ring(today_n, goal)
        self.draw_chart(days, goal)
        self.update_insights()

        # seven day chips: today and the six after it
        for i, chip in enumerate(self.chips):
            d = today + dt.timedelta(days=i)
            have = days.get(d.isoformat(), 0)
            chip["top"].config(text=day_label(d, today),
                               fg=GOLD if i == 0 else MUTED)
            chip["day"].config(text=f"{d.day} {MONTHS[d.month - 1]}")
            if i == 0:
                chip["tgt"].config(text=f"{fmt_int(have)} / {fmt_int(goal)}",
                                   fg=GREEN if have >= goal else INK)
            else:
                chip["tgt"].config(text=f"goal {fmt_int(goal)}")
            met = have >= goal and i > 0
            chip["frame"].config(highlightbackground=GOLD if i == 0 else
                                 (GREEN if met else LINE))

        # token odds
        left = max(0, (EVENT_END - today).days)
        projected = total + goal * left
        self.lbl_event.config(text=f"ends {EVENT_END.day} {MONTHS[EVENT_END.month - 1]} "
                                   f"{EVENT_END.year}  ·  {left} days left")
        self.lbl_token_head.config(
            text=f"{fmt_int(total)} chests counted  →  {fmt_int(projected)} by the end "
                 f"at {fmt_int(goal)}/day")
        for (now_lbl, later_lbl), (_, p, _) in zip(self.token_rows, TOKEN_MODELS):
            now_lbl.config(text=fmt_chance(chance(total, p)))
            later_lbl.config(text=fmt_chance(chance(projected, p)))
        if total < 1:
            self.lbl_token_foot.config(
                text="Treat the token as a lottery ticket. The daily goal is what you control.")
        else:
            expected = total * TOKEN_MODELS[0][1]
            shown = f"{expected:.4f}" if expected < 0.1 else f"{expected:.2f}"
            self.lbl_token_foot.config(
                text=f"Expected tokens so far: {shown} at the community rate. "
                     "The odds use only the chests you opened yourself.")

        # matches table
        self.tree.delete(*self.tree.get_children())
        for name, r in sorted(self.data["replays"].items(),
                              key=lambda kv: kv[1].get("when", ""), reverse=True):
            if not r.get("ok"):
                self.tree.insert("", "end", iid=name,
                                 values=(f"{r.get('when', name)}  (unreadable)",
                                         "—", "—", "—", "—"))
                continue
            c = r["counts"]
            self.tree.insert("", "end", iid=name,
                             values=(r.get("when", name), c["normal"], c["epic"],
                                     c["sprite"], r["total"]))


_MUTEX = None


def single_instance():
    """One window per user, so two copies can never overwrite each other's counts."""
    global _MUTEX
    try:
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _MUTEX = k32.CreateMutexW(None, False, "Local\\FortniteChestCounter")
        return not (not _MUTEX or ctypes.get_last_error() == 183)   # 183 = already exists
    except Exception:
        return True


def main():
    if "--scan" in sys.argv:
        return scan_cli()
    if not READER.exists():
        raise SystemExit(f"Missing replay reader: {READER}")
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    if not single_instance():
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("Fortnite Chest Counter",
                            "The chest counter is already open.\n\n"
                            "Check the taskbar and close it before starting another one.")
        root.destroy()
        return 1
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
