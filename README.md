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

## Updating data from the website

The **Import** button (upload icon, top right) adds transactions without
touching Python. The file is read in the browser and never uploaded anywhere.

Two file types:

- **CSV of transactions** — merged into the existing dataset. Columns are matched
  by name, case and punctuation insensitive, so `Date_of_Transaction`,
  `date of transaction` and `DATEOFTRANSACTION` all work. Only two things are
  required: a transaction date, and either `Amount_USD` or `Amount_Kip` together
  with `Exchange_Rates_USD`. Everything else is optional and degrades gracefully.
  Dates accept ISO, `d/m/Y` and `m/d/Y`.
- **`.json` data pack** — replaces the dataset wholesale. Produce one with
  `python build_dashboard.py --pack`. Useful for handing a colleague a refreshed
  dataset without rebuilding the whole HTML.

New banks, countries, currencies, purpose codes, months and years are added
automatically; the period selector grows to match. The import is previewed before
it commits — rows read, rows usable, rows skipped and why, value added per
direction, date range, and any new codes — so you can sanity-check the totals
before pressing Apply.

**Two limits worth knowing.** Changes last for the session only; closing the tab
discards them. To make an update permanent, drop the file into `Payment/` or
`Receive/` and rebuild — that is also the only way to keep the parquet database
as the single source of truth. And submission-lag percentiles and
value-concentration ranks are not recalculated on import, because medians and
rankings cannot be merged additively the way counts and sums can. Every other
figure updates.

The cleaning rules applied on import mirror `create_clean_view()` in the build
script. If you change one, change the other.

## Getting charts out

Hover any chart card and three buttons appear top-right:

- **Copy** — puts the chart on the clipboard as a PNG. Paste straight into
  Excel, Word or PowerPoint and it arrives pixel-identical to the screen,
  including the current theme. (On `file://` some browsers block clipboard
  images; it falls back to downloading the PNG.)
- **PNG** — saves the same image at 2× for print.
- **CSV** — the numbers behind that specific chart, already shaped for a pivot.

The image is produced by cloning the SVG, resolving every CSS variable and
class-driven rule into literal values, then rasterising. That step matters:
serialising the live node alone would lose all colour and type, because the
chart's styling lives in the stylesheet rather than on the element.

The CSV is the data actually plotted, after the period, `Use`, and flow filters
— so what you export always matches what you were looking at.

There is no native-Excel chart object export. That would need an `.xlsx` writer
producing chart XML, and a malformed one makes Excel refuse the file outright.
Pasting the PNG gives an exact picture; the CSV gives editable numbers you can
chart in Excel in two clicks.

## What the Bank selector actually filters

Only the bank cube carries a bank axis — adding one to the country, currency and
purpose cubes would multiply the file size several times over for a filter that
is rarely used that way. So the selector reaches some views and not others, and
the banner now says which, per tab:

| Tab | Bank filter |
|---|---|
| Banks, FX & Duplicates, Data Quality | applies to everything |
| Overview | the four totals only — charts below are all-banks |
| Time Series | only when Dimension is set to Bank |
| BOP, Weekly, Search | does not apply |

This is stated on screen rather than left implicit, because a filtered header
above unfiltered charts is exactly the kind of thing that quietly produces a
wrong number in a published report.

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

**Theme and language** are one button each, in the header and on the lock screen.
Both flip on click, and both show what you *get* rather than where you are — a
moon while light, a sun while dark; `ລາວ` while in English, `EN` while in Lao.

The theme starts on match-system; the first press resolves that to whichever is
the opposite of what is on screen, so it always visibly changes something. The
choice then persists in `localStorage` and is applied by an inline script before
first paint, so there is no flash of the wrong scheme on reload. Charts redraw on
switch because their colours are read from CSS at draw time.

**Glass** — panels use `backdrop-filter` over a fixed three-point colour field,
with a raking highlight on each card. Two deliberate exceptions: sticky table
headers stay opaque, otherwise rows scroll visibly through them; and browsers
without `backdrop-filter` fall back to solid panels via `@supports`. Print styles
drop the glass entirely.

**Built for the phone first.** Charts measure their container and draw into a
viewBox matching its real pixel width, so one SVG unit is one CSS pixel and 10px
type renders at 10px. The earlier fixed 800-wide viewBox scaled to 45% on a
375px screen, which rendered axis labels at roughly 4.5px — legible on a desktop
mockup, useless in the hand. Axis label density, tick counts, margins, bar
thickness and the label column in ranked bars all scale with the measured width.
KPI tiles pair up from 340px so the first chart is not pushed below the fold.

**Country names follow the Lao view.** `config/country_names_lo.json` carries all
251 ISO codes, lifted from the `R by Country M` sheet of your time-series
workbook so the dashboard uses the same wording as the reports people already
read — ຈີນ, ສະຫະລັດ, ໄທ. It is a plain JSON file; edit any entry you disagree
with. Codes with no Lao entry fall back to the English name.

**Dropdowns** are custom, not native. A browser's `<select>` popup is drawn by
the operating system and ignores page styling entirely — in dark mode it rendered
pale grey on white and was effectively unreadable. Each one is replaced with a
styled panel: tick on the current value, keyboard navigation (arrows, Home/End,
Enter, Escape), and a filter box on any list longer than ten items, which makes
the 66-bank selector usable.

The real `<select>` is kept in the DOM as the source of truth and its `value`
setter is patched to repaint the button, so programmatic assignment and every
existing `change` listener keep working. Menus are appended to `<body>` and
positioned with `position: fixed`, so they are never clipped by the horizontally
scrolling control strip on narrow screens, and they flip above the trigger when
there is no room below.

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
