# -*- coding: utf-8 -*-
"""
Fast re-publish: re-encrypt a cached data pack into the CURRENT template.html,
skipping the ~20-minute data build.

Use this after a template/UI-only change, when the DATA has not changed:

    python reinject.py --admin-password-file a.txt \
                       --viewer-password-file t.txt \
                       --mpd-password-file m.txt

It reads ITRS_data_pack.json (the plaintext payload written by
`build_dashboard.py ... --pack`), re-encrypts it under the passphrases, injects
it into template.html, and writes docs/index.html — in a minute or two instead
of twenty.

Regenerate the pack with a full build (`build_dashboard.py --publish --encrypt
--pack ...`) whenever the DATA changes (new year baked into the base, a config
that alters the cubes, etc.). The pack is plaintext and stays out of git.
"""
import argparse
import sys
from pathlib import Path

import build_dashboard as B   # reuse encrypt_payload, read_roles, TEMPLATE, ROLES

HERE = Path(__file__).resolve().parent
PACK = HERE / "ITRS_data_pack.json"
OUT = HERE / "docs" / "index.html"


def main():
    ap = argparse.ArgumentParser()
    for role in B.ROLES:
        ap.add_argument(f"--{role}-password-file")
    a = ap.parse_args()

    if not PACK.exists():
        sys.exit("No ITRS_data_pack.json found.\n"
                 "Run a full build once with --pack to create it:\n"
                 "  python build_dashboard.py --publish --encrypt --pack "
                 "--admin-password-file a.txt --viewer-password-file t.txt "
                 "--mpd-password-file m.txt")

    blob = PACK.read_text(encoding="utf-8")
    html = B.TEMPLATE.read_text(encoding="utf-8")
    if "__ITRS_DATA__" not in html:
        sys.exit("Template is missing the __ITRS_DATA__ placeholder")

    roles = B.read_roles(a)
    print(f"Re-encrypting cached pack ({len(blob)/1048576:.1f} MB) into the current template ...")
    enc = B.encrypt_payload(blob, roles)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html.replace("__ITRS_DATA__", enc.replace("<", "\\u003c")),
                   encoding="utf-8")
    print(f"Wrote {OUT}  ({OUT.stat().st_size/1048576:.1f} MB)  [encrypted, from pack]")
    print("Remember to bump docs/sw.js CACHE before publishing.")


if __name__ == "__main__":
    main()
