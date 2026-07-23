# -*- coding: utf-8 -*-
"""
ITRS Live Report - dashboard build script (v2)
==============================================

Reads every ITRS source file it can find, applies one cleaning layer,
pre-aggregates into compact JSON cubes, and bakes the result into a single
self-contained HTML file.

    python build_dashboard.py

Output: ITRS_Dashboard.html  (open in any browser - no server, no internet)

ADDING NEW DATA
---------------
Drop new files into the folders below and re-run. Nothing else to change:

    <root>/Payment/*.parquet      outflows
    <root>/Receive/*.parquet      inflows
    <root>/Payment/*.csv|.xlsx    also picked up
    <root>/Receive/*.csv|.xlsx

Flow direction comes from the folder name, so a 2026 file needs no new config.
Columns are matched by name and unioned, so extra or missing columns are fine.

CONFIG
------
    config/report_lines.json   weekly report line definitions - edit freely
"""

import argparse
import base64
import getpass
import gzip
import json
import os
import re
import secrets
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import duckdb
except ImportError:
    sys.exit("duckdb is required:  python -m pip install duckdb")

# PBKDF2 work factor. Raise it if you want more brute-force headroom; the cost
# is paid once per unlock, on the viewer's device.
KDF_ITERATIONS = 310_000

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG = HERE / "config"
TEMPLATE = HERE / "template.html"
OUTPUT = HERE / "ITRS_Dashboard.html"

DATA_EXT = (".parquet", ".csv", ".xlsx", ".xlsm")

# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------

PUR2_META = {
    "01": ("Goods", "ສິນຄ້າ", "current"),
    "02": ("Services", "ບໍລິການ", "current"),
    "03": ("Primary income", "ລາຍໄດ້ຂັ້ນໜຶ່ງ", "current"),
    "04": ("Secondary income", "ລາຍໄດ້ຂັ້ນສອງ", "current"),
    "05": ("Financial account", "ບັນຊີການເງິນ", "financial"),
    "06": ("Unclassifiable", "ບໍ່ສາມາດຈັດປະເພດ", "other"),
    "07": ("Goods, no cross-border movement", "ສິນຄ້າບໍ່ມີການສົ່ງອອກ", "current"),
    "99": ("Unclassified", "ບໍ່ໄດ້ຈັດປະເພດ", "other"),
}

# Plausible LAK-per-USD band. Medians run 8,258 (2017) to 21,483 (2025).
FX_MIN, FX_MAX = 7000.0, 25000.0

SIZE_BUCKETS = [
    (0, 1_000, "< $1k"), (1_000, 10_000, "$1k-$10k"),
    (10_000, 100_000, "$10k-$100k"), (100_000, 1_000_000, "$100k-$1m"),
    (1_000_000, 10_000_000, "$1m-$10m"), (10_000_000, float("inf"), "> $10m"),
]

# (column in the cleaned `tx` view, English label, Lao label)
QUALITY_FIELDS = [
    ("sub_date", "Submission date", "ວັນທີສົ່ງ"),
    ("country", "Counterpart country", "ປະເທດຄູ່ຮ່ວມ"),
    ("currency", "Currency", "ສະກຸນເງິນ"),
    ("usd", "Amount (USD)", "ມູນຄ່າ (USD)"),
    ("method_raw", "Transfer method", "ວິທີໂອນ"),
    ("lsic", "LSIC industry code", "ລະຫັດ LSIC"),
    ("tr_name", "Transferor name", "ຊື່ຜູ້ໂອນ"),
    ("rc_name", "Recipient name", "ຊື່ຜູ້ຮັບ"),
    ("tr_tin", "Transferor TIN", "ເລກ TIN ຜູ້ໂອນ"),
    ("rc_tin", "Recipient TIN", "ເລກ TIN ຜູ້ຮັບ"),
    ("docs", "Supporting documents", "ເອກະສານປະກອບ"),
]

COUNTRY_NAMES = {
    "CN": "China", "US": "United States", "HK": "Hong Kong SAR", "TH": "Thailand",
    "SG": "Singapore", "MO": "Macao SAR", "VN": "Viet Nam", "RU": "Russia",
    "GB": "United Kingdom", "AU": "Australia", "KR": "Korea, Rep.", "JP": "Japan",
    "AE": "United Arab Emirates", "MY": "Malaysia", "DE": "Germany", "FR": "France",
    "IN": "India", "ID": "Indonesia", "TW": "Taiwan", "CH": "Switzerland",
    "NL": "Netherlands", "IT": "Italy", "CA": "Canada", "BE": "Belgium",
    "KH": "Cambodia", "MM": "Myanmar", "PH": "Philippines", "NZ": "New Zealand",
    "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "FI": "Finland",
    "ES": "Spain", "AT": "Austria", "PL": "Poland", "TR": "Turkiye",
    "SA": "Saudi Arabia", "QA": "Qatar", "KW": "Kuwait", "IL": "Israel",
    "ZA": "South Africa", "BR": "Brazil", "MX": "Mexico", "AR": "Argentina",
    "CL": "Chile", "LU": "Luxembourg", "IE": "Ireland", "PT": "Portugal",
    "GR": "Greece", "CZ": "Czechia", "HU": "Hungary", "RO": "Romania",
    "UA": "Ukraine", "KZ": "Kazakhstan", "BD": "Bangladesh", "PK": "Pakistan",
    "LK": "Sri Lanka", "NP": "Nepal", "MN": "Mongolia", "BN": "Brunei",
    "LA": "Lao PDR", "VG": "British Virgin Islands", "KY": "Cayman Islands",
    "BM": "Bermuda", "PA": "Panama", "CY": "Cyprus", "MT": "Malta",
    "MU": "Mauritius", "SC": "Seychelles", "WS": "Samoa", "LI": "Liechtenstein",
    "MC": "Monaco", "AD": "Andorra", "IM": "Isle of Man", "JE": "Jersey",
    "GG": "Guernsey", "BS": "Bahamas", "BB": "Barbados", "CR": "Costa Rica",
    "EG": "Egypt", "NG": "Nigeria", "KE": "Kenya", "MA": "Morocco",
    "BH": "Bahrain", "OM": "Oman", "JO": "Jordan", "LB": "Lebanon",
    "IQ": "Iraq", "IR": "Iran", "AF": "Afghanistan", "UZ": "Uzbekistan",
    "BY": "Belarus", "RS": "Serbia", "HR": "Croatia", "SI": "Slovenia",
    "SK": "Slovakia", "BG": "Bulgaria", "EE": "Estonia", "LV": "Latvia",
    "LT": "Lithuania", "IS": "Iceland", "MV": "Maldives", "FJ": "Fiji",
    "PG": "Papua New Guinea", "TL": "Timor-Leste", "MG": "Madagascar",
}

# How many entities to embed in the searchable index, and how many of each
# entity's largest transactions to carry with it.
ENTITY_LIMIT = 4000
ENTITY_TX_LIMIT = 12
EXCEPTION_LIMIT = 300
# Currencies carried at daily grain for the weekly report's currency paragraph.
DAILY_CURRENCIES = 10
# Minimum daily total, in USD, for an entity to be carried at daily grain.
# The narrative names movers; smaller amounts would never be printed.
ENTITY_DAY_MIN = 500_000


# --------------------------------------------------------------------------
# Text cleaning
# --------------------------------------------------------------------------

def _mojibake_bytes(s):
    """Recover the original byte sequence from CP1252-mangled text.

    A plain ``s.encode('cp1252')`` is not enough: bytes 0x81, 0x8D, 0x8F, 0x90
    and 0x9D are undefined in CP1252, so a decoder that hit them passed them
    through unchanged as control characters. The encode therefore has to fall
    back to the raw code point for anything CP1252 cannot represent.
    """
    out = bytearray()
    for ch in s:
        try:
            out += ch.encode("cp1252")
        except UnicodeEncodeError:
            cp = ord(ch)
            if cp > 0xFF:
                return None
            out.append(cp)
    return bytes(out)


def fix_mojibake(s):
    """Repair UTF-8 text that was decoded as CP1252 and re-encoded."""
    if not s or not re.search(r"[ÃÂàáâãäåæçèéêëìíîï]", s):
        return s
    raw = _mojibake_bytes(s)
    if raw is None:
        return s
    try:
        repaired = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return s
    return repaired if not re.search(r"[Ãàº»]", repaired) else s


def norm_text(s):
    if s is None:
        return None
    s = unicodedata.normalize("NFC", str(s))
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def clean_name(s):
    """Normalise an entity name for grouping: repair, collapse, strip noise."""
    s = norm_text(fix_mojibake(s) if s else s)
    if not s:
        return None
    s = s.strip(" .,-_/\\\"'")
    return s or None


# --------------------------------------------------------------------------
# Source discovery
# --------------------------------------------------------------------------

def discover(folder):
    """Every readable data file in a flow folder, newest extension wins."""
    d = ROOT / folder
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir()
                  if p.is_file() and p.suffix.lower() in DATA_EXT
                  and not p.name.startswith("~$"))


