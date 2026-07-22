# ITRS Live Report — dashboard

A single self-contained HTML dashboard built from the ITRS parquet database.
No server, no internet, no install on the viewing machine. Open it, or email it,
or drop it in OneDrive and share the link.

```
dashboard/
  build_dashboard.py        the build script — run this to refresh
  template.html             layout, styling and all dashboard logic
  config/report_lines.json  weekly report line definitions — EDIT THIS
  ITRS_Dashboard.html       the output (open this)
```

## Refreshing with new data

1. Drop new files into `../Payment/` (outflows) or `../Receive/` (inflows).
2. Run:

```bash
python build_dashboard.py
```

That is the whole process. Specifically:

- **Any filename works.** Files are discovered by folder, not by name, so
  `ITRS_P_2026.parquet` needs no configuration.
- **Flow direction comes from the folder**, so a file with no `Flow` column
  still lands on the right side.
- **`.parquet`, `.csv`, `.xlsx` and `.xlsm` are all read.** Excel needs DuckDB's
  spatial extension, which installs itself on first use; if it can't, the script
  warns and skips those files rather than failing.
- **Columns are matched by name and unioned.** Extra columns are ignored, missing
  columns become null. Adding a field to next year's extract will not break the build.
- **New years appear automatically** in the period selector, time series, weekly
  report and every chart.

First run needs the two dependencies:

```bash
python -m pip install duckdb pyarrow
```

## The eight tabs

| Tab | What it answers |
|---|---|
| **Overview** | Headline flows, net position, top countries, currency mix |
| **Balance of Payments** | Current vs financial account, the gross→BOP bridge, drillable purpose hierarchy |
| **Time Series** | Bank / country / currency / purpose × monthly, quarterly, yearly — the workbook layout, exportable to CSV |
| **Weekly Report** | Week-on-week table in the official layout, with generated Lao or English narrative |
| **Search** | Type a company name, see its flows, counterparties, banks and largest transactions |
| **FX & Duplicates** | Exchange-rate exceptions and three tiers of duplicate detection |
| **Banks** | League table, market share, submission timeliness, reporting gaps |
| **Data Quality** | Grade, validation exceptions, field completeness by year, per-bank scorecard |

Language toggle (EN / ລາວ) is top-right. Purpose names always stay in Lao exactly
as the reporting banks recorded them — they are never translated.

## The `Use` flag — the BOP definition switch

`Use` is what decides whether a transaction enters the balance of payments. It is
the single most important field in the dataset, so it is a first-class control in
the header with three positions:

| Setting | Records | Value | What it is |
|---|---|---|---|
| **Use = Yes** | 4,616,301 (99.4%) | $174.4bn (39.9%) | Enters BOP compilation. **The default.** |
| **Use = No** | 26,418 (0.6%) | $262.8bn (60.1%) | Excluded — own-account interbank positions |
| **All** | 4,642,719 | $437.3bn | Gross reporting turnover. Not a BOP measure. |

The three reconcile exactly: 87.3 + 130.7 = $218.0bn inflow, and
4,616,301 + 26,418 = 4,642,719 records.

**0.6% of records carry 60% of the value.** That asymmetry is the whole point —
it is why gross turnover cannot be read as a BOP number, and why a handful of
mis-flagged records can move the published accounts.

The setting applies everywhere: every tab, the weekly report, the time series,
the exception counts. Three ways to use it:

- **Use = Yes** for anything that feeds the accounts.
- **Use = No** to audit the exclusion itself — see exactly what compilation
  drops, which banks it comes from, and which purpose codes it lands on. This is
  the view for checking that the flag is being set correctly.
- **All** for reporting-compliance work, never for BOP analysis.

The Balance of Payments tab shows the full gross→BOP bridge and a dedicated
`Use` panel with the record/value split and what the exclusion is made of.

`M2` and `Move_Fund` are the two other compilation flags, shown alongside on the
same panel.

## Publishing to GitHub Pages

The source is already on GitHub. **The data file is not, and cannot be published
by anyone but you** — it has to be encrypted with a passphrase only you know.

Three commands from this directory:

```bash
python build_dashboard.py --publish --encrypt
```

```bash
git add docs/index.html && git commit -m "Publish encrypted dashboard"
```

```bash
git push
```

Then: repo → **Settings → Pages → Source: `main` / `docs`**. The site appears at
`https://itwithyou.github.io/DataLive_BOP/` within a minute or two.

Share the URL and the passphrase **separately** — never in the same message,
never in the repo, never in a commit message or issue.

The build **refuses** to write a publishable file unencrypted. `--publish` without
`--encrypt` is an error, not a warning.

## Appearance

**Theme** — light, dark, or match-system, from the sun / A / moon control in the
header (and on the lock screen). The choice persists in `localStorage` and is
applied before first paint, so there is no flash of the wrong scheme on load.
Charts redraw on switch because their colours are read from CSS at draw time.

**Glass** — panels use `backdrop-filter` over a fixed three-point colour field,
with a raking highlight on each card. Two deliberate exceptions: sticky table
headers stay opaque, otherwise rows scroll visibly through them; and browsers
without `backdrop-filter` fall back to solid panels via `@supports`. Print styles
drop the glass entirely.

### What the encryption does and does not do

The payload is gzipped, then encrypted with **AES-256-GCM** under a key derived
by **PBKDF2-SHA256, 310,000 iterations**, with a random 16-byte salt and 12-byte
IV. The HTML contains ciphertext only. Decryption happens in the browser via
WebCrypto; the passphrase is never transmitted and never stored. GCM's
authentication tag is the password check, so there is no password hash in the
file to attack separately.

