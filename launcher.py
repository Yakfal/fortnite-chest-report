"""
launcher.py - one double-click entry point for the whole suite.

Three ways in:

    FortniteGrindSuite.exe              the small control panel
    FortniteGrindSuite.exe --hud        straight to the on-screen HUD
    FortniteGrindSuite.exe --report     write a full report to report.txt and print it

The HUD is started as its own process, so closing the panel does not close the HUD and a HUD
crash cannot take the panel with it. When frozen by PyInstaller, `sys.executable` is the exe
itself, which is why the child process is "the same exe, with --hud".
"""

import os
import subprocess
import sys
import threading
from pathlib import Path

import fc_core as core

REPORT_FILE = "report.txt"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = 0x00000008


def spawn_hud():
    """Start the HUD in its own detached process."""
    cmd = [sys.executable] + (["--hud"] if getattr(sys, "frozen", False) else ["overlay.py"])
    try:
        subprocess.Popen(cmd, cwd=str(core.BASE), creationflags=DETACHED | CREATE_NO_WINDOW,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True
    except OSError as e:
        return f"{type(e).__name__}: {e}"


def full_report():
    """Refresh from the replays, then write every report section to report.txt."""
    import archive
    import efficiency
    import forecast
    import loadout
    import mode
    import quit as quit_model
    import spatial

    data = core.sync()
    parts = [
        efficiency.report(data),
        forecast.report(data),
        loadout.report(data),
        spatial.report(data, show_map=True),
        mode.report(data),
        quit_model.report(data),
    ]
    rows, csv_path = archive.write_csv(data)
    from collections import Counter
    counts = Counter()
    for m in core.matches(data):
        counts[m.get("mode", "unknown")] += 1
    parts.append(f"DATA LOGGER\n{'=' * 60}\n{rows} matches written to {csv_path}\n"
                 f"modes: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + "\n")
    text = "\n".join(parts)
    path = core.BASE / REPORT_FILE
    path.write_text(text, encoding="utf-8")
    return text, path


def open_report():
    path = core.BASE / REPORT_FILE
    if path.exists():
        try:
            os.startfile(str(path))          # Windows: opens in Notepad
        except (OSError, AttributeError):
            pass


# ---------------------------------------------------------------- CLI modes

def cli_hud():
    import overlay
    argv = sys.argv[:]
    sys.argv = [argv[0]] + [a for a in argv[1:] if a != "--hud"]
    return overlay.main()


def cli_report():
    print("Refreshing from replays...")
    text, path = full_report()
    print(text)
    print(f"(saved to {path})")
    return 0


# ---------------------------------------------------------------- control panel

def panel():
    import tkinter as tk
    from tkinter import messagebox

    import onboarding

    # First launch (or a fresh install): show the guide. "Start Tracker" opens the HUD and
    # then the panel follows, so reports and archiving are still one click away.
    if onboarding.should_show():
        welcome = tk.Tk()
        onboarding.Onboarding(welcome, on_start=spawn_hud)
        welcome.mainloop()

    root = tk.Tk()
    root.title("Fortnite Grind & Efficiency Suite")
    root.configure(bg="#0a0e13")
    root.geometry("420x360")
    root.resizable(False, False)

    tk.Label(root, text="GRIND & EFFICIENCY SUITE", bg="#0a0e13", fg="#e8eef5",
             font=("Segoe UI Semibold", 13)).pack(pady=(18, 2))
    tk.Label(root, text=f"{core.DEMOS}", bg="#0a0e13", fg="#7f8f9f",
             font=("Segoe UI", 8)).pack()

    status = tk.Label(root, text="ready", bg="#0a0e13", fg="#39d98a", font=("Segoe UI", 9))
    status.pack(pady=(10, 8))

    def make(text, command, colour):
        return tk.Button(root, text=text, command=command, bg=colour, fg="#0a0e13",
                         activebackground="#39d98a", relief="flat", bd=0,
                         font=("Segoe UI Semibold", 10), height=2, width=32,
                         cursor="hand2")

    def do_hud():
        result = spawn_hud()
        status.config(text="HUD launched" if result is True else f"HUD failed: {result}",
                      fg="#39d98a" if result is True else "#ff4d4d")

    def do_report():
        status.config(text="reading replays...", fg="#7f8f9f")

        def work():
            try:
                text, path = full_report()
                first = [l for l in text.splitlines() if l.strip()][:1]
                status.config(text=f"report ready: {path.name}", fg="#39d98a")
                if any("Traceback" in l for l in first):
                    status.config(text="report had errors, see report.txt", fg="#ff4d4d")
            except Exception as e:
                status.config(text=f"failed: {type(e).__name__}", fg="#ff4d4d")
        threading.Thread(target=work, daemon=True).start()

    def do_csv():
        import archive
        try:
            rows, path = archive.write_csv()
            status.config(text=f"stats.csv: {rows} matches", fg="#39d98a")
        except Exception as e:
            status.config(text=f"failed: {type(e).__name__}", fg="#ff4d4d")

    def do_archive():
        import archive
        try:
            summary = archive.archive_replays(days=3, delete=False)
            n = len(summary["zipped"])
            msg = ("Nothing old enough to archive yet" if not n
                   else f"{n} replays zipped, originals kept")
            status.config(text=msg, fg="#39d98a")
            if n:
                messagebox.showinfo("Archive", f"{msg}\n\n{summary['zip']}\n\n"
                                    "Originals were kept. To remove them after checking, run:\n"
                                    "python archive.py --archive --days 3 --delete")
        except Exception as e:
            status.config(text=f"failed: {type(e).__name__}", fg="#ff4d4d")

    make("Open HUD (second monitor)", do_hud, "#39d98a").pack(pady=4)
    make("Run full report -> report.txt", do_report, "#2b6cb0").pack(pady=4)
    make("Refresh stats.csv", do_csv, "#1d2733").pack(pady=4)
    make("Archive old replays (keeps originals)", do_archive, "#1d2733").pack(pady=4)
    tk.Button(root, text="Open report.txt", command=open_report, bg="#0a0e13", fg="#7f8f9f",
              relief="flat", bd=0, font=("Segoe UI", 9), cursor="hand2").pack(pady=(8, 0))

    root.mainloop()
    return 0


def main():
    args = sys.argv[1:]
    if "--hud" in args:
        return cli_hud()
    if "--report" in args:
        return cli_report()
    return panel()


if __name__ == "__main__":
    raise SystemExit(main())
