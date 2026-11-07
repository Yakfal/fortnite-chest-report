"""
onboarding.py - first-run welcome screen.

Shows once, before the tracker, and explains the one setting the app cannot work without:
recorded replays. After "Start Tracker" it launches the HUD and remembers that it has been
seen (data.json -> settings.onboarded).

Design notes, deliberately unlike a stock Tk dialog:
  * a dark card with an accent rule instead of grey system chrome
  * three numbered step chips drawn with Frames, not a text list
  * a footer showing where replays will be read from, so the path is never a mystery

Usage:
    python onboarding.py            # show it standalone
"""

import tkinter as tk
from pathlib import Path

import fc_core as core

BG = "#0a0e13"
CARD = "#101720"
LINE = "#1d2733"
TEXT = "#e8eef5"
MUTED = "#7f8f9f"
ACCENT = "#39d98a"

STEPS = (
    ("1", "Open Fortnite Settings", "Gear icon in the top-right corner of the lobby."),
    ("2", "Go to the Game tab", "The Game tab holds graphics, audio and replay options."),
    ("3", "Turn on Record Replays", "Scroll to Replays and switch Record Replays to ON."),
)


class Onboarding:
    """The welcome window. `on_start` is called after it closes."""

    def __init__(self, root, on_start=None):
        self.root = root
        self.on_start = on_start
        self.done = False

        root.title("Welcome to the Fortnite Grind Tracker")
        root.configure(bg=BG)
        root.resizable(False, False)
        width, height = 560, 546
        root.geometry(f"{width}x{height}+{max(0, (root.winfo_screenwidth() - width) // 2)}"
                      f"+{max(0, (root.winfo_screenheight() - height) // 3)}")

        card = tk.Frame(root, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="both", expand=True, padx=18, pady=18)

        # accent rule across the top of the card
        tk.Frame(card, bg=ACCENT, height=3).pack(fill="x")

        head = tk.Frame(card, bg=CARD)
        head.pack(fill="x", padx=26, pady=(26, 0))
        tk.Label(head, text="FORTNITE", bg=CARD, fg=ACCENT,
                 font=("Segoe UI Semibold", 10)).pack(anchor="w")
        tk.Label(head, text="Grind Tracker", bg=CARD, fg=TEXT,
                 font=("Segoe UI Semibold", 27)).pack(anchor="w")
        tk.Label(head, text="Chests, efficiency and drop zones - read straight from your\n"
                            "own replay files. Nothing is uploaded, nothing touches the game.",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 10), justify="left").pack(anchor="w",
                                                                                pady=(8, 0))

        tk.Frame(card, bg=LINE, height=1).pack(fill="x", padx=26, pady=18)

        tk.Label(card, text="ONE-TIME SETUP", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=26)

        for number, title, detail in STEPS:
            self.step(card, number, title, detail)

        footer = tk.Frame(card, bg=CARD)
        footer.pack(fill="x", padx=26, pady=(16, 0))
        tk.Label(footer, text="Replays are read from:", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8)).pack(anchor="w")
        tk.Label(footer, text=str(core.DEMOS), bg=CARD, fg="#9fb0c0",
                 font=("Consolas", 8), wraplength=470, justify="left").pack(anchor="w")

        actions = tk.Frame(card, bg=CARD)
        actions.pack(fill="x", padx=26, pady=(18, 24), side="bottom")

        self.btn = tk.Button(actions, text="Start Tracker", command=self.start,
                             bg=ACCENT, fg="#062015", activebackground="#2fc47c",
                             activeforeground="#062015", relief="flat", bd=0,
                             font=("Segoe UI Semibold", 11), height=2, width=22,
                             cursor="hand2")
        self.btn.pack(side="left")
        tk.Button(actions, text="Not now", command=self.close, bg=CARD, fg=MUTED,
                  activebackground=CARD, activeforeground=TEXT, relief="flat", bd=0,
                  font=("Segoe UI", 9), cursor="hand2").pack(side="left", padx=(14, 0))

        root.protocol("WM_DELETE_WINDOW", self.close)
        self.btn.focus_set()
        root.bind("<Return>", lambda _e: self.start())

    def step(self, parent, number, title, detail):
        """One numbered row: a round-ish chip, then the instruction."""
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", padx=26, pady=(12, 0))

        chip = tk.Label(row, text=number, bg=LINE, fg=ACCENT, width=2, height=1,
                        font=("Segoe UI Semibold", 12))
        chip.pack(side="left", anchor="n")

        body = tk.Frame(row, bg=CARD)
        body.pack(side="left", fill="x", expand=True, padx=(12, 0))
        tk.Label(body, text=title, bg=CARD, fg=TEXT, font=("Segoe UI Semibold", 11),
                 anchor="w").pack(fill="x")
        tk.Label(body, text=detail, bg=CARD, fg=MUTED, font=("Segoe UI", 9),
                 anchor="w", justify="left", wraplength=420).pack(fill="x")

    # ---------------------------------------------------------------- actions
    def start(self):
        """Remember the welcome was seen, close it, and launch the tracker."""
        try:
            data = core.load_data()
            data.setdefault("settings", {})["onboarded"] = True
            core.save_data(data)
        except Exception as e:                  # onboarding must never block the app
            print(f"onboarding: could not save state ({type(e).__name__}: {e})")
        self.close()
        if self.on_start:
            self.on_start()

    def close(self):
        self.done = True
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def should_show(data=None):
    """True the first time, or if the user never finished the welcome."""
    data = data if data is not None else core.load_data()
    return not bool((data.get("settings") or {}).get("onboarded"))


def main():
    root = tk.Tk()
    Onboarding(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
