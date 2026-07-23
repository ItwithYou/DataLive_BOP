# -*- coding: utf-8 -*-
"""
Daily update: import new data, rebuild, publish, push. One command.

    python update.py                     import to today, publish, push
    python update.py --to 2026-06-20     import up to a date
    python update.py --no-push           build and commit, do not push
    python update.py --local             rebuild the local file only, no publish

The encrypted build wraps the data key once per role, so it needs one passphrase
for EVERY role (superyang, admin, team/viewer, mpd, epitrs). Each is read from
its env var, then <name>.txt on the Desktop, then this folder — see ROLE_FILES.
They are never passed as arguments, which would put them in shell history and the
process list.

Every step is checked before the next runs, so a failure stops rather than
publishing a half-built report. The service-worker cache is bumped
automatically, otherwise phones that installed the dashboard keep serving the
previous copy.
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
SW = HERE / "docs" / "sw.js"
OUT = HERE / "docs" / "index.html"

# One entry per role the encrypted build wraps a key for. Each passphrase is
# looked up in its env var, then <name>.txt on the Desktop, then this folder.
# Keep this list in sync with ROLES in build_dashboard.py — a missing file here
# means build_dashboard would stop and wait for that passphrase at a prompt.
# (flag, env var, Desktop/here filename, human label)
ROLE_FILES = [
    ("--superyang-password-file", "ITRS_SUPERYANG_PASS_FILE", "superyang.txt", "superyang (super-admin)"),
    ("--admin-password-file",     "ITRS_ADMIN_PASS_FILE",     "admin.txt",     "admin"),
    ("--viewer-password-file",    "ITRS_TEAM_PASS_FILE",      "team.txt",      "team / viewer"),
    ("--mpd-password-file",       "ITRS_MPD_PASS_FILE",       "mpd.txt",       "mpd"),
    ("--epitrs-password-file",    "ITRS_EPITRS_PASS_FILE",    "epitrs.txt",    "epitrs (Period Report)"),
]


def run(cmd, why, capture=False):
    print(f"\n=== {why} ===", flush=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run(cmd, cwd=HERE, env=env,
                       capture_output=capture, text=True, encoding="utf-8", errors="replace")
    if capture and r.stdout:
        print(r.stdout.strip())
    if r.returncode != 0:
        if capture and r.stderr:
            print(r.stderr.strip(), file=sys.stderr)
        sys.exit(f"\nStopped: {why} failed (exit {r.returncode}). Nothing was published.")
    return r


def find_pass(env_var, *fallbacks):
    p = os.environ.get(env_var)
    if p and Path(p).exists():
        return Path(p)
    for f in fallbacks:
        if Path(f).exists():
            return Path(f)
    return None


def bump_cache():
    """Installed phones cache the previous build; a new cache name evicts it."""
    if not SW.exists():
        return None
    txt = SW.read_text(encoding="utf-8")
    m = re.search(r"const CACHE = 'itrs-v(\d+)'", txt)
    if not m:
        return None
    nxt = int(m.group(1)) + 1
    SW.write_text(txt.replace(m.group(0), f"const CACHE = 'itrs-v{nxt}'"), encoding="utf-8")
    print(f"  service worker cache -> itrs-v{nxt}")
    return nxt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from", default="2026-01-01")
    ap.add_argument("--to", dest="d_to", default=date.today().isoformat())
    ap.add_argument("--import", dest="do_import", action="store_true",
                    help="bake the export files into parquet before building (rarely needed)")
    ap.add_argument("--no-push", action="store_true", help="commit but do not push")
    ap.add_argument("--local", action="store_true", help="rebuild the local file only")
    a = ap.parse_args()

    py = sys.executable

    # The published base is 2017-2025. The current year is added by uploading the
    # export files in the dashboard's Data sources panel, on each device, where
    # each file can also be removed - so no import happens here by default. Pass
    # --import only if you want to bake the export files into the base instead.
    if a.do_import:
        run([py, "import_detail.py"], "Baking the export files into parquet", capture=True)

    if a.local:
        run([py, "build_dashboard.py"], "Rebuilding local dashboard", capture=True)
        print("\nDone. Open ITRS_Dashboard.html")
        return

    desktop = Path.home() / "Desktop"
    pass_args, missing = [], []
    for flag, env_var, fname, label in ROLE_FILES:
        p = find_pass(env_var, desktop / fname, HERE / fname)
        if p:
            pass_args += [flag, str(p)]
        else:
            missing.append((label, fname, env_var))
    if missing:
        lines = ["Passphrase files not found for these roles:", ""]
        for label, fname, env_var in missing:
            lines.append(f"  - {label}: create {fname} on your Desktop (one line),")
            lines.append(f"      or point {env_var} at a file holding it.")
        lines += ["",
                  "Every role needs one, at least 12 characters. The build wraps the data",
                  "key separately for each, so a missing one would stall at a prompt.",
                  "Delete the files once the run finishes."]
        sys.exit("\n".join(lines))

    bump_cache()
    run([py, "build_dashboard.py", "--publish", "--encrypt", *pass_args],
        "Building encrypted dashboard", capture=True)

    if not OUT.exists():
        sys.exit("Build reported success but docs/index.html is missing.")
    print(f"  {OUT.name}: {OUT.stat().st_size/1048576:.1f} MB")

    run(["git", "add", "docs/index.html", "docs/sw.js"], "Staging", capture=True)
    status = run(["git", "status", "--porcelain", "docs"], "Checking", capture=True)
    if not status.stdout.strip():
        print("\nNo change to publish.")
        return
    run(["git", "commit", "-q", "-m", f"Update dashboard to {a.d_to}"], "Committing", capture=True)

    if a.no_push:
        print("\nCommitted. Push when ready:  git push")
        return
    run(["git", "push"], "Pushing", capture=True)
    print("\nPublished. Allow a minute, then hard-refresh:")
    print("  https://itwithyou.github.io/DataLive_BOP/docs/")
    print("\nDelete the passphrase files from your Desktop now.")


if __name__ == "__main__":
    main()