def reader_sql(path):
    ext = path.suffix.lower()
    p = str(path).replace("'", "''")
    if ext == ".parquet":
        return f"SELECT * FROM read_parquet('{p}', union_by_name = true)"
    if ext == ".csv":
        return f"SELECT * FROM read_csv('{p}', header = true, sample_size = -1, all_varchar = false)"
    return f"SELECT * FROM st_read('{p}')"      # xlsx/xlsm via spatial ext


def build_connection():
    con = duckdb.connect()
    con.execute("PRAGMA threads=4")

    sources = {"Payment": discover("Payment"), "Receive": discover("Receive")}
    if not any(sources.values()):
        sys.exit(f"No data files found under {ROOT / 'Payment'} or {ROOT / 'Receive'}")

    needs_excel = any(p.suffix.lower() in (".xlsx", ".xlsm")
                      for v in sources.values() for p in v)
    if needs_excel:
        try:
            con.execute("INSTALL spatial; LOAD spatial;")
        except Exception as e:                                    # noqa: BLE001
            print(f"  ! Excel support unavailable ({e}); skipping .xlsx/.xlsm")
            for k in sources:
                sources[k] = [p for p in sources[k]
                              if p.suffix.lower() not in (".xlsx", ".xlsm")]

    parts = []
    for flow, files in sources.items():
        for p in files:
            # Flow comes from the folder, so a file with no Flow column still works.
            parts.append(f"SELECT *, '{flow}' AS _flow FROM ({reader_sql(p)})")
    con.execute("CREATE VIEW raw AS " + "\nUNION ALL BY NAME\n".join(parts))

    files_used = [p.name for v in sources.values() for p in v]
    print(f"  {len(files_used)} source files "
          f"({len(sources['Payment'])} payment, {len(sources['Receive'])} receive)")
    return con, files_used


def create_clean_view(con):
    """The single cleaning layer every cube reads from."""
    con.execute(f"""
        CREATE VIEW tx_all AS
        SELECT
            Hash_ID,
            NULLIF(TRIM(Banks), '')                                   AS bank,
            COALESCE(NULLIF(TRIM(Flow), ''), _flow)                   AS flow,
            COALESCE(Source_Year, YEAR(Date_of_Transaction))          AS yr,
            Date_of_Transaction                                       AS tx_date,
            strftime(Date_of_Transaction, '%Y-%m')                    AS ym,
            strftime(Date_of_Transaction, '%Y-%m-%d')                 AS ymd,
            Date_of_Submission                                        AS sub_date,
            date_diff('day', Date_of_Transaction, Date_of_Submission) AS lag_days,
            NULLIF(TRIM(Reference_Number), '')                        AS ref,

            COALESCE(NULLIF(TRIM(Pur_5), ''), '')                     AS pur5,
            NULLIF(TRIM(Purpose_Name), '')                            AS purpose_name,
            UPPER(NULLIF(TRIM(Country_Code), ''))                     AS country,
            UPPER(NULLIF(TRIM(Currency_Code), ''))                    AS currency,
            NULLIF(TRIM(Transfer_Method), '')                         AS method_raw,
            NULLIF(TRIM(LSIC_Code), '')                               AS lsic,
            NULLIF(TRIM(Transferor_Name), '')                         AS tr_name,
            NULLIF(TRIM(Recipient_Name), '')                          AS rc_name,
            NULLIF(TRIM(Transferor_TIN), '')                          AS tr_tin,
            NULLIF(TRIM(Recipient_TIN), '')                           AS rc_tin,
            NULLIF(TRIM(Supporting_Documents), '')                    AS docs,

            Amount_Transferred                                        AS amt_orig,
            Amount_USD                                                AS usd,
            Amount_Kip                                                AS kip,
            Exchange_Rates_USD                                        AS fx,
            -- The original-currency rate (LAK per unit). This is where a
            -- misapplied-currency error lives: a USD transfer that carries a
            -- THB-magnitude rate here converts to the wrong number of kip.
            Exchange_Rates_Kip                                        AS fxkip,
            Source_File,                                             -- for the Data Sources panel

            ("Use"     = 'Yes')                                       AS use_flag,
            (M2        = 'Yes')                                       AS m2_flag,
            (Move_Fund = 'Yes')                                       AS move_flag,

            (Exchange_Rates_USD IS NULL
                OR Exchange_Rates_USD < {FX_MIN}
                OR Exchange_Rates_USD > {FX_MAX})                     AS bad_fx,
            (Amount_USD IS NULL)                                      AS null_amt,
            (Amount_USD = 0)                                          AS zero_amt,
            (Amount_USD < 0)                                          AS neg_amt,
            (Date_of_Submission < Date_of_Transaction)                AS future_dated,
            (date_diff('day', Date_of_Transaction, Date_of_Submission) > 30) AS late_sub
        FROM raw
        WHERE Date_of_Transaction IS NOT NULL
    """)

    # A bank that submits the same transaction twice must not be counted twice.
    # Identity is bank + flow + date + reference + currency + amount; a blank or
    # too-short reference cannot establish identity, so those rows are always
    # kept rather than risk collapsing genuinely repeated small transfers.
    con.execute("""
        CREATE VIEW tx_ranked AS
        SELECT *,
          CASE WHEN ref IS NOT NULL AND LENGTH(ref) >= 4
               THEN ROW_NUMBER() OVER (
                    PARTITION BY bank, flow, ymd, ref, currency, amt_orig
                    ORDER BY Hash_ID)
               ELSE 1 END AS dup_rank,
          CASE WHEN ref IS NOT NULL AND LENGTH(ref) >= 4
               THEN COUNT(*) OVER (
                    PARTITION BY bank, flow, ymd, ref, currency, amt_orig)
               ELSE 1 END AS dup_count
        FROM tx_all
    """)
    # Every cube reads `tx`, so the whole report is deduplicated by construction.
    con.execute("CREATE VIEW tx AS SELECT * FROM tx_ranked WHERE dup_rank = 1")

    kept, total = con.execute(
        "SELECT (SELECT COUNT(*) FROM tx), (SELECT COUNT(*) FROM tx_all)").fetchone()
    if total > kept:
        print(f"  deduplicated: {total - kept:,} repeat submissions removed "
              f"({kept:,} of {total:,} kept)")


# --------------------------------------------------------------------------
# Dimensions
# --------------------------------------------------------------------------

BANK_ALIAS_MAP = {}


def bank_key(code):
    """Case and punctuation carry no meaning in a hand-typed bank code."""
    return "".join(ch for ch in str(code).upper() if ch.isalnum())


def load_bank_aliases():
    """Optional operator-confirmed merges, keyed on bank_key() of each spelling.

    Only for cases the automatic rule cannot decide, e.g. {"SACOMBANK": "SACOM"}.
    """
    path = CONFIG / "bank_aliases.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8")).get("aliases", {})
    except Exception as e:
        print(f"  WARNING: bank_aliases.json unreadable ({e}); ignoring")
        return {}
    return {bank_key(k): bank_key(v) for k, v in raw.items() if str(k).strip()}


def load_bank_officers():
    """Who checks which reporting bank, and each bank's full Lao name.

    Supplied by the BOP team and treated as the authority: a code missing here
    is shown as unassigned rather than guessed at.
    """
    path = CONFIG / "bank_officers.json"
    if not path.exists():
        return {}, {}
    cfg = json.loads(path.read_text(encoding="utf-8"))
    return cfg.get("officers", {}), cfg.get("names_lo", {})


def load_bop_rules():
    """Classification rules, shown on the dashboard's Settings tab.

    Carried into the payload so the rules that produced the figures travel
    with them: a reader can see exactly which conditions were in force."""
    path = CONFIG / "bop_rules.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_purpose_names():
    """Official ITRS purpose names, Lao and English, from Purpose_manual.

    The reporting banks' own wording is kept as a fallback for codes the
    official list does not cover."""
    path = CONFIG / "purpose_names.json"
    if not path.exists():
        print(f"  ! {path.name} not found; purposes stay in the banks' wording")
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("names", {})


def load_country_names_lo():
    """Lao country names, taken from the existing BOL time-series workbook so the
    dashboard reads the same way as the reports people already know."""
    path = CONFIG / "country_names_lo.json"
    if not path.exists():
        print(f"  ! {path.name} not found; Lao view will fall back to English names")
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("names", {})


def load_report_config():
    path = CONFIG / "report_lines.json"
    if not path.exists():
        sys.exit(f"Missing config file: {path}")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    lines = sorted(cfg["lines"], key=lambda l: l["order"])
    return cfg, lines


def report_line_for(pur5, lines):
    """Longest-prefix match of a Pur_5 code to a report line."""
    best, best_len = None, -1
    catch = None
    for i, ln in enumerate(lines):
        if ln.get("catchAll"):
            catch = i
        for code in ln["codes"]:
            if pur5.startswith(code) and len(code) > best_len:
                best, best_len = i, len(code)
    return best if best is not None else (catch if catch is not None else len(lines) - 1)


