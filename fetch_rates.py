# -*- coding: utf-8 -*-
"""Fetch the daily LAK exchange rates and write docs/rates.json.

The dashboard cannot read these sites itself: neither allows cross-origin
requests, so a browser fetch is blocked. This runs server-side (a scheduled
GitHub Action, or by hand) and drops a small rates.json next to index.html,
which the Exchange tab then reads instantly from its own origin.

Two sources, and they format numbers the OPPOSITE way round -- getting this
wrong silently scales a rate by 1000, so each has its own parser:

  BOL  (bol.gov.la)   European:  22.558 -> 22558      673,20 -> 673.20
  BCEL (bcel.com.la)  English:   22,489 -> 22489      672.61 -> 672.61

BOL's server is missing an intermediate certificate, so its chain will not
verify. Only that one host is fetched without verification, and every source
must pass a sanity check on the USD rate before it is published, so a bad
response cannot quietly poison the figures.
"""
import json
import re
import ssl
import sys
import urllib.request
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "docs" / "rates.json"

BOL_URL = "https://www.bol.gov.la/en/ExchangRate.php"
BCEL_URL = "https://www.bcel.com.la/bcel/exchange-rate.html?lang=en"

# A published USD rate outside this band means the page changed shape or the
# response was tampered with; the source is dropped rather than trusted.
USD_MIN, USD_MAX = 15000.0, 40000.0
UA = {"User-Agent": "Mozilla/5.0 (ITRS rates fetcher)"}


def fetch(url, verify=True, timeout=30):
    ctx = ssl.create_default_context()
    if not verify:                      # BOL only: incomplete chain on their server
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def num_eu(s):
    """European: dot groups thousands, comma is the decimal point."""
    s = re.sub(r"[^\d.,-]", "", s or "")
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def num_en(s):
    """English: comma groups thousands, dot is the decimal point."""
    s = re.sub(r"[^\d.,-]", "", s or "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def table_rows(html):
    """Cells of the first <table>, tags stripped, blanks dropped."""
    m = re.search(r"<table[^>]*>(.*?)</table>", html, re.S | re.I)
    if not m:
        return []
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S | re.I):
        cells = [re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", c))).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        cells = [c for c in cells if c]
        if cells:
            out.append(cells)
    return out


def parse(html, tonum):
    """Rows -> {code: {name, buy, sell}}.

    Shape-agnostic on purpose, because the two tables differ: find the currency
    code (a bare 3-letter code, possibly followed by a denomination like
    "USD 50-100"), take the first number as buy and the last as sell. The first
    listing of a currency wins, so BCEL's small-note row does not overwrite it.
    """
    rates = {}
    for cells in rows_of(html):
        code = None
        for c in cells:
            m = re.match(r"^([A-Z]{3})\b", c.strip())
            if m and m.group(1) not in ("NO.",):
                code = m.group(1)
                break
        if not code or code in rates:
            continue
        nums = [n for n in (tonum(c) for c in cells) if n is not None and n > 0]
        # drop a leading row-number like "1", "2" ...
        if len(nums) > 2 and nums[0] < 100 and float(nums[0]).is_integer():
            nums = nums[1:]
        if len(nums) < 2:
            continue
        name = next((c for c in cells
                     if not re.match(r"^[A-Z]{3}\b", c.strip())
                     and tonum(c) is None and len(c) > 2), code)
        rates[code] = {"name": name, "buy": nums[0], "sell": nums[-1]}
    return rates


def rows_of(html):
    return table_rows(html)


def find_date(html):
    for pat in (r"\d{4}-\d{2}-\d{2}", r"\d{1,2}-\d{1,2}-\d{4}", r"\d{1,2}/\d{1,2}/\d{4}"):
        m = re.search(pat, html)
        if m:
            return m.group(0)
    return ""


def sane(rates, label):
    usd = rates.get("USD")
    if not usd:
        print(f"  {label}: no USD row - dropping this source")
        return False
    if not (USD_MIN <= usd["buy"] <= USD_MAX and USD_MIN <= usd["sell"] <= USD_MAX):
        print(f"  {label}: USD {usd['buy']}/{usd['sell']} outside "
              f"{USD_MIN:.0f}-{USD_MAX:.0f} - dropping this source")
        return False
    return True


def source(label, url, tonum, verify):
    try:
        html = fetch(url, verify=verify)
    except Exception as e:                       # network/TLS: keep the other source
        print(f"  {label}: fetch failed ({type(e).__name__}) - skipped")
        return None
    rates = parse(html, tonum)
    if not rates or not sane(rates, label):
        return None
    print(f"  {label}: {len(rates)} currencies, USD "
          f"{rates['USD']['buy']:,.2f}/{rates['USD']['sell']:,.2f}")
    return {"date": find_date(html), "url": url, "rates": rates}


def main():
    print("Fetching LAK exchange rates ...")
    out = {"fetched": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ"), "sources": {}}
    bol = source("BOL", BOL_URL, num_eu, verify=False)   # their chain is incomplete
    bcel = source("BCEL", BCEL_URL, num_en, verify=True)
    if bol:
        out["sources"]["bol"] = bol
    if bcel:
        out["sources"]["bcel"] = bcel
    if not out["sources"]:
        print("No source could be read - leaving the existing rates.json alone.")
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {OUT} ({', '.join(out['sources'])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
