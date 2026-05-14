# Portfolio Ledger

Production-grade Python 3 tool for processing brokerage transaction
exports from **Scalable Capital Germany**, tracking individual tax lots
(FIFO matching) and generating tax / portfolio reports in CSV, Excel
and PDF.

The codebase is structured for long-term maintainability and is ready
for additional broker integrations - new brokers only need a new parser
class registered in `app/parsers/registry.py`.

---

## Features

- Parses Scalable Capital DE CSV exports (German number formatting).
- Tracks tax lots via FIFO matching (`collections.deque`) with full
  support for partial lot consumption.
- Aggregates current holdings per account and across the family.
- Optional **cash** line in the combined report from operator-entered
  current idle cash per portfolio folder.
- Combined PDF/Excel/CSV include an **annual summary** of pre-tax realized
  gains by account, family totals, and approximate **Family CGT Paid** per year.
- Renders 3 standard reports (Tax Lots realized gains, current
  holdings, combined family portfolio) in 3 output formats (CSV, Excel,
  PDF).
- Generates an opt-in **per-lot Cost Basis Transfer** report for
  broker-to-broker transfers (e.g. Scalable Capital -> IBKR).
- Detects and elides paired broker-internal "switch" transfers so the
  original tax lots survive sub-account moves (see
  [Broker switch transfers](#broker-switch-transfers-scalable-capital)).
- Handles `Corporate action` rows with a zero-cost-basis acquisition
  model for inbound (free / spin-off / scrip) shares.
- All money math uses `Decimal` - never `float`.
- Output uses US decimal formatting (`.` decimal, `,` thousands).
- Single-file Typer CLI with `process`, `generate-reports`, and
  `generate-cost-basis` commands (reports can be chosen interactively or
  via `--reports` / `--format`).
- Optional **per-portfolio ISIN exclusions** for current holdings and
  the combined report (`PORTFOLIO_LEDGER_IGNORE_ISINS` + `--apply-isin-ignore`).

## Project layout

```
app/
  config.py              Constants and filesystem paths.
  main.py                Typer CLI entrypoint (python -m app.main).
  models/                Pydantic Transaction model + tax-lot dataclasses.
  parsers/               Broker-specific CSV parsers (+ registry).
  services/              Pure business logic: ingestion, tax lots, holdings.
  reports/               CSV / Excel / PDF renderers + orchestrator.
  cli/                   Interactive prompts for report selection.
  utils/                 Decimal / date / logging helpers.
input/
  <person_name>/         Drop CSV exports here, one folder per account.
output/
  csv/  excel/  pdf/     Generated reports land here.
tests/                   Pytest unit tests.
```

## Installation (local)

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## CLI commands

Drop Scalable Capital CSV exports under `input/<account_name>/` (one folder
per person). Invoke the CLI as `python3 -m app.main <command>` (after
activating the venv, `python -m app.main` works the same way).

| Command | Purpose |
| -------- | ------- |
| `process` | Parse exports, run FIFO tax-lot matching, print a short summary. |
| `generate-reports` | Write Tax Lots, Current Holdings, and/or Combined reports (CSV, Excel, PDF). |
| `generate-cost-basis` | Write the per-lot **Cost Basis Transfer** report for broker transfers. |

Shared options (where supported): `--account` / `-a` (single folder), `--input-dir`,
`--verbose` / `-v`. For `generate-reports`, add `--reports` / `-r` and `--format` / `-f`
for non-interactive runs; repeatable `--cash folder:amount` for combined idle cash;
`--apply-isin-ignore` to apply `PORTFOLIO_LEDGER_IGNORE_ISINS` from `.env`.

All examples in one place:

```bash
# --- process (summary only) ---
python3 -m app.main process
python3 -m app.main process --account ramu
python3 -m app.main process --verbose
python3 -m app.main process --apply-isin-ignore

# --- generate-reports (interactive if --reports / --format incomplete) ---
python3 -m app.main generate-reports

python3 -m app.main generate-reports \
  --reports tax-lots --reports holdings --reports combined \
  --format all

python3 -m app.main generate-reports --reports combined --format pdf \
  --cash ramu:12000 --cash rakshana:8000

python3 -m app.main generate-reports --account ramu

# Partially interactive: only formats → prompts for which reports
python3 -m app.main generate-reports -f csv

# Partially interactive: only reports → prompts for formats
python3 -m app.main generate-reports --reports combined --reports tax-lots

# Omit configured ISINs from current holdings + combined (see Environment overrides)
python3 -m app.main generate-reports \
  --reports holdings --reports combined --format csv \
  --apply-isin-ignore

python3 -m app.main generate-reports --verbose

# --- generate-cost-basis (one row per open lot) ---
python3 -m app.main generate-cost-basis
python3 -m app.main generate-cost-basis --account ramu -f pdf

# --- tests ---
python3 -m pytest tests/ -q
```

**Combined report — idle cash.** Interactive runs can ask whether to add a **Cash**
row and prompt once per folder under `input/`. Amounts drive the Cash row and family
**Allocation** (no tax or cost-basis adjustment). With both `--reports` and
`--format`, pass repeatable `--cash folder:amount`; omit `--cash` for securities only.

**ISIN ignore list.** Set `PORTFOLIO_LEDGER_IGNORE_ISINS` in `.env` (comma-separated
`folder:ISIN`, folder name case-insensitive). Exclusions apply only when you pass
`--apply-isin-ignore` on `process` or `generate-reports`; tax lots and cost-basis
reports are unaffected.

Reports go to `output/csv/`, `output/excel/`, `output/pdf/` with timestamps in each
filename.

### Cost Basis Transfer report (broker-to-broker transfers)

When transferring assets out of Scalable Capital (e.g. into IBKR), the
receiving broker needs the acquisition price of **each lot** so future
sells stay tax-matched correctly. The averaged "Current Holdings"
report is not enough - you need one row per still-open tax lot.
Use `generate-cost-basis` (see examples above).

Output filenames: `cost_basis_transfer_{stamp}.{csv,xlsx,pdf}`. Each
row carries `Account`, `ISIN`, `Symbol`, `Acquisition Date`,
`Quantity`, `Cost per Share`, and `Cost Basis`. Enter
`Quantity` and `Cost per Share` for each row on the receiving broker's
intake form; the `Cost Basis` column is shown only as a sanity check.

### Broker switch transfers (Scalable Capital)

Scalable Capital occasionally re-shelves shares between its two
sub-accounts (the regular brokerage account and the savings/depot
account). The export records each move as **two paired
`Security transfer` rows**:

1. an outbound leg with negative quantity, dated when the shares
   leave the source sub-account; its `reference` is a plain broker
   movement id, e.g. `WWUM 00596749782`.
2. an inbound leg with the same absolute quantity and ISIN, typically
   one business day later; its `reference` carries the
   `SWITCH-...-WDP` marker.

Tax-lot wise the shares never left the customer - the broker preserves
the **original lots** across the move. If the engine processed both
legs naively it would pop the original (often cheap) lots on the
outbound and create one expensive lot at the inbound day's price,
silently destroying the cost basis for every later sell on that ISIN.

To prevent that, ingestion runs `app.services.transfer_pairs.collapse_switch_pairs`
right after the chronological sort. The detection rule is:

- The inbound leg's `reference` starts with `SWITCH-`, **and**
- there is an outbound `Security transfer` for the same
  `(account, ISIN, |quantity|)` within ≤ 7 days (closest match wins).

When both conditions hold, **both legs are dropped** before they reach
the tax-lot engine, so the original lots stay in the queue with their
true acquisition prices and dates.

Unpaired transfers are intentionally left alone:

- A real **outbound** transfer to another broker (no later `SWITCH-`
  inbound) flows through the engine, consuming lots in FIFO order and
  adjusting invested capital by the broker-reported transfer amount.
- A real **inbound** transfer from another broker (the `reference`
  does not start with `SWITCH-`) is admitted as a fresh acquisition at
  the broker's transfer price.

Each collapsed pair is logged at INFO level by `transfer_pairs`; the
ingestion summary line includes a `collapsed N switch-pair leg(s)`
field so you can see at a glance how many rows were elided.

### Corporate actions

`Corporate action` rows in the Scalable Capital export move shares
without cash (the `amount` and `price` columns are always `0`). They
typically appear for stock splits, scrip dividends, ticker changes
and spin-offs. Modelling each variant precisely (in particular the
German Finanzamt's proportional cost-basis split for spin-offs) would
require per-action ratios that the broker does not publish in the
export, so the engine uses a deliberately simple rule:

- `+qty` rows are admitted as **zero cost basis acquisitions**. Any
  later sell of those shares therefore books the full proceeds as
  realized gain. This is conservative for tax purposes (it
  over-states gain), but fully automatic.
- `-qty` rows **reduce open lots FIFO** (no cash, so no row in the Tax
  Lots realized-gains report). This matches broker behaviour when an
  old ISIN is replaced: you typically see a deduction on the retired ISIN
  and a separate `+qty` corporate action on the new ISIN. The replacement
  row still loads at **zero** cost basis here unless you adjust manually
  for strict tax figures.

If you need the exact German proportional split for a particular
spin-off, override the realized-gain row in the destination broker's
intake form using the
[Cost Basis Transfer report](#cost-basis-transfer-report-broker-to-broker-transfers)
as a starting point.

## Environment overrides

Environment variables let you point the tool at non-default directories,
narrow transaction types, and optionally list ISINs to hide from holdings /
combined reporting — useful when running inside Docker:

| Variable                                      | Purpose |
| --------------------------------------------- | ------- |
| `PORTFOLIO_LEDGER_INPUT_DIR`                  | Override the default `./input/`. |
| `PORTFOLIO_LEDGER_OUTPUT_DIR`                 | Override the default `./output/`. |
| `PORTFOLIO_LEDGER_TRANSACTION_TYPES`          | Comma-separated list of raw broker `type` values to admit (default includes `Buy,Sell,Savings plan,Distribution,Taxes,Tax,Security transfer,Corporate action`). Anything not listed is dropped at parse time. |
| `PORTFOLIO_LEDGER_IGNORE_ISINS`                 | Optional. Comma-separated `folder:ISIN` pairs (e.g. `rakshana:DE000EWG2LD7`). Used only with `--apply-isin-ignore`; see [CLI commands](#cli-commands). |

A starter `.env.template` is committed at the repo root - copy it to
`.env` and edit if you need to override the defaults.

## Adding a new broker

1. Subclass `app.parsers.base.BrokerParser`.
2. Implement `can_parse()` (sniff the file header) and `parse()`
   (yield `Transaction` objects).
3. Register the new class in `app/parsers/registry.py`.

The rest of the application (tax-lot engine, holdings, reports) is
broker-agnostic and requires no further changes.