def build_dimensions(con, lines):
    dims = {}

    dims["months"] = [r[0] for r in con.execute(
        "SELECT DISTINCT ym FROM tx WHERE ym IS NOT NULL ORDER BY 1").fetchall()]
    dims["years"] = [int(r[0]) for r in con.execute(
        "SELECT DISTINCT yr FROM tx WHERE yr IS NOT NULL ORDER BY 1").fetchall()]

    # --- purposes ---------------------------------------------------------
    votes = defaultdict(Counter)
    for code, name, c in con.execute("""
            SELECT pur5, purpose_name, COUNT(*) FROM tx
            WHERE pur5 <> '' AND purpose_name IS NOT NULL GROUP BY 1, 2""").fetchall():
        votes[code][norm_text(fix_mojibake(name))] += c

    official = load_purpose_names()
    codes = [r[0] for r in con.execute(
        "SELECT pur5 FROM tx WHERE pur5 <> '' GROUP BY 1 ORDER BY 1").fetchall()]
    seen, purposes = set(), []
    for code in codes:
        code = str(code).strip()
        if len(code) not in (2, 4, 6):
            code = code[:6] if len(code) > 6 else code[:4] if len(code) > 4 else code[:2]
        if code in seen:
            continue
        seen.add(code)
        best = votes.get(code)
        off = official.get(code, {})
        # Purpose_manual does not list every 2-digit heading (05 is missing),
        # so fall back to the BOP category name for those.
        if not off.get("en") and code in PUR2_META:
            off = dict(off, en=PUR2_META[code][0])
        purposes.append({
            "code": code,
            "en": off.get("en"),
            "name": off.get("lo") or (best.most_common(1)[0][0] if best else None) or code,
            "parent": code[:-2] if len(code) > 2 else None,
            "pur2": code[:2] if code[:2] in PUR2_META else "99",
            "line": report_line_for(code, lines),
        })
    dims["purposes"] = sorted(purposes, key=lambda p: p["code"])

    # The six Goods commodities the Period Report breaks out. "side" is the
    # measured dominant money-flow (home section); off-direction legs are still
    # shown where material, so this is a sort/label hint, not a hard filter.
    dims["goodsCodes"] = [
        {"code": "010107", "side": "in"},   # Minerals
        {"code": "010105", "side": "in"},   # Agriculture & forest
        {"code": "010102", "side": "in"},   # Electricity
        {"code": "010109", "side": "out"},  # Fuel (oil)
        {"code": "010110", "side": "out"},  # Vehicles
        {"code": "010301", "side": "out"},  # Gold
    ]

    # --- other dimensions -------------------------------------------------
    countries = [r[0] for r in con.execute("""
        SELECT country FROM tx WHERE country IS NOT NULL
        GROUP BY 1 ORDER BY SUM(usd) DESC NULLS LAST""").fetchall()]
    lo_names = load_country_names_lo()
    dims["countries"] = [
        {"code": c, "name": COUNTRY_NAMES.get(c, c), "lo": lo_names.get(c)}
        for c in countries
    ]

    dims["currencies"] = [r[0] for r in con.execute("""
        SELECT currency FROM tx WHERE currency IS NOT NULL
        GROUP BY 1 ORDER BY SUM(usd) DESC NULLS LAST""").fetchall()]

    # The bank field is typed by hand and the same bank arrives under several
    # spellings. Case and punctuation are collapsed automatically; anything
    # needing judgement stays separate and is reported, since merging two real
    # banks would corrupt the figures. bank_lk maps every raw spelling to the
    # canonical index, so every downstream join keeps working unchanged.
    raw_banks = con.execute("""
        SELECT bank, SUM(usd) AS usd, COUNT(*) AS n FROM tx WHERE bank IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC NULLS LAST""").fetchall()
    aliases = load_bank_aliases()
    groups = {}
    for code, usd, n in raw_banks:
        key = aliases.get(bank_key(code), bank_key(code))
        g = groups.setdefault(key, {"variants": [], "usd": 0.0})
        g["variants"].append(code)
        g["usd"] += float(usd or 0)
    # Order banks by value, and label each group with its most-used spelling.
    ordered = sorted(groups.items(), key=lambda kv: -kv[1]["usd"])
    dims["banks"] = [g["variants"][0] for _, g in ordered]
    BANK_ALIAS_MAP.clear()
    for ix, (_, g) in enumerate(ordered):
        for v in g["variants"]:
            BANK_ALIAS_MAP[v] = ix
    merged = [(g["variants"][0], g["variants"][1:]) for _, g in ordered if len(g["variants"]) > 1]
    if merged:
        print(f"  bank spellings merged: {len(raw_banks)} raw -> {len(dims['banks'])} banks")
        for canon, others in merged:
            print(f"    {canon} <- {', '.join(others)}")

    raw_methods = [r[0] for r in con.execute(
        "SELECT DISTINCT method_raw FROM tx WHERE method_raw IS NOT NULL").fetchall()]
    method_map = {m: (norm_text(fix_mojibake(m)) or m) for m in raw_methods}
    dims["methods"] = sorted(set(method_map.values()))

    dims["pur2"] = [{"code": k, "en": v[0], "lo": v[1], "account": v[2]}
                    for k, v in sorted(PUR2_META.items())]
    dims["sizeBuckets"] = [b[2] for b in SIZE_BUCKETS]
    dims["qualityFields"] = [{"en": e, "lo": l} for _, e, l in QUALITY_FIELDS]
    dims["reportLines"] = [
        # `codes` travels to the client so in-browser imports can classify new
        # rows with the same longest-prefix rule the build uses.
        {"id": l["id"], "lo": l["lo"], "en": l["en"], "codes": list(l["codes"]),
         "catchAll": bool(l.get("catchAll")),
         "group": l.get("group", "financial"), "bankOwn": bool(l.get("bankOwn"))}
        for l in lines
    ]
    return dims, method_map


def register_lookups(con, dims, method_map):
    def tbl(name, cols, rows):
        con.execute(f"CREATE TABLE {name} ({cols})")
        con.executemany(
            f"INSERT INTO {name} VALUES ({','.join('?' * (cols.count(',') + 1))})", rows)

    tbl("purpose_lk", "pur5 VARCHAR, pur2 VARCHAR, ix INTEGER, line INTEGER",
        [(p["code"], p["pur2"], i, p["line"]) for i, p in enumerate(dims["purposes"])])
    mix = {m: i for i, m in enumerate(dims["methods"])}
    tbl("method_lk", "raw VARCHAR, ix INTEGER",
        [(r, mix[c]) for r, c in method_map.items()])
    tbl("month_lk", "ym VARCHAR, ix INTEGER",
        [(m, i) for i, m in enumerate(dims["months"])])
    tbl("year_lk", "yr BIGINT, ix INTEGER",
        [(y, i) for i, y in enumerate(dims["years"])])
    tbl("country_lk", "code VARCHAR, ix INTEGER",
        [(c["code"], i) for i, c in enumerate(dims["countries"])])
    tbl("curr_lk", "code VARCHAR, ix INTEGER",
        [(c, i) for i, c in enumerate(dims["currencies"])])
    # Every raw spelling, not just the canonical one, so joins on t.bank hit.
    tbl("bank_lk", "code VARCHAR, ix INTEGER",
        sorted(BANK_ALIAS_MAP.items(), key=lambda kv: kv[1]))
    tbl("pur2_lk", "code VARCHAR, ix INTEGER",
        [(p["code"], i) for i, p in enumerate(dims["pur2"])])

    con.execute("""
        CREATE VIEW txf AS
        SELECT t.*,
               COALESCE(p.ix, -1)                             AS purpose_ix,
               COALESCE(p.pur2, '99')                         AS pur2,
               COALESCE(p.line, -1)                           AS line_ix,
               COALESCE(m.ix, -1)                             AS method_ix,
               CASE WHEN t.flow = 'Payment' THEN 0 ELSE 1 END AS flow_ix,
               CASE WHEN t.use_flag THEN 1 ELSE 0 END         AS use_ix
        FROM tx t
        LEFT JOIN purpose_lk p ON p.pur5 = t.pur5
        LEFT JOIN method_lk  m ON m.raw  = t.method_raw
    """)


# --------------------------------------------------------------------------
# Cube helpers
# --------------------------------------------------------------------------

def r2(x, nd=3):
    if x is None:
        return 0
    v = round(float(x), nd)
    return 0 if v == 0 else v


def fetch_cube(con, sql):
    """Cube rows come back as [...int dims, count, usdMillions]."""
    out = []
    for row in con.execute(sql).fetchall():
        out.append([int(v) if v is not None else -1 for v in row[:-2]]
                   + [int(row[-2] or 0), r2(row[-1])])
    return out


