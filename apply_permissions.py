# -*- coding: utf-8 -*-
"""One-click apply for the dashboard's Access tab.

The Access tab can't change the live site by itself (a static web page has no
server behind it). So after you set who-sees-what and click "Save" there, run
this once — double-click "Apply access.bat". It:

  1. takes the permissions.json you just downloaded (from your Downloads folder),
  2. copies it into config/,
  3. rebuilds the encrypted dashboard (reinject.py, which also bumps the cache),
  4. commits and pushes docs/ so everyone gets the new access.

No commands to type. Passwords live in config/users.json and are never touched
by this — it only changes which tabs each person may see.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")


def run(cmd):
    return subprocess.run(cmd, cwd=HERE, env=ENV).returncode


def main():
    downloads = Path(os.path.expanduser("~")) / "Downloads"
    cands = sorted(downloads.glob("permissions*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands:
        print('No permissions file was found in your Downloads folder.')
        print('Open the dashboard, go to the Access tab, set the access,')
        print('click "Save", then run this again.')
        return 1

    src = cands[0]
    dst = HERE / "config" / "permissions.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst)
    print(f"Applying access from: {src}")

    if run([sys.executable, "reinject.py"]) != 0:
        print("\nBuild failed - nothing was published. Your live site is unchanged.")
        return 1

    run(["git", "add", "docs/index.html", "docs/sw.js"])
    run(["git", "commit", "-m", "Update user access (from Access tab)"])
    if run(["git", "push", "origin", "main"]) != 0:
        print("\nBuilt the update, but could not publish it to GitHub.")
        print("Check your internet connection and that git is signed in, then run again.")
        return 1

    print("\nDone. Everyone's tab access updates within about a minute.")
    return 0


if __name__ == "__main__":
    code = main()
    try:
        input("\nPress Enter to close...")
    except EOFError:
        pass
    sys.exit(code)