Encryption also shrinks the file — 7.6 MB plaintext becomes 2.6 MB encrypted,
because gzip runs first. Unlock takes 1–3 seconds on a phone; that delay is the
PBKDF2 work factor and it is deliberate.

**It genuinely protects against:** anyone finding the URL, search engines,
scrapers, GitHub's own search, and the fact that Pages from a private repo are
still publicly reachable on Free and Pro plans.

**It does not protect against:** anyone who has the passphrase. It is a shared
secret, not per-user accounts. There is no audit trail, no revocation, and no way
to tell who opened it. Whoever unlocks it can extract and redistribute the whole
dataset. Rotating access means rebuilding with a new passphrase and redistributing.

**Choose a passphrase accordingly** — four or five unrelated words beats a short
complex string. The minimum enforced is 12 characters.

### Before you publish anything

This dataset contains company names, TINs, bank-level detail and individual
transaction amounts. Two questions worth settling first:

1. **Does BOL policy permit hosting supervisory data on third-party
   infrastructure at all?** Encryption is a technical control; it does not answer
   a policy question. If the answer is no, the encrypted file still works
   perfectly from a shared drive or an internal server — you do not need GitHub.
2. **Do you need to know who accessed it?** If yes, a shared passphrase is the
   wrong mechanism. Use Cloudflare Pages with Cloudflare Access (free tier,
   real per-user sign-in and logs) or an internal SharePoint site instead. The
   same encrypted file works behind either.

### Git history is permanent

If a plaintext build or a parquet file is ever committed, deleting it later does
not remove it — it stays in history and remains downloadable. Removing it means
rewriting history and force-pushing.

The provided `.gitignore` excludes `*.parquet`, `*.xlsx`, `*.xlsm`, `*.csv`,
`Payment/`, `Receive/`, and `ITRS_Dashboard.html` (the plaintext build). Verify
with `git status` before your first commit that only `docs/index.html`,
`build_dashboard.py`, `template.html`, `config/` and the docs are staged.

## Weekly report — verify the mapping before official use

`config/report_lines.json` maps purpose codes to report lines. It is plain JSON,
safe to edit, and the build picks up changes on the next run. Matching is
longest-prefix-wins, so `050104` overrides `05`.

**The default mapping is inferred, not authoritative.** In particular the sector
split the official note uses — government / state enterprise / business /
commercial bank borrowing — is **not derivable from `Pur_5` alone**. The default
approximates it with the `bankOwn` flag and the name patterns in `bankOwnRule`.
Check one known week against a published note before using this for anything
official, and adjust the config until it reconciles.

The "Excl. commercial banks" scope reproduces the note's second section by
dropping lines flagged `bankOwn`.

## What the checks actually check

**Exchange rate.** `Amount_USD = Amount_Kip ÷ Exchange_Rates_USD` — verified on
99.99% of rows. `Exchange_Rates_USD` is the LAK-per-USD rate, running 8,258
(2017) to 21,483 (2025). Anything outside 7,000–25,000 is flagged. When the rate
is wrong the USD amount is wrong too, so these corrupt every aggregate they
touch. Band is set by `FX_MIN` / `FX_MAX` in the build script.

**Duplicates**, in three tiers because confidence genuinely differs:

- **Tier A — reference collision** (33,660 groups, 34,363 extra rows, $3.4bn).
  Same bank, flow, reference number, date, amount, currency. A reference number
  should be unique per bank, so this is near-certainly a re-sent record. *Start here.*
- **Tier B — full match, named** (11,004 groups, 12,774 rows, $1.6bn). Every
  business key matches and both entity names are present. Strong evidence.
- **Tier C — weak, unnamed** (235,160 groups, 1.6M rows). Same business key but
  no entity names, so genuine repeated small transfers cannot be told apart from
  double entries. A lead, not a finding — do not report this number as duplicates.

## Known data issues this build handles

- **Mojibake in `Transfer_Method`** — 5 values were double-encoded UTF-8 mixing
  CP1252 characters with raw undefined bytes. Repaired and merged into their
  clean twins; 21 raw variants collapse to 16 real methods.
- **Dirty `Pur_2`** — strays like `050`, `06`, `07` and blanks. The 2-digit code
  is taken from `Pur_5` instead, which has a clean 2/4/6 structure.
- **Inconsistent purpose names** — 255 spellings for 179 codes. Each code takes
  the spelling used by the most transactions.
- **Sparse fields** — LSIC code, transfer method and entity names are 80%+ empty.
  Shown on the Data Quality tab as a reporting-compliance finding, not scored as
  a validity failure.

## Index limits

The HTML embeds pre-aggregated cubes, not raw rows, which is why it is 7 MB
instead of 570 MB. Three things are capped (constants at the top of the build
script, raise them if you want a bigger file):

- `ENTITY_LIMIT = 4000` — searchable counterparties, covering 76.5% of named value
- `ENTITY_TX_LIMIT = 12` — largest transactions carried per counterparty
- `EXCEPTION_LIMIT = 300` — FX and duplicate rows listed per tier

Exception *counts* and *totals* are complete and unfiltered; only the row-level
listings are capped. For full row-level work, query the parquet directly with
DuckDB — the cleaning logic in `create_clean_view()` is the reference implementation.

## Coverage

4,642,719 transactions, 2017-01-02 to 2025-12-31, $437.3bn gross, 236 countries,
66 banks, 140 purpose codes. Every `Hash_ID` is distinct — no duplicate rows at
the file level.

**2026 data is not in the parquet database yet.** The weekly reports and time
series workbook in `../../ITRS Report/` already run into 2026, sourced from
`ITRS_DataBase!_2026_data.xlsm`. Once 2026 is exported to `Payment/` and
`Receive/`, re-running the build picks it up with no other change.