def build_cubes(con, dims):
    cubes = {}
    base = """
        FROM txf t
        JOIN month_lk mo ON mo.ym = t.ym
        JOIN year_lk  y  ON y.yr  = t.yr
        LEFT JOIN pur2_lk p2 ON p2.code = t.pur2
    """

    cubes["month"] = fetch_cube(con, f"""
        SELECT mo.ix, t.flow_ix, p2.ix, t.use_ix, COALESCE(bk.ix,-1), COUNT(*), SUM(t.usd)/1e6
        {base} LEFT JOIN bank_lk bk ON bk.code = t.bank
        WHERE p2.ix IS NOT NULL GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5""")

    cubes["country"] = fetch_cube(con, f"""
        SELECT y.ix, t.flow_ix, c.ix, t.use_ix, COALESCE(bk.ix,-1), COUNT(*), SUM(t.usd)/1e6
        {base} JOIN country_lk c ON c.code = t.country
        LEFT JOIN bank_lk bk ON bk.code = t.bank
        GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5""")

    cubes["currency"] = fetch_cube(con, f"""
        SELECT y.ix, t.flow_ix, cu.ix, t.use_ix, COALESCE(bk.ix,-1), COUNT(*), SUM(t.usd)/1e6
        {base} JOIN curr_lk cu ON cu.code = t.currency
        LEFT JOIN bank_lk bk ON bk.code = t.bank
        GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5""")

    cubes["bank"] = fetch_cube(con, f"""
        SELECT y.ix, t.flow_ix, b.ix, t.use_ix, COUNT(*), SUM(t.usd)/1e6
        {base} JOIN bank_lk b ON b.code = t.bank
        GROUP BY 1,2,3,4 ORDER BY 1,2,3,4""")

    cubes["purpose"] = fetch_cube(con, f"""
        SELECT y.ix, t.flow_ix, t.purpose_ix, t.use_ix, COALESCE(bk.ix,-1), COUNT(*), SUM(t.usd)/1e6
        {base} LEFT JOIN bank_lk bk ON bk.code = t.bank
        WHERE t.purpose_ix >= 0 GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5""")

    cubes["method"] = fetch_cube(con, f"""
        SELECT y.ix, t.flow_ix, t.method_ix, t.use_ix, COUNT(*), SUM(t.usd)/1e6
        {base} WHERE t.method_ix >= 0 GROUP BY 1,2,3,4 ORDER BY 1,2,3,4""")

    case_sql = " ".join(f"WHEN t.usd >= {lo} AND t.usd < {hi} THEN {i}"
                        for i, (lo, hi, _) in enumerate(SIZE_BUCKETS) if hi != float("inf"))
    last = len(SIZE_BUCKETS) - 1
    cubes["size"] = fetch_cube(con, f"""
        SELECT * FROM (
          SELECT y.ix AS a, t.flow_ix AS b,
                 CASE {case_sql} WHEN t.usd >= {SIZE_BUCKETS[last][0]} THEN {last} ELSE -1 END AS c,
                 t.use_ix AS d, COUNT(*) AS n, SUM(t.usd)/1e6 AS v
          {base} WHERE t.usd IS NOT NULL AND t.usd > 0 GROUP BY 1,2,3,4
        ) WHERE c >= 0 ORDER BY a,b,c,d""")

    # ---- monthly cubes powering the Time Series tab ----------------------
    # [month, flow, dimension, useFlag, count, usdMillions]. The use flag has to
    # be a dimension here, not a filter, or the "Use in BOP" control cannot
    # reach this tab.
    mjoin = "FROM txf t JOIN month_lk mo ON mo.ym = t.ym"
    cubes["tsBank"] = fetch_cube(con, f"""
        SELECT mo.ix, t.flow_ix, b.ix, t.use_ix, COUNT(*), SUM(t.usd)/1e6
        {mjoin} JOIN bank_lk b ON b.code = t.bank
        GROUP BY 1,2,3,4 ORDER BY 1,2,3,4""")
    cubes["tsCountry"] = fetch_cube(con, f"""
        SELECT mo.ix, t.flow_ix, c.ix, t.use_ix, bk.ix, COUNT(*), SUM(t.usd)/1e6
        {mjoin} JOIN country_lk c ON c.code = t.country
                JOIN bank_lk bk ON bk.code = t.bank
        GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5""")
    cubes["tsCurrency"] = fetch_cube(con, f"""
        SELECT mo.ix, t.flow_ix, cu.ix, t.use_ix, bk.ix, COUNT(*), SUM(t.usd)/1e6
        {mjoin} JOIN curr_lk cu ON cu.code = t.currency
                JOIN bank_lk bk ON bk.code = t.bank
        GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5""")
    cubes["tsPurpose"] = fetch_cube(con, f"""
        SELECT mo.ix, t.flow_ix, t.purpose_ix, t.use_ix, bk.ix, COUNT(*), SUM(t.usd)/1e6
        {mjoin} JOIN bank_lk bk ON bk.code = t.bank
        WHERE t.purpose_ix >= 0 AND LENGTH(t.pur5) <= 4
        GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5""")

    # ---- daily cubes powering the Weekly Report --------------------------
    # [date, flow, reportLine, useFlag, bank, count, usdMillions]
    # The bank axis is what lets the Period Report answer "show me BCEL only".
    # -1 is not used here: every transaction has a reporting bank.
    cubes["dayLine"] = [
        [d, int(f), int(l), int(uix), int(b), int(n), r2(u)]
        for d, f, l, uix, b, n, u in con.execute("""
            SELECT t.ymd, t.flow_ix, t.line_ix, t.use_ix, bk.ix, COUNT(*), SUM(t.usd)/1e6
            FROM txf t JOIN bank_lk bk ON bk.code = t.bank
            WHERE t.line_ix >= 0
            GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5""").fetchall()
    ]
    # [date, flow, bank, useFlag, count, usdMillions] -- bank shares for any range.
    # Built by hand rather than via fetch_cube: the leading dimension is a date
    # string, which fetch_cube would try to coerce to an integer.
    cubes["dayBank"] = [
        [d, int(f), int(b), int(u), int(n), r2(v)]
        for d, f, b, u, n, v in con.execute("""
            SELECT t.ymd, t.flow_ix, b.ix, t.use_ix, COUNT(*), SUM(t.usd)/1e6
            FROM txf t JOIN bank_lk b ON b.code = t.bank
            GROUP BY 1,2,3,4 ORDER BY 1,2,3,4""").fetchall()
    ]

    # [date, flow, entity, useFlag, usdMillions] -- named contributors for the
    # narrative. Only daily totals at or above the threshold are carried: the
    # report names the movers, and keeping every small transfer would multiply
    # the file for names that would never be printed.
    ent_rows = con.execute(f"""
        SELECT t.ymd, t.flow_ix, TRIM(COALESCE(t.tr_name, t.rc_name)) AS nm,
               t.use_ix, SUM(t.usd)/1e6 AS v
        FROM txf t
        WHERE COALESCE(t.tr_name, t.rc_name) IS NOT NULL
        GROUP BY 1,2,3,4
        HAVING SUM(t.usd) >= {ENTITY_DAY_MIN}
        ORDER BY 1,2""").fetchall()
    # The narrative names the largest movers, so a placeholder here is printed
    # as though it were a company: "NULL 289.99 million" appeared in a report.
    # Same rule as the entity index, applied at the only other place names
    # reach the reader.
    _block = set(load_name_blocklist())
    def _real_name(nm):
        # Test the cleaned form as well as the raw one: clean_name strips
        # punctuation and spacing, so "0010-0182 6211" only becomes a bare
        # account number after cleaning, and that is the form the reader sees.
        for v in ((nm or "").strip(), (clean_name(nm) or "").strip()):
            if not v or len(v) < 3 or v.upper() in _block or v.isdigit():
                return False
            if not any(c.isalpha() for c in v):
                return False
        return True
    dropped_n = len(ent_rows)
    ent_rows = [r for r in ent_rows if _real_name(r[2])]
    print(f"  narrative names: dropped {dropped_n - len(ent_rows):,} placeholder rows")
    big_names, big_ix = [], {}
    for _, _, nm, _, _ in ent_rows:
        cleaned = clean_name(nm) or nm
        if cleaned not in big_ix:
            big_ix[cleaned] = len(big_names)
            big_names.append(cleaned)
    dims["bigEntities"] = big_names
    cubes["dayEntity"] = [
        [d, int(f), big_ix[clean_name(nm) or nm], int(u), r2(v)]
        for d, f, nm, u, v in ent_rows
    ]

    # [month, flow, reportLine, useFlag, bank, count, usdMillions]
    # The BOP report lines are the categories the compilation actually uses, so
    # the time series can be read by real BOP item rather than by purpose code.
    cubes["tsLine"] = fetch_cube(con, f"""
        SELECT mo.ix, t.flow_ix, t.line_ix, t.use_ix, bk.ix, COUNT(*), SUM(t.usd)/1e6
        {mjoin} JOIN bank_lk bk ON bk.code = t.bank
        WHERE t.line_ix >= 0
        GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5""")

    # [date, flow, currency, useFlag, usdMillions]
    cubes["dayCurrency"] = [
        [d, int(f), int(c), int(uix), r2(u)]
        for d, f, c, uix, u in con.execute(f"""
            SELECT t.ymd, t.flow_ix, cu.ix, t.use_ix, SUM(t.usd)/1e6
            FROM txf t JOIN curr_lk cu ON cu.code = t.currency
            WHERE cu.ix < {DAILY_CURRENCIES}
            GROUP BY 1,2,3,4 ORDER BY 1,2,3,4""").fetchall()
    ]

    # dayGoods: [date, flow, goodsCode index 0-5, useFlag, usdMillions].
    # The six commodity sub-items of the Goods report line, kept per day so the
    # Period Report can break Goods down for any date range. Both directions are
    # carried because both are displayed (imports pay out fuel/vehicles/gold;
    # exports receive minerals/agriculture/electricity, but each has an
    # off-direction leg worth showing).
    cubes["dayGoods"] = [
        [d, int(f), int(g), int(uix), r2(u)]
        for d, f, g, uix, u in con.execute("""
            SELECT t.ymd, t.flow_ix, gc.ix, t.use_ix, SUM(t.usd)/1e6
            FROM txf t
            JOIN (VALUES ('010107',0),('010105',1),('010102',2),
                         ('010109',3),('010110',4),('010301',5)) AS gc(code, ix)
              ON gc.code = t.pur5
            WHERE t.usd IS NOT NULL
            GROUP BY 1,2,3,4
            HAVING ROUND(SUM(t.usd)/1e6, 3) <> 0
            ORDER BY 1,2,3,4""").fetchall()
    ]
    print(f"  dayGoods         {len(cubes['dayGoods']):,} rows")
    # Guard against a direction-inversion wiring bug: each code's declared side
    # must match its measured dominant money-flow over the whole range.
    _dom = {c: side for c, side in con.execute("""
        SELECT t.pur5,
               CASE WHEN SUM(CASE WHEN t.flow_ix=1 THEN t.usd ELSE 0 END)
                       > SUM(CASE WHEN t.flow_ix=0 THEN t.usd ELSE 0 END)
                    THEN 'in' ELSE 'out' END
        FROM txf t WHERE t.pur5 IN ('010107','010105','010102','010109','010110','010301')
        GROUP BY 1""").fetchall()}
    for gc in dims["goodsCodes"]:
        assert _dom.get(gc["code"]) == gc["side"], \
            f"goods side mismatch {gc['code']}: measured {_dom.get(gc['code'])}, declared {gc['side']}"

    # ---- reporting quality ----------------------------------------------
    cubes["lag"] = [
        [int(a), int(b), r2(c, 1), r2(d, 1), int(e), int(f), int(g)]
        for a, b, c, d, e, f, g in con.execute("""
            SELECT y.ix, bk.ix, MEDIAN(t.lag_days), QUANTILE_CONT(t.lag_days, 0.9),
                   COUNT(*) FILTER (WHERE t.late_sub), COUNT(*) FILTER (WHERE t.future_dated), COUNT(*)
            FROM txf t JOIN year_lk y ON y.yr = t.yr JOIN bank_lk bk ON bk.code = t.bank
            WHERE t.lag_days IS NOT NULL GROUP BY 1,2 ORDER BY 1,2""").fetchall()
    ]
    cubes["quality"] = [
        [int(x or 0) for x in row]
        for row in con.execute("""
            SELECT y.ix, bk.ix, COUNT(*),
                   COUNT(*) FILTER (WHERE t.bad_fx),
                   COUNT(*) FILTER (WHERE t.null_amt),
                   COUNT(*) FILTER (WHERE t.zero_amt),
                   COUNT(*) FILTER (WHERE t.neg_amt),
                   COUNT(*) FILTER (WHERE t.future_dated),
                   COUNT(*) FILTER (WHERE t.pur2 = '99'),
                   COUNT(*) FILTER (WHERE t.country IS NULL),
                   COUNT(*) FILTER (WHERE t.method_raw IS NULL),
                   COUNT(*) FILTER (WHERE t.lsic IS NULL)
            FROM txf t JOIN year_lk y ON y.yr = t.yr JOIN bank_lk bk ON bk.code = t.bank
            GROUP BY 1,2 ORDER BY 1,2""").fetchall()
    ]
    field_sql = ", ".join(
        f"COUNT(*) FILTER (WHERE \"{c}\" IS NULL OR TRIM(CAST(\"{c}\" AS VARCHAR)) = '')"
        for c, _, _ in QUALITY_FIELDS)
    cubes["completeness"] = [
        [int(v) for v in row]
        for row in con.execute(f"""
            SELECT y.ix, COUNT(*), {field_sql}
            FROM tx r JOIN year_lk y ON y.yr = r.yr GROUP BY 1 ORDER BY 1""").fetchall()
    ]
    cubes["concentration"] = [
        [int(a), int(b), r2(c, 2), r2(d, 2), r2(e, 2)]
        for a, b, c, d, e in con.execute("""
            WITH ranked AS (
                SELECT y.ix AS yix, t.flow_ix, t.usd,
                       ROW_NUMBER() OVER (PARTITION BY y.ix, t.flow_ix ORDER BY t.usd DESC) AS rn,
                       SUM(t.usd) OVER (PARTITION BY y.ix, t.flow_ix) AS tot
                FROM txf t JOIN year_lk y ON y.yr = t.yr WHERE t.usd > 0 AND t.use_ix = 1)
            SELECT yix, flow_ix,
                   100*SUM(usd) FILTER (WHERE rn<=10)/ANY_VALUE(tot),
                   100*SUM(usd) FILTER (WHERE rn<=100)/ANY_VALUE(tot),
                   100*SUM(usd) FILTER (WHERE rn<=1000)/ANY_VALUE(tot)
            FROM ranked GROUP BY 1,2 ORDER BY 1,2""").fetchall()
    ]
    cubes["flags"] = [
        [int(a), int(b), int(c), int(d), int(e), int(f), r2(g)]
        for a, b, c, d, e, f, g in con.execute("""
            SELECT y.ix, t.flow_ix,
                   CASE WHEN t.use_flag THEN 1 ELSE 0 END,
                   CASE WHEN t.m2_flag THEN 1 ELSE 0 END,
                   CASE WHEN t.move_flag THEN 1 ELSE 0 END,
                   COUNT(*), SUM(t.usd)/1e6
            FROM txf t JOIN year_lk y ON y.yr = t.yr
            GROUP BY 1,2,3,4,5 ORDER BY 1,2""").fetchall()
    ]
    return cubes


