"""
build.py - build the suite and stage release trees.

Usage:
  python build.py                       # build exe + stage a full (source) tree
  python build.py --public              # also stage a public (data-stripped) tree
  python build.py --no-portable         # keep the reader beside the exe instead of inlining
  python build.py --skip-build          # just re-stage release trees from an existing exe
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
DIST = PROJECT / "dist"
BUILD_DIR = PROJECT / "build"

NAME = "FortniteGrindSuite"
EXE_NAME = f"{NAME}.exe"

SOURCE_TREE = DIST / "source"
PUBLIC_TREE = DIST / "public"

# Extra files to stage alongside the exe in the release trees.
# The chest map image must sit next to the exe at runtime, so copy it into
# both the source and public trees.
EXTRA_FILES = frozenset({"chest_map.webp"})


def pyinstaller_installed() -> bool:
    try:
        import PyInstaller  # noqa: F401
        return True
    except ImportError:
        return False


def install_pyinstaller() -> bool:
    print("PyInstaller not found; installing via pip...")
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "pyinstaller"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        if proc.stderr:
            print(proc.stderr.rstrip())
        return False
    return True


def build_exe(*, portable: bool) -> int:
    if not pyinstaller_installed():
        if not install_pyinstaller():
            print("could not install PyInstaller")
            return 1

    spec = PROJECT / "FortniteGrindSuite.spec"
    if spec.exists():
        env_extra = {}
        if not portable:
            env_extra["FC_NO_BUNDLE_READER"] = "1"
        print(f"building via {spec.name} (portable={portable})...")
        proc = subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(spec)],
            cwd=PROJECT,
            env={**os.environ, **env_extra},
            capture_output=False,
        )
        return proc.returncode

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", NAME,
        "--distpath", str(DIST),
        "--workpath", str(BUILD_DIR),
        "--specpath", str(PROJECT),
        "--hidden-import", "tkinter",
        "--hidden-import", "tkinter.ttk",
        "--hidden-import", "tkinter.messagebox",
        "--hidden-import", "tkinter.filedialog",
        "--windowed",
        str(PROJECT / "launcher.py"),
    ]
    print("building via command line (no .spec file found)...")
    proc = subprocess.run(cmd, capture_output=False)
    return proc.returncode


def commit_sha(path: Path) -> str:
    try:
        return subprocess.run(
            ["git", "log", "-1", "--format=%h", "--", path],
            cwd=PROJECT, capture_output=True, text=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def _copy_dir(src: Path, dst: Path, skip_dirs: frozenset, skip_files: frozenset) -> None:
    """Like shutil.copytree but without dusting recursion errors off at every turn.

    This implementation is careful to avoid gobling up symlinks and cyclic
    filesystem graphs.
    """
    seen = {dst}

    def _copy(sub_src: Path):
        sub_dst = dst / sub_src.relative_to(src)
        if sub_dst in seen:
            return
        seen.add(sub_dst)
        sub_dst.mkdir(parents=True, exist_ok=True)
        for child in sorted(sub_src.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
            if child.is_dir():
                if child.name in skip_dirs:
                    continue
                try:
                    _copy(child)
                except OSError:
                    pass
            else:
                if child.name in skip_files:
                    continue
                try:
                    shutil.copy2(child, sub_dst / child.name)
                except (shutil.SameFileError, OSError):
                    pass

    _copy(src)


def stage_release_tree(*, public: bool) -> None:
    dest = PUBLIC_TREE if public else SOURCE_TREE

    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)

    skip_dirs = frozenset({"build", ".pytest_cache", "__pycache__", ".git",
                           ".freebuff", "dist", "reader"})
    skip_files = frozenset({"data.json", "data.json.tmp", "stats.csv", "report.txt"})

    _copy_dir(PROJECT, dest, skip_dirs, skip_files)

    exe = DIST / EXE_NAME
    if exe.exists():
        exe_dest = dest / "dist" / EXE_NAME
        exe_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(exe, exe_dest)
        print(f"[stage] exe -> {exe_dest.relative_to(PROJECT)}")
    else:
        print("[stage] exe missing - run build first")

    # Stage the chest map image alongside the exe in the release trees.
    # overlay.py looks for chest_map.webp next to the exe at runtime.
    for extra in EXTRA_FILES:
        src = PROJECT / extra
        if src.exists():
            dst = dest / "dist" / extra
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"[stage] {extra} -> {dst.relative_to(PROJECT)}")

    if public:
        shutil.copy2(PROJECT / "README.md", dest / "README.md")
        print("[stage] public tree uses project README.md")

    print(f"[stage] {dest.name} tree ready under {dest}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--public", action="store_true",
                    help="also stage a public, data-stripped tree")
    ap.add_argument("--portable", action="store_true",
                    help="inline the replay reader into the exe (default)")
    ap.add_argument("--no-portable", action="store_true",
                    help="do not inline the replay reader into the exe")
    ap.add_argument("--skip-build", action="store_true",
                    help="re-stage trees from an existing exe")
    ap.add_argument("--verbose", "-v", action="count", default=0)
    args = ap.parse_args()

    portable = args.portable and not args.no_portable
    if args.portable and args.no_portable:
        portable = False

    exe = DIST / EXE_NAME

    if not args.skip_build:
        code = build_exe(portable=portable)
        if code != 0:
            print(f"build failed (exit {code})")
            return 1
        print(f"[build] exe ready: {exe}")

    stage_release_tree(public=False)
    if args.public or args.verbose:
        stage_release_tree(public=True)

    if exe.exists():
        sha = commit_sha(exe) or "unknown"
        size_mb = exe.stat().st_size / 1_048_576
        print(f"\n[release] {EXE_NAME} ({size_mb:.1f} MB, git sha {sha})")
        print(f"[release] source tree: {SOURCE_TREE}")
        if args.public or args.verbose:
            print(f"[release] public tree: {PUBLIC_TREE}")
        print("\n[release] next step: python deploy.py --public --push")
    else:
        print("\n[release] no exe - cannot stage a release tree")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
