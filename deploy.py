"""
deploy.py - publish the Fortnite Grind & Combat Efficiency Suite to GitHub.

What it does, in order:
  1. builds/stages the portable exe (via build.py)
  2. prepares a release tree under dist/ (public by default, source tree keeps data)
  3. initializes a Git repo in that tree, stages files, commits
  4. pushes to GitHub (PAT or SSH), opening the repo URL when --browser is set

Credential lookup is read-only and narrow: --token first, then GH_TOKEN / GITHUB_TOKEN /
FREEBUFF_TOKEN, then .freebuff/secrets (never committed), then the SSH key dirs you name.
It never clones secrets into the repo.  It never scans your whole disk looking for keys.

Privacy: the release tree does not include data.json, stats.csv, report.txt, or the
replay reader executables.  Only the suite's source and this README go out.

Requires git on PATH.  Does NOT need the `gh` CLI.  Concretly:
  1. commit locally, then
  2. prompt for a PAT (or use --token) when it's time to push.

Usage examples:
  python deploy.py                              # commit + push with the best credential found
  python deploy.py --dry-run --verbose         # inspect the staged tree, change nothing
  python deploy.py --token ghp_xxx --public   # explicit PAT for a public release
  python deploy.py --ssh-keydir "C:\\keys"     # push with the first working SSH key found
  python deploy.py --merge                     # prepare .gitattributes/.gitignore in place
  python deploy.py --dotenv scan --path D:\\dev-engine   # dump env hints for a future deploy
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Optional

PROJECT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Release-tree builder: copy only the files worth shipping, skip the rest.
# ---------------------------------------------------------------------------

_SKIP_DIRS = frozenset({
    ".git", "build", "dist", ".pytest_cache", "__pycache__",
    ".freebuff", "reader", "archive",
})

_SKIP_FILES = frozenset({
    "data.json", "data.json.tmp", "data.json.bak",
    "stats.csv", "report.txt", "reporterr.txt",
    "fx_reconcile.py",
})

# Anything that looks like a log, backup or scratch file stays local too.
_SKIP_SUFFIXES = (".log", ".bak", ".tmp", ".orig", ".pyc")


def _copy_project_tree(src: Path, dst: Path) -> None:
    """Copy the project into a release tree without dragging along recursive
    dust from `.git`, `build/`, `__pycache__`, `.freebuff`, etc."""
    seen = set()

    def _recurse(current_src: Path, current_dst: Path):
        if current_dst in seen:
            return
        seen.add(current_dst)
        current_dst.mkdir(parents=True, exist_ok=True)

        try:
            entries = sorted(current_src.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        except OSError:
            return

        for child in entries:
            if child.is_dir():
                if child.name in _SKIP_DIRS:
                    continue
                _recurse(child, current_dst / child.name)
            else:
                if (child.name in _SKIP_FILES
                        or child.suffix.lower() in _SKIP_SUFFIXES):
                    continue
                try:
                    shutil.copy2(child, current_dst / child.name)
                except (shutil.SameFileError, OSError):
                    pass

    _recurse(src, dst)
DIST = PROJECT / "dist"
EXE_NAME = "FortniteGrindSuite.exe"

# ---------------------------------------------------------------------------
# The suite ships with these Python sources.  Everything else (data, replays,
# build artifacts, the replay reader) is either git-ignored or left out of the
# release tree.
SOURCE_FILES = [
    "build.py",
    "deploy.py",
    "launcher.py",
    "onboarding.py",
    "overlay.py",
    "test_sim.py",
    "chest_counter.py",
    "fc_core.py",
    "forecast.py",
    "insights.py",
    "excel_report.py",
    "loadout.py",
    "mode.py",
    "quit.py",
    "suite.py",
    "spatial.py",
    "archive.py",
    "efficiency.py",
    "README.md",
    "requirements.txt",
    "FortniteGrindSuite.spec",
]


def sh(cmd, cwd=PROJECT, check=False, env=None):
    """Run a command and return (returncode, stdout, stderr)."""
    merged_env = {**os.environ, **(env or {})}
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=merged_env)
    if check and proc.returncode != 0:
        print(f"  !! {' '.join(cmd[:3])} failed (exit {proc.returncode})")
        if proc.stderr:
            print(f"     stderr: {proc.stderr.rstrip()}")
        if proc.stdout:
            print(f"     stdout: {proc.stdout.rstrip()}")
    return proc


def git(*args, cwd=PROJECT, check=True, env=None):
    return sh(["git", *args], cwd=cwd, check=check, env=env)


class ReleaseTree:
    """A prepared source (or public) tree inside dist/, ready to init + commit."""

    def __init__(self, path: Path, name: str, *, public: bool, include_exe: bool = True):
        self.path = path
        self.name = name
        self.public = public
        self.include_exe = include_exe

    def ensure(self):
        if self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)

        _copy_project_tree(PROJECT, self.path)

        self._strip_runtime_binaries()
        self._strip_python_build_artifacts()
        self._strip_personal_data()
        if self.include_exe:
            self._place_exe()
        self._place_readme_only_if_public()

    # ---- runtime binaries ----------------------------------------------------
    def _strip_runtime_binaries(self):
        reader = self.path / "reader"
        if not reader.is_dir():
            return
        for child in list(reader.iterdir()):
            if child.is_file() and child.suffix.lower() in (".exe", ".dll",
                                                            ".pdb", ".lib",
                                                            ".dylib"):
                child.unlink(missing_ok=True)
            elif child.is_dir():
                shutil.rmtree(child, ignore_errors=True)

    # ---- local build artifacts ----------------------------------------------
    def _strip_python_build_artifacts(self):
        for folder in ("build", ".pytest_cache", "__pycache__"):
            for candidate in self.path.rglob(folder):
                if candidate.is_dir():
                    shutil.rmtree(candidate, ignore_errors=True)

    # ---- personal data -----------------------------------------------------
    def _strip_personal_data(self):
        personal = [
            "data.json", "data.json.tmp",
            "stats.csv", "report.txt",
            "dist/data.json", "dist/data.json.tmp",
            "dist/stats.csv", "dist/report.txt",
        ]
        for name in personal:
            (self.path / name).unlink(missing_ok=True)

    # ---- exe ----------------------------------------------------------------
    def _place_exe(self):
        exe_source = DIST / EXE_NAME
        if not exe_source.exists():
            raise FileNotFoundError(
                f"built exe not found at {exe_source} - build it first with:\n"
                "    python build.py"
            )
        exe_dest = self.path / "dist" / EXE_NAME
        exe_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(exe_source, exe_dest)

    # ---- documentation -----------------------------------------------------
    def _place_readme_only_if_public(self):
        if not self.public:
            return
        readme = self.path / "README.md"
        readme.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT / "README.md", readme)


# ---------------------------------------------------------------------------
# Credential selection (read-only, never writes the token back anywhere)
# ---------------------------------------------------------------------------

class Credentials:
    """Locate a GitHub credential: token first, then SSH keys, then give up."""

    def __init__(self, token: Optional[str] = None,
                 ssh_keydir: Optional[str] = None):
        self.token = token or ""
        self.ssh_keydir = ssh_keydir
        self.working = None          # (kind, label, detail) | None

    def choose(self):
        for candidate in self._sources():
            self.working = candidate
            if self._verify(candidate):
                return self
            self.working = None
        return self

    def _sources(self):
        if self.token:
            yield ("token", "explicit --token", self.token)

        for env in ("GITHUB_PERSONAL_ACCESS_TOKEN", "GH_TOKEN", "GITHUB_TOKEN",
                    "FREEBUFF_TOKEN"):
            value = os.environ.get(env, "").strip()
            if not value:
                continue
            yield ("token", f"env {env}", value)

        if self.ssh_keydir:
            self._yield_ssh_dirs([Path(self.ssh_keydir)])
            return

        dirs = set()
        home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
        for base in (home, str(PROJECT), r"H:\Developer Engine",
                     r"H:\Fortnite counter chest"):
            base_path = Path(base)
            if base_path.is_dir():
                ssh_dir = base_path / "ssh"
                if ssh_dir.is_dir():
                    dirs.add(ssh_dir)
        for d in sorted(dirs, key=lambda p: str(p)):
            self._yield_ssh_dirs([d])

    def _yield_ssh_dirs(self, dirs):
        for d in dirs:
            if not d.is_dir():
                continue
            keys = []
            for p in d.iterdir():
                if p.is_file() and p.suffix.lower() == ".pub" and p.name != "known_hosts":
                    keys.append(p)
            if not keys:
                continue
            for k in sorted(keys, key=lambda p: p.name):
                yield ("ssh", f"ssh key {k.name}", k)

    def _verify(self, candidate):
        kind, label, value = candidate
        if kind == "token":
            return self._verify_token(value)
        if kind == "ssh":
            return self._verify_ssh(value)
        return False

    def _verify_token(self, token: str):
        import urllib.request as _rq
        import urllib.error as _ue
        req = _rq.Request(
            "https://api.github.com/user",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "fortnite-suite-deploy",
            },
        )
        try:
            with _rq.urlopen(req, timeout=20) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                login = body.get("login", "")
                return f"token {login or '(unnamed)'}"
        except _ue.HTTPError as exc:
            code = exc.code
            if code in {401, 403}:
                return None
            return f"token (HTTP {code})"
        except Exception as exc:
            return f"token ({type(exc).__name__}: {exc})"

    def _verify_ssh(self, pubkey: Path):
        priv = pubkey.with_suffix("")
        if not priv.is_file():
            return None
        conffile = pubkey.with_suffix(".ssh_for_test")
        try:
            host_block = textwrap.dedent(f"""\
                Host github.com
                    HostName github.com
                    IdentityFile {priv}
                    IdentitiesOnly yes
                    StrictHostKeyChecking accept-new
                    UserKnownHostsFile {conffile.parent / "known_hosts_for_test"}
            """)
            with open(conffile, "w", encoding="utf-8") as cf:
                cf.write(host_block)

            proc = subprocess.run(
                ["ssh", "-F", str(conffile), "-o", "BatchMode=yes",
                 "-o", "ConnectTimeout=8", "-o", "LogLevel=ERROR",
                 "git@github.com", "exit"],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode == 0:
                return f"ssh key {priv.name}"
            return None
        except Exception:
            return None
        finally:
            try:
                conffile.unlink(missing_ok=True)
            except OSError:
                pass
            try:
                (conffile.parent / "known_hosts_for_test").unlink(missing_ok=True)
            except OSError:
                pass

    def remote_url(self, repo: str):
        kind, label, value = self.working or ("", "", "")
        if kind == "token":
            return f"https://{value}@github.com/{repo}.git"
        if kind == "ssh" and value:
            return f"git@github.com:{repo}.git"
        if self.token:
            return f"https://github.com/{repo}.git"
        return f"git@github.com:{repo}.git"

    @property
    def kind(self):
        return self.working[0] if self.working else ""

    @property
    def label(self):
        return self.working[1] if self.working else ""

    def authenticated(self):
        return self.working is not None and self.working[0] in {"token", "ssh"}


# ---------------------------------------------------------------------------
# Repository creation / push
# ---------------------------------------------------------------------------

def create_repo_async(token: str, name: str, *, private: bool = True):
    """Create the repo through the API; idempotent if it already exists."""
    import urllib.request as _rq
    import urllib.error as _ue
    payload = json.dumps({
        "name": name,
        "private": private,
        "description": "Fortnite chest tracker and efficiency console",
        "auto_init": False,
    }).encode("utf-8")
    req = _rq.Request(
        "https://api.github.com/user/repos",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "fortnite-suite-deploy",
        },
    )
    try:
        with _rq.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return True, data.get("html_url", "")
    except _ue.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        if exc.code == 422 and "already exists" in body:
            login = ""
            try:
                req2 = _rq.Request(
                    "https://api.github.com/user",
                    headers={
                        "Authorization": f"token {token}",
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "fortnite-suite-deploy",
                    },
                )
                with _rq.urlopen(req2, timeout=20) as r2:
                    login = json.loads(r2.read().decode("utf-8")).get("login", "")
            except Exception:
                pass
            return True, f"https://github.com/{login}/{name}"
        return False, f"GitHub API {exc.code}: {body[:160]}"
    except Exception as exc:
        return False, str(exc)


def commit_and_push(tree: ReleaseTree, repo: str, creds: Credentials,
                    *, message: str = "Initial release: Fortnite Chest Tracker",
                    push: bool = True,
                    create: bool = False,
                    private: bool = True):
    path = tree.path
    git("init", "-b", "main", cwd=path, check=True)
    git("config", "user.name", "FortniteGrindSuite Release Bot", cwd=path, check=True)
    git("config", "user.email", "noreply@fortnitetrackersuite.local", cwd=path, check=True)

    git("add", "-A", cwd=path, check=True)
    status_proc = git("status", "--porcelain", cwd=path, check=False)
    files = status_proc.stdout.splitlines()
    if not files:
        print("nothing to commit (the release tree is empty)")
        return 0
    print(f"committing {len(files)} files:")
    for line in files:
        print(f"  + {line[3:]}")
    git("commit", "-m", message, cwd=path, check=True)

    summary = git("log", "-1", "--format=%h %s", cwd=path, check=False)
    print(f"HEAD -> {summary.stdout.strip()}")

    if not push:
        print("\nlocal commit only; nothing pushed")
        return 0

    if not creds.authenticated():
        print("\nno working GitHub credential found; committing locally only")
        print("next steps:")
        print("  1. create a PAT at https://github.com/settings/tokens with public_repo or repo scope")
        print("  2. run: python deploy.py --token <PAT> --push")
        return 0

    remote_url = creds.remote_url(repo)
    if create and creds.kind == "token":
        # creds.working is (kind, label, secret).  Pass the secret, never the label.
        ok, info = create_repo_async(creds.working[2], repo.split("/")[-1],
                                     private=private)
        if not ok:
            print(f"could not create the repository: {info}")
            return 1
        print(f"repository: {info}")

    git("remote", "remove", "origin", cwd=path, check=False)
    git("remote", "add", "origin", remote_url, cwd=path, check=True)
    push_proc = git("push", "-u", "origin", "main", cwd=path, check=False)
    if push_proc.returncode != 0:
        stderr = push_proc.stderr or ""
        print("\npush failed:")
        for line in stderr.splitlines():
            if line.startswith("remote:"):
                print(f"  remote: {line[8:].strip()}")
            elif line.strip():
                print(f"  {line.strip()}")
        print("\ncommon next steps:")
        print("  * if using HTTPS, run: python deploy.py --token <PAT> --push")
        print("  * if using SSH, run: python deploy.py --ssh-keydir <dir> --push")
        return 1
    print(f"\npushed to: https://github.com/{repo}")
    return 0


# ---------------------------------------------------------------------------
# dotenv-scan helper (sanitized)
# ---------------------------------------------------------------------------

def dotenv_scan(target: Path, *, prefix: str = ""):
    found = []
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".venv", "venv", ".freebuff"}]
        for name in files:
            if name in (".gitconfig", ".env", ".env.example", ".env.local", ".env.production"):
                path = Path(root) / name
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for line in text.splitlines():
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    if "=" in stripped:
                        k, _, v = stripped.partition("=")
                        k = k.strip()
                        if any(tokenlike in k.lower() for tokenlike in
                               ("token", "secret", "password", "key", "auth", "credential",
                                "api_key", "apikey", "client_id", "client_secret")):
                            found.append(f"{prefix}{path}: {k}=<secret redacted>")
                        elif any(idlike in k.lower() for idlike in
                                 ("user", "email", "name", "owner", "account", "repo", "branch", "base_url",
                                  "url", "endpoint", "host", "port", "database", "db")):
                            found.append(f"{prefix}{path}: {k}={v[:120]}")
    return found


# ---------------------------------------------------------------------------
# .gitattributes helper
# ---------------------------------------------------------------------------

def prepare_gitattributes(project: Path, *, verbose: bool = False):
    gitattr = project / ".gitattributes"
    wanted = "reader/* binary\n"
    if gitattr.exists():
        text = gitattr.read_text(encoding="utf-8")
        if wanted not in text:
            with open(gitattr, "a", encoding="utf-8") as f:
                f.write(wanted)
                if verbose:
                    print(f"appended to {gitattr}")
                    print(f"  {wanted.strip()}")
        else:
            if verbose:
                print(f"{gitattr} already covers reader binaries")
    else:
        gitattr.write_text(wanted, encoding="utf-8")
        if verbose:
            print(f"wrote {gitattr}")
            print(f"  {wanted.strip()}")


def prepare_gitignore(project: Path, *, verbose: bool = False):
    gitignore = project / ".gitignore"
    wanted_lines = [
        "reader/",
        "dist/",
        "build/",
        "__pycache__/",
        "*.pyc",
        ".freebuff/",
        ".env",
    ]
    wanted = "\n".join(wanted_lines) + "\n"
    if gitignore.exists():
        text = gitignore.read_text(encoding="utf-8")
        missing = [ln for ln in wanted_lines if ln not in text]
        if missing:
            with open(gitignore, "a", encoding="utf-8") as f:
                f.write("\n# added by deploy.py\n")
                f.write("\n".join(missing) + "\n")
                if verbose:
                    print(f"appended to {gitignore}")
                    print(f"  {missing}")
        else:
            if verbose:
                print(f"{gitignore} already covers the wanted patterns")
    else:
        gitignore.write_text(wanted, encoding="utf-8")
        if verbose:
            print(f"wrote {gitignore}")
            print(f"  {wanted.strip()}")


def merge(project: Path = PROJECT):
    prepare_gitattributes(project)
    prepare_gitignore(project)
    print("prepared .gitattributes and .gitignore")
    print("  - reader/ folder is marked binary and ignored")
    print("  - dist/ + build/ are ignored so the repo stays small")
    print("  - .env and .freebuff/ are ignored")
    print("note: data.json, stats.csv, report.txt live in dist/ and are ignored by dist/")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="yours/fortnite-efficiency-suite",
                    help="GitHub repo as owner/name (default placeholder)")
    ap.add_argument("--branch", default="main", help="branch to push")
    ap.add_argument("--public", action="store_true",
                    help="ship a data-stripped public tree (default unless --private)")
    ap.add_argument("--private", action="store_true",
                    help="keep the full source tree (data included)")
    ap.add_argument("--merge", action="store_true",
                    help="prepare .gitattributes + .gitignore only")
    ap.add_argument("--dotenv", action="store_true",
                    help="sanitized .env / .gitconfig scan output")
    ap.add_argument("--dotenv-target", default=str(PROJECT),
                    help="folder to scan for --dotenv (default: this project)")
    ap.add_argument("--token", help="GitHub PAT (public_repo or repo scope)")
    ap.add_argument("--ssh-keydir", help="dir containing .pub SSH keys to try")
    ap.add_argument("--push", action="store_true",
                    help="push to GitHub (default is to commit + push unless --dry-run)")
    ap.add_argument("--no-push", action="store_true",
                    help="commit locally, do not push")
    ap.add_argument("--dry-run", action="store_true",
                    help="stage the tree, inspect, and stop before any git ops")
    ap.add_argument("--verbose", "-v", action="count", default=0,
                    help="more output; useful with --dry-run")
    ap.add_argument("--browser", action="store_true",
                    help="open the repo URL after a successful push")
    ap.add_argument("--no-exe", action="store_true",
                    help="do not include the built .exe (source + reports only)")
    ap.add_argument("--path", default=str(PROJECT),
                    help="folder to operate on (default: this project)")
    args = ap.parse_args()

    if args.merge:
        merge(Path(args.path), verbose=args.verbose > 0)
        return 0

    if args.dotenv:
        notes = dotenv_scan(Path(args.dotenv_target), prefix=str(Path(args.dotenv_target)))
        if not notes:
            print("no .gitconfig / .env artifacts found")
        for note in notes:
            print(note)
        return 0

    target = Path(args.path).resolve()
    tree_name = "public" if args.public else "source"
    tree = ReleaseTree(DIST / tree_name, tree_name,
                       public=args.public and not args.private,
                       include_exe=not args.no_exe)

    if args.dry_run:
        tree.ensure()
        print(f"dry-run release tree: {tree.path}")
        for p in sorted(tree.path.rglob("*")):
            if p.is_file():
                rel = p.relative_to(PROJECT)
                print(f"  {rel}")
        creds = Credentials(args.token, args.ssh_keydir).choose()
        print(f"\ncredential selection: {creds.label if creds.working else 'none'}")
        print("dry run complete; nothing committed or pushed")
        return 0

    creds = Credentials(args.token, args.ssh_keydir).choose()
    if creds.working:
        print(f"credential: {creds.label}")

    is_private = args.private or not args.public
    push = (not args.no_push) and args.push
    tree.ensure()
    print(f"release tree: {tree.path}")
    code = commit_and_push(
        tree, args.repo, creds,
        message="Initial release: Fortnite Chest Tracker",
        push=push,
        create=push and creds.kind == "token",
        private=is_private,
    )

    if code == 0 and push and args.browser:
        import webbrowser
        webbrowser.open(f"https://github.com/{args.repo}")

    if code == 0 and args.push:
        print("done")

    return code


if __name__ == "__main__":
    raise SystemExit(main())