# --------------------------------------------------------------------------
# Exceptions: FX errors and duplicates
# --------------------------------------------------------------------------

def build_exceptions(con, dims):
    """Row-level exception lists, capped so the HTML stays small."""
    exc = {}
    bank_ix = {b: i for i, b in enumerate(dims["banks"])}
    curr_ix = {c: i for i, c in enumerate(dims["currencies"])}

    # ---- FX rate errors --------------------------------------------------
    exc["fxByYearBank"] = [
        [int(a), int(b), int(c), int(d), int(e)]
        for a, b, c, d, e in con.execute("""
            SELECT y.ix, bk.ix,
                   COUNT(*) FILTER (WHERE t.fx IS NULL),
                   COUNT(*) FILTER (WHERE t.fx IS NOT NULL AND t.fx < {lo}),
                   COUNT(*) FILTER (WHERE t.fx > {hi})
            FROM txf t JOIN year_lk y ON y.yr = t.yr JOIN bank_lk bk ON bk.code = t.bank
            WHERE t.bad_fx GROUP BY 1,2 ORDER BY 1,2""".format(lo=FX_MIN, hi=FX_MAX)).fetchall()
    ]
    # Per (currency, year) median original-currency rate. This is the reference
    # a transaction's own rate is judged against, and the client uses it to name
    # which currency a wrong rate actually belongs to.
    ccy_rates = {}
    for c, y, m, n in con.execute("""
            SELECT currency, yr, MEDIAN(fxkip) AS m, COUNT(*) AS n
            FROM tx WHERE fxkip > 0 AND currency IS NOT NULL
            GROUP BY 1,2 HAVING COUNT(*) >= 30""").fetchall():
        ci = curr_ix.get(c)
        if ci is not None:
            ccy_rates.setdefault(ci, {})[int(y)] = r2(m, 3)
    exc["ccyRates"] = ccy_rates

    # Totals across every transaction (not just the shown top 300), so the tab
    # can state how many wrong-rate / out-of-band / missing records exist.
    # medfx: the year's own LAK/USD rate. The band check is judged against it
    # rather than a fixed 7,000-25,000 window, because the real rate rose from
    # ~8,200 in 2017 to ~22,000 in 2024 and a fixed band mis-reads both ends.
    fc = con.execute(f"""
        WITH med AS (SELECT currency, yr, MEDIAN(fxkip) AS m FROM tx
                     WHERE fxkip > 0 GROUP BY 1,2 HAVING COUNT(*) >= 30),
             medfx AS (SELECT yr, MEDIAN(fx) AS mf FROM tx WHERE fx > 0 GROUP BY 1)
        SELECT
          COUNT(*) FILTER (WHERE t.usd IS NULL) AS missing,
          COUNT(*) FILTER (WHERE t.usd <= 0) AS nonpos,
          COUNT(*) FILTER (WHERE med.m IS NOT NULL AND t.fxkip > 0
                           AND (t.fxkip > med.m*3 OR t.fxkip < med.m/3)) AS wrongrate,
          COUNT(*) FILTER (WHERE t.usd > 0 AND medfx.mf IS NOT NULL AND t.fx > 0
                           AND (t.fx > medfx.mf*2 OR t.fx < medfx.mf/2)) AS band
        FROM tx t LEFT JOIN med ON med.currency = t.currency AND med.yr = t.yr
                  LEFT JOIN medfx ON medfx.yr = t.yr
    """).fetchone()
    exc["fxCounts"] = {"missing": int(fc[0]), "nonpos": int(fc[1] or 0),
                       "wrongrate": int(fc[2] or 0), "band": int(fc[3] or 0)}

    # ---- Wrong-currency rate: the rate a bank filed sits far from what that
    # currency was actually worth, almost always because they applied another
    # currency's rate (a USD transfer that used the THB rate, and so on). Ranked
    # by how much the mistake distorts the USD figure, since that is what feeds
    # the balance of payments. The band check (null / absurd USD) stays too.
    exc["fxRows"] = [
        {"id": hid, "d": d, "b": bank_ix.get(b, -1), "f": 0 if fl == "Payment" else 1,
         "c": curr_ix.get(c, -1), "amt": r2(amt, 2),
         "rate": None if rate is None else r2(rate, 3),
         "exp": None if exp is None else r2(exp, 3),
         "usd": r2(usd, 2), "ref": ref,
         "kind": kind}
        for hid, d, b, fl, c, amt, rate, exp, usd, ref, kind in con.execute(f"""
            WITH med AS (
                SELECT currency, yr, MEDIAN(fxkip) AS m FROM tx
                WHERE fxkip > 0 GROUP BY 1,2 HAVING COUNT(*) >= 30),
                 medfx AS (SELECT yr, MEDIAN(fx) AS mf FROM tx WHERE fx > 0 GROUP BY 1)
            SELECT t.Hash_ID, t.ymd, t.bank, t.flow, t.currency, t.amt_orig,
                   t.fxkip, med.m, t.usd, t.ref,
                   CASE
                     WHEN t.usd IS NULL THEN 'missing'
                     WHEN t.usd <= 0 THEN 'nonpos'
                     WHEN med.m IS NOT NULL AND t.fxkip > 0
                          AND (t.fxkip > med.m * 3 OR t.fxkip < med.m / 3) THEN 'wrongrate'
                     WHEN medfx.mf IS NOT NULL AND t.fx > 0
                          AND (t.fx > medfx.mf * 2 OR t.fx < medfx.mf / 2) THEN 'band'
                     ELSE 'ok' END AS kind
            FROM tx t LEFT JOIN med ON med.currency = t.currency AND med.yr = t.yr
                      LEFT JOIN medfx ON medfx.yr = t.yr
            WHERE (t.usd IS NULL OR t.usd <= 0
                   OR (medfx.mf IS NOT NULL AND t.fx > 0
                       AND (t.fx > medfx.mf * 2 OR t.fx < medfx.mf / 2))
                   OR (med.m IS NOT NULL AND t.fxkip > 0
                       AND (t.fxkip > med.m * 3 OR t.fxkip < med.m / 3)))
            ORDER BY CASE WHEN med.m IS NOT NULL AND t.fxkip > 0
                          THEN ABS(COALESCE(t.usd,0) * (med.m / t.fxkip) - COALESCE(t.usd,0))
                          ELSE COALESCE(t.usd, 0) END DESC, t.ymd
            LIMIT {EXCEPTION_LIMIT}""").fetchall()
    ]

    # ---- Duplicates, three confidence tiers ------------------------------
    # A: same bank+flow+reference+date+amount+currency. A reference number is
    #    meant to be unique per bank, so a collision is a near-certain re-send.
    con.execute(f"""
        CREATE VIEW dupA AS
        SELECT bank, flow, ref, ymd, amt_orig, currency, yr,
               COUNT(*) AS c, SUM(usd) AS usd, ANY_VALUE(country) AS country,
               ANY_VALUE(COALESCE(tr_name, rc_name)) AS nm
        FROM tx_all
        WHERE ref IS NOT NULL AND LENGTH(ref) >= 4
        GROUP BY 1,2,3,4,5,6,7 HAVING COUNT(*) > 1""")
    # B: no usable reference, but every business key matches AND both entity
    #    names are present - strong evidence of a genuine double entry.
    con.execute("""
        CREATE VIEW dupB AS
        SELECT bank, flow, ymd, amt_orig, currency, country, pur5, tr_name, rc_name, yr,
               COUNT(*) AS c, SUM(usd) AS usd
        FROM tx_all
        WHERE tr_name IS NOT NULL AND rc_name IS NOT NULL
        GROUP BY 1,2,3,4,5,6,7,8,9,10 HAVING COUNT(*) > 1""")
    # C: same business key but entity names missing, so it cannot be told
    #    apart from genuinely repeated small transfers. Reported, not counted.
    con.execute("""
        CREATE VIEW dupC AS
        SELECT bank, flow, ymd, amt_orig, currency, country, pur5, yr,
               COUNT(*) AS c, SUM(usd) AS usd
        FROM tx_all
        WHERE tr_name IS NULL AND rc_name IS NULL
        GROUP BY 1,2,3,4,5,6,7,8 HAVING COUNT(*) > 1""")

    exc["dupSummary"] = {}
    for tier in ("A", "B", "C"):
        row = con.execute(f"""
            SELECT COUNT(*), SUM(c - 1), SUM(usd * (c - 1) / c) / 1e6 FROM dup{tier}""").fetchone()
        exc["dupSummary"][tier] = {
            "groups": int(row[0] or 0), "extra": int(row[1] or 0), "usdM": r2(row[2])}

    exc["dupByYearBank"] = {}
    for tier in ("A", "B", "C"):
        exc["dupByYearBank"][tier] = [
            [int(a), int(b), int(c), r2(d)]
            for a, b, c, d in con.execute(f"""
                SELECT y.ix, bk.ix, SUM(d.c - 1), SUM(d.usd * (d.c - 1) / d.c) / 1e6
                FROM dup{tier} d JOIN year_lk y ON y.yr = d.yr
                JOIN bank_lk bk ON bk.code = d.bank
                GROUP BY 1,2 ORDER BY 1,2""").fetchall()
        ]

    exc["dupRows"] = {
        "A": [{"d": d, "b": bank_ix.get(b, -1), "f": 0 if fl == "Payment" else 1,
               "c": curr_ix.get(c, -1), "amt": r2(a, 2), "n": int(n),
               "usd": r2(u, 2), "ref": rf, "nm": clean_name(nm), "ctry": ct}
              for d, b, fl, c, a, n, u, rf, nm, ct in con.execute(f"""
                  SELECT ymd, bank, flow, currency, amt_orig, c, usd, ref, nm, country
                  FROM dupA ORDER BY usd * (c - 1) / c DESC LIMIT {EXCEPTION_LIMIT}""").fetchall()],
        "B": [{"d": d, "b": bank_ix.get(b, -1), "f": 0 if fl == "Payment" else 1,
               "c": curr_ix.get(c, -1), "amt": r2(a, 2), "n": int(n),
               "usd": r2(u, 2), "ref": None, "nm": clean_name(tn), "nm2": clean_name(rn),
               "ctry": ct, "pur": p}
              for d, b, fl, c, a, n, u, tn, rn, ct, p in con.execute(f"""
                  SELECT ymd, bank, flow, currency, amt_orig, c, usd, tr_name, rc_name, country, pur5
                  FROM dupB ORDER BY usd * (c - 1) / c DESC LIMIT {EXCEPTION_LIMIT}""").fetchall()],
        "C": [{"d": d, "b": bank_ix.get(b, -1), "f": 0 if fl == "Payment" else 1,
               "c": curr_ix.get(c, -1), "amt": r2(a, 2), "n": int(n),
               "usd": r2(u, 2), "ctry": ct, "pur": p}
              for d, b, fl, c, a, n, u, ct, p in con.execute(f"""
                  SELECT ymd, bank, flow, currency, amt_orig, c, usd, country, pur5
                  FROM dupC ORDER BY usd * (c - 1) / c DESC LIMIT {EXCEPTION_LIMIT}""").fetchall()],
    }
    return exc


# --------------------------------------------------------------------------
# Entity search index
# --------------------------------------------------------------------------

# Strings banks type into the name field when there is no counterparty to
# report. Grouped by name they dominate the index: "NULL" alone summed to
# $127.8bn. Extend via config/name_blocklist.json; never shrink below this.
PLACEHOLDER_NAMES = {
    "NULL", "NONE", "N/A", "NA", "NIL", "N.A.", "UNKNOWN", "NOT AVAILABLE",
    "XXX", "XX", "X", "XXXX", "XXXXX", "-", ".", "--", "0",
    "CASH", "SELF", "OTHER", "OTHERS", "TEST", "TT", "IB", "SWIFT", "NAME",
}


def load_name_blocklist():
    names = set(PLACEHOLDER_NAMES)
    path = CONFIG / "name_blocklist.json"
    if path.exists():
        try:
            extra = json.loads(path.read_text(encoding="utf-8")).get("names", [])
            names |= {str(x).strip().upper() for x in extra if str(x).strip()}
        except Exception as e:
            print(f"  WARNING: name_blocklist.json unreadable ({e}); built-in list only")
    return sorted(names)


def build_entities(con, dims):
    """Searchable index of the largest counterparties, with their top transactions."""
    con.execute("""
        CREATE VIEW ent_raw AS
        SELECT tr_name AS nm, 0 AS side, * EXCLUDE (tr_name, rc_name), rc_name AS other
        FROM txf WHERE tr_name IS NOT NULL
        UNION ALL BY NAME
        SELECT rc_name AS nm, 1 AS side, * EXCLUDE (tr_name, rc_name), tr_name AS other
        FROM txf WHERE rc_name IS NOT NULL
    """)
    block = load_name_blocklist()
    con.execute("CREATE TABLE ent_block (nm VARCHAR NOT NULL)")
    con.executemany("INSERT INTO ent_block VALUES (?)", [(n,) for n in block])
    # NOT EXISTS, not NOT IN: a single NULL in the blocklist would make NOT IN
    # reject every row and empty the entity index without any error.
    con.execute(r"""
        CREATE VIEW ent AS
        SELECT * FROM ent_raw e
        WHERE LENGTH(TRIM(e.nm)) >= 3
          AND NOT EXISTS (SELECT 1 FROM ent_block b WHERE b.nm = UPPER(TRIM(e.nm)))
          AND NOT regexp_matches(TRIM(e.nm), '^[0-9]+$')
          AND regexp_matches(TRIM(e.nm), '[A-Za-z\x{0E80}-\x{0EFF}]')
    """)
    d = con.execute("""
        SELECT COUNT(*), COALESCE(SUM(usd)/1e6, 0)
        FROM ent_raw WHERE nm IS NOT NULL
    """).fetchone()
    k = con.execute("SELECT COUNT(*), COALESCE(SUM(usd)/1e6, 0) FROM ent").fetchone()
    print(f"  placeholder names removed: {d[0]-k[0]:,} rows, ${d[1]-k[1]:,.0f}m")
    # Normalise names in SQL so the index groups spelling-identical entries.
    con.execute("""
        CREATE TABLE ent_tot AS
        SELECT TRIM(nm) AS nm, COUNT(*) AS n, SUM(usd) AS usd,
               SUM(CASE WHEN flow_ix = 1 THEN usd ELSE 0 END) AS usd_in,
               SUM(CASE WHEN flow_ix = 0 THEN usd ELSE 0 END) AS usd_out,
               MIN(yr) AS y0, MAX(yr) AS y1
        FROM ent GROUP BY 1
    """)
    top = con.execute(f"""
        SELECT nm FROM ent_tot ORDER BY usd DESC NULLS LAST LIMIT {ENTITY_LIMIT}""").fetchall()
    keep = [r[0] for r in top]
    con.execute("CREATE TABLE ent_keep (nm VARCHAR, ix INTEGER)")
    con.executemany("INSERT INTO ent_keep VALUES (?, ?)",
                    [(n, i) for i, n in enumerate(keep)])

    bank_ix = {b: i for i, b in enumerate(dims["banks"])}
    curr_ix = {c: i for i, c in enumerate(dims["currencies"])}
    ctry_ix = {c["code"]: i for i, c in enumerate(dims["countries"])}
    pur_ix = {p["code"]: i for i, p in enumerate(dims["purposes"])}

    totals = {r[0]: r[1:] for r in con.execute("""
        SELECT k.ix, t.n, t.usd/1e6, t.usd_in/1e6, t.usd_out/1e6, t.y0, t.y1
        FROM ent_tot t JOIN ent_keep k ON k.nm = t.nm""").fetchall()}

    per_year = defaultdict(list)
    for ix, y, f, n, u in con.execute("""
            SELECT k.ix, e.yr, e.flow_ix, COUNT(*), SUM(e.usd)/1e6
            FROM ent e JOIN ent_keep k ON k.nm = TRIM(e.nm)
            GROUP BY 1,2,3 ORDER BY 1,2,3""").fetchall():
        per_year[int(ix)].append([int(y), int(f), int(n), r2(u)])

    tops = {"ctry": defaultdict(list), "pur": defaultdict(list),
            "bank": defaultdict(list), "curr": defaultdict(list)}
    specs = [("ctry", "e.country", ctry_ix), ("pur", "e.pur5", pur_ix),
             ("bank", "e.bank", bank_ix), ("curr", "e.currency", curr_ix)]
    for key, col, lut in specs:
        for ix, val, u in con.execute(f"""
                SELECT ix, val, u FROM (
                  SELECT k.ix AS ix, {col} AS val, SUM(e.usd)/1e6 AS u,
                         ROW_NUMBER() OVER (PARTITION BY k.ix ORDER BY SUM(e.usd) DESC) AS rn
                  FROM ent e JOIN ent_keep k ON k.nm = TRIM(e.nm)
                  WHERE {col} IS NOT NULL GROUP BY 1,2)
                WHERE rn <= 5 ORDER BY ix, u DESC""").fetchall():
            if val in lut:
                tops[key][int(ix)].append([lut[val], r2(u)])

    txs = defaultdict(list)
    for ix, d, f, b, c, amt, usd, ctry, pur, other in con.execute(f"""
            SELECT ix, ymd, flow_ix, bank, currency, amt_orig, usd, country, pur5, other FROM (
              SELECT k.ix AS ix, e.ymd, e.flow_ix, e.bank, e.currency, e.amt_orig, e.usd,
                     e.country, e.pur5, e.other,
                     ROW_NUMBER() OVER (PARTITION BY k.ix ORDER BY e.usd DESC) AS rn
              FROM ent e JOIN ent_keep k ON k.nm = TRIM(e.nm))
            WHERE rn <= {ENTITY_TX_LIMIT} ORDER BY ix, usd DESC""").fetchall():
        txs[int(ix)].append([d, int(f), bank_ix.get(b, -1), curr_ix.get(c, -1),
                             r2(amt, 2), r2(usd, 2), ctry_ix.get(ctry, -1),
                             pur_ix.get(pur, -1), clean_name(other)])

    entities = []
    for i, nm in enumerate(keep):
        t = totals.get(i)
        if not t:
            continue
        entities.append({
            "nm": clean_name(nm) or nm,
            "n": int(t[0]), "usd": r2(t[1]), "in": r2(t[2]), "out": r2(t[3]),
            "y0": int(t[4]), "y1": int(t[5]),
            "yr": per_year.get(i, []),
            "ctry": tops["ctry"].get(i, []), "pur": tops["pur"].get(i, []),
            "bank": tops["bank"].get(i, []), "curr": tops["curr"].get(i, []),
            "tx": txs.get(i, []),
        })

    total_named = con.execute("SELECT COUNT(*), SUM(usd)/1e6 FROM ent_tot").fetchone()
    kept_usd = sum(e["usd"] for e in entities)
    print(f"  entity index: {len(entities):,} of {int(total_named[0]):,} named entities "
          f"({100 * kept_usd / (total_named[1] or 1):.1f}% of named value)")

    # Full searchable name list: EVERY counterparty, sender or receiver, so the
    # Search tab can find anyone - not just the top few thousand by value.
    # Only name + count + received + sent USD; no per-transaction detail, which
    # is what keeps this affordable. Rich detail stays with the top entities.
    # Rows: [name, count, recvUsdM, sentUsdM]. Sorted by name so gzip finds the
    # shared prefixes between "LAO ..." and "LAO ..." neighbours.
    allnames = [
        [nm, int(c), r2(recv), r2(sent)]
        for nm, c, recv, sent in con.execute("""
            SELECT TRIM(nm) AS n, COUNT(*) AS c,
                   SUM(CASE WHEN side = 1 THEN usd ELSE 0 END)/1e6 AS recv,
                   SUM(CASE WHEN side = 0 THEN usd ELSE 0 END)/1e6 AS sent
            FROM ent GROUP BY 1 ORDER BY LOWER(TRIM(nm))""").fetchall()
    ]
    print(f"  full name index: {len(allnames):,} names")
    return entities, {"total": int(total_named[0]), "kept": len(entities),
                      "coverPct": r2(100 * kept_usd / (total_named[1] or 1), 1),
                      "allNames": len(allnames)}, allnames


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def build_meta(con, files, ent_meta, cfg):
    row = con.execute("""
        SELECT COUNT(*), COUNT(DISTINCT Hash_ID), MIN(tx_date)::DATE, MAX(tx_date)::DATE,
               SUM(usd)/1e6 FROM tx""").fetchone()
    # Per source file: direction, rows, date span. This is what the dashboard's
    # Data Sources panel lists, so the analyst sees exactly what is loaded and
    # how the files join up.
    file_stats = [
        {"name": nm, "flow": fl, "rows": int(n),
         "min": str(mn), "max": str(mx)}
        for nm, fl, n, mn, mx in con.execute("""
            SELECT Source_File, ANY_VALUE(flow), COUNT(*),
                   MIN(tx_date)::DATE, MAX(tx_date)::DATE
            FROM tx WHERE Source_File IS NOT NULL
            GROUP BY 1 ORDER BY MIN(tx_date), Source_File""").fetchall()
    ]
    return {
        "generated": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"),
        "rows": int(row[0]), "uniqueIds": int(row[1]),
        "dateMin": str(row[2]), "dateMax": str(row[3]), "grossUsdM": r2(row[4]),
        "sourceFiles": files, "sourceStats": file_stats, "fxBand": [FX_MIN, FX_MAX],
        "entityMeta": ent_meta,
        "entityTxLimit": ENTITY_TX_LIMIT, "exceptionLimit": EXCEPTION_LIMIT,
        "bankOwnPatterns": cfg.get("bankOwnRule", {}).get("namePatterns", []),
    }


def encrypt_payload(blob, roles):
    """gzip, encrypt once under a random data key, then wrap that key per role.

    ``roles`` maps a role name to its passphrase. The payload is encrypted a
    single time with a random 256-bit data key; that key is then separately
    wrapped under a PBKDF2 key derived from each passphrase. So:

      * no passphrase, and no hash of one, is stored anywhere in the file;
      * adding or changing a role re-wraps a 32-byte key, it does not
        re-encrypt megabytes;
      * GCM's auth tag is the check -- the browser tries each wrapped key and
        the one that authenticates identifies the role. A wrong passphrase
        simply fails to unwrap.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    raw = gzip.compress(blob.encode("utf-8"), 9)
    data_key = secrets.token_bytes(32)
    iv = secrets.token_bytes(12)
    ct = AESGCM(data_key).encrypt(iv, raw, None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")

    wrapped = []
    for role, pw in roles.items():
        salt = secrets.token_bytes(16)
        kek = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                         iterations=KDF_ITERATIONS).derive(pw.encode("utf-8"))
        wiv = secrets.token_bytes(12)
        wrapped.append({"role": role, "salt": b64(salt), "iv": b64(wiv),
                        "key": b64(AESGCM(kek).encrypt(wiv, data_key, None))})

    print(f"  payload {len(blob)/1048576:.1f} MB → gzip {len(raw)/1048576:.1f} MB "
          f"→ encrypted {len(ct)/1048576:.1f} MB")
    print(f"  roles: {', '.join(roles)}")
    return json.dumps({
        "enc": "AES-GCM-256", "kdf": "PBKDF2-SHA256", "iter": KDF_ITERATIONS,
        "gz": True, "iv": b64(iv), "ct": b64(ct), "keys": wrapped,
    }, separators=(",", ":"))


MIN_PASSPHRASE = 12

TOO_SHORT = (
    f"Passphrase must be at least {MIN_PASSPHRASE} characters.\n"
    "Once published, the file can be downloaded and attacked offline without\n"
    "limit, so the passphrase is the only thing protecting the dataset.\n"
    "Generate one with:\n"
    "  python -c \"import secrets,string; a=string.ascii_letters+string.digits; "
    "print(''.join(secrets.choice(a) for _ in range(24)))\""
)


# Role -> (environment variable, prompt label, what it unlocks in the UI).
ROLES = {
    "superyang": ("ITRS_PASSWORD_SUPERYANG", "Superyang passphrase",
                  "super-admin: full access, data import, and Settings"),
    "admin":  ("ITRS_PASSWORD_ADMIN",  "Admin passphrase",
               "full access and data import, without Settings"),
    "viewer": ("ITRS_PASSWORD_VIEWER", "Team passphrase",
               "read-only: every report, no import"),
    "mpd":    ("ITRS_PASSWORD_MPD",    "MPD passphrase",
               "limited: Overview, Balance of Payments, Time Series, Period Report"),
    "epitrs": ("ITRS_PASSWORD_EPITRS", "EPitrs passphrase",
               "limited: Period Report only"),
}


def _check(pw, label):
    if len(pw) < MIN_PASSPHRASE:
        sys.exit(f"{label}: " + TOO_SHORT)
    return pw


def read_roles(args):
    """Collect one passphrase per role, from env vars, files, or a prompt.

    Passphrases are never accepted on the command line: arguments are visible
    in shell history and in the process list to any other user on the machine.
    """
    out = {}
    for role, (env_var, label, _) in ROLES.items():
        path = getattr(args, f"{role}_password_file", None)
        if path:
            out[role] = _check(Path(path).read_text(encoding="utf-8").strip(), label)
            continue
        val = os.environ.get(env_var)
        if val:
            out[role] = _check(val.strip(), label)
            continue
        while True:
            p1 = getpass.getpass(f"{label}: ")
            if len(p1) < MIN_PASSPHRASE:
                print("  " + TOO_SHORT.replace("\n", "\n  "))
                continue
            if p1 != getpass.getpass(f"Confirm {label.lower()}: "):
                print("  Passphrases did not match.")
                continue
            out[role] = p1
            break
    if len(set(out.values())) != len(out):
        sys.exit("Roles must not share a passphrase.")
    return out


def parse_args():
    p = argparse.ArgumentParser(description="Build the ITRS dashboard.")
    p.add_argument("--encrypt", action="store_true",
                   help="encrypt the data payload behind a passphrase (use this "
                        "for anything published to GitHub Pages or similar)")
    p.add_argument("--superyang-password-file",
                   help="file holding the superyang passphrase (super-admin, "
                        "the only role that sees Settings)")
    p.add_argument("--admin-password-file",
                   help="file holding the admin passphrase (full access)")
    p.add_argument("--viewer-password-file",
                   help="file holding the team passphrase (read-only)")
    p.add_argument("--mpd-password-file",
                   help="file holding the MPD passphrase (limited tab access)")
    p.add_argument("--epitrs-password-file",
                   help="file holding the EPitrs passphrase (Period Report only)")
    p.add_argument("--out", help="output path (default: ITRS_Dashboard.html, "
                                 "or docs/index.html with --encrypt --publish)")
    p.add_argument("--publish", action="store_true",
                   help="write to docs/index.html, ready for GitHub Pages")
    p.add_argument("--pack", action="store_true",
                   help="also write ITRS_data_pack.json, loadable from the "
                        "dashboard's Import button to swap datasets without a rebuild")
    return p.parse_args()


def main():
    args = parse_args()
    if args.publish and not args.encrypt:
        sys.exit("Refusing to build an unencrypted file for publishing.\n"
                 "This dataset contains company names, TINs and transaction detail.\n"
                 "Use:  python build_dashboard.py --publish --encrypt")

    if not TEMPLATE.exists():
        sys.exit(f"Template not found: {TEMPLATE}")

    # Ask for the passphrase before the slow work, not after it. Aggregating and
    # indexing takes minutes; prompting at the end means an unattended build
    # stalls on an invisible prompt.
    roles = read_roles(args) if args.encrypt else None

    cfg, lines = load_report_config()
    print(f"Report config: {len(lines)} lines from {CONFIG / 'report_lines.json'}")

    print("Reading source data ...")
    con, files = build_connection()
    create_clean_view(con)

    print("Building dimensions ...")
    dims, method_map = build_dimensions(con, lines)
    register_lookups(con, dims, method_map)
    print(f"  {len(dims['months'])} months, {len(dims['purposes'])} purpose codes, "
          f"{len(dims['countries'])} countries, {len(dims['banks'])} banks, "
          f"{len(dims['methods'])} methods (from {len(method_map)} raw variants)")

    print("Aggregating cubes ...")
    cubes = build_cubes(con, dims)
    for name, rows in cubes.items():
        print(f"  {name:<14} {len(rows):>8,} rows")

    print("Scanning for exceptions ...")
    exceptions = build_exceptions(con, dims)
    ds = exceptions["dupSummary"]
    print(f"  FX errors: {len(exceptions['fxRows']):,} shown")
    for t in ("A", "B", "C"):
        print(f"  duplicates tier {t}: {ds[t]['groups']:,} groups, "
              f"{ds[t]['extra']:,} extra rows, ${ds[t]['usdM']:,.1f}m")

    print("Building entity index ...")
    entities, ent_meta, all_names = build_entities(con, dims)

    meta = build_meta(con, files, ent_meta, cfg)
    _bank_officers, _bank_names = load_bank_officers()
    _acronyms = []
    _ap = CONFIG / "name_acronyms.json"
    if _ap.exists():
        try:
            _acronyms = [str(a).strip().upper() for a
                         in json.loads(_ap.read_text(encoding="utf-8")).get("acronyms", [])
                         if str(a).strip()]
        except Exception as e:
            print(f"  WARNING: name_acronyms.json unreadable ({e}); ignoring")
    payload = {"meta": meta, "dims": dims, "cubes": cubes,
               "exceptions": exceptions, "entities": entities,
               "rules": load_bop_rules(),
               "officers": _bank_officers, "bankNames": _bank_names,
               "nameAcronyms": _acronyms, "allNames": all_names}

    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    if args.pack:
        pack = HERE / "ITRS_data_pack.json"
        pack.write_text(blob, encoding="utf-8")
        print(f"  data pack: {pack.name} ({pack.stat().st_size / 1048576:.1f} MB)")

    html = TEMPLATE.read_text(encoding="utf-8")
    if "__ITRS_DATA__" not in html:
        sys.exit("Template is missing the __ITRS_DATA__ placeholder")

    if args.encrypt:
        print("Encrypting ...")
        blob = encrypt_payload(blob, roles)

    out = Path(args.out) if args.out else (
        HERE / "docs" / "index.html" if args.publish else OUTPUT)
    out.parent.mkdir(parents=True, exist_ok=True)
    # The payload lands inside a <script> block, so a literal </script> in the
    # data would end it early. JSON escaping of '<' prevents that.
    out.write_text(html.replace("__ITRS_DATA__", blob.replace("<", "\\u003c")),
                   encoding="utf-8")

    print(f"\nWrote {out}  ({out.stat().st_size / 1024 / 1024:.1f} MB)"
          f"{'  [encrypted]' if args.encrypt else '  [PLAINTEXT — do not publish]'}")
    print(f"  {meta['rows']:,} transactions | {meta['dateMin']} to {meta['dateMax']}"
          f" | ${meta['grossUsdM'] / 1000:,.1f}bn gross")
    if args.publish:
        print("\n  Ready for GitHub Pages. Settings → Pages → Source: main / docs")


if __name__ == "__main__":
    main()
