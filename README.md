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
  `generate-cost-basis` commands.

## Project layout

```
app/
  config.py              Constants and filesystem paths.
  main.py                Typer CLI entrypoint (python -m app.main).
  models/                Pydantic Transaction model + tax-lot dataclasses.
  parsers/               Broker-specific CSV parsers (+ registry).
  services/              Pure business logic: ingestion, tax lots, holdings.
  reports/               CSV / Excel / PDF renderers + orchestrator.
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

## Usage

Drop your Scalable Capital CSV exports under `input/<account_name>/`
(one folder per person). Then:

```bash
# Smoke-test: parse + run tax-lot matching and print a summary
python -m app.main process

# Generate every report in every format
python -m app.main generate-reports

# Filter to one account (folder name)
python -m app.main process --account ramu
python -m app.main generate-reports --account ramu

# Choose specific output formats (repeatable)
python -m app.main generate-reports -f csv -f pdf

# Verbose / debug logging
python -m app.main process --verbose
```

Reports are written into `output/csv/`, `output/excel/`, `output/pdf/`
with timestamps in the filename so historical runs do not overwrite
each other.

### Cost Basis Transfer report (broker-to-broker transfers)

When transferring assets out of Scalable Capital (e.g. into IBKR), the
receiving broker needs the acquisition price of **each lot** so future
sells stay tax-matched correctly. The averaged "Current Holdings"
report is not enough - you need one row per still-open tax lot.

```bash
# Generate one row per open lot in every format
python -m app.main generate-cost-basis

# Filter or limit formats just like generate-reports
python -m app.main generate-cost-basis --account ramu -f pdf
```

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
- `-qty` rows (the broker reducing a parent position when shares are
  converted into a successor security) are **ignored**. The original
  parent lots stay in the queue, so a later sell of the parent uses
  its full original cost basis.

If you need the exact German proportional split for a particular
spin-off, override the realized-gain row in the destination broker's
intake form using the
[Cost Basis Transfer report](#cost-basis-transfer-report-broker-to-broker-transfers)
as a starting point.

## Running tests

```bash
python -m pytest tests/ -q
```

## Environment overrides

Three env vars let you point the tool at non-default directories or
narrow the admitted broker transaction types - useful when running
inside Docker:

| Variable                              | Purpose                            |
| ------------------------------------- | ---------------------------------- |
| `PORTFOLIO_LEDGER_INPUT_DIR`          | Override the default `./input/`.   |
| `PORTFOLIO_LEDGER_OUTPUT_DIR`         | Override the default `./output/`.  |
| `PORTFOLIO_LEDGER_TRANSACTION_TYPES`  | Comma-separated list of raw broker `type` values to admit (default includes `Buy,Sell,Savings plan,Distribution,Taxes,Tax,Security transfer,Corporate action`). Anything not listed is dropped at parse time. |

A starter `.env.template` is committed at the repo root - copy it to
`.env` and edit if you need to override the defaults.

## Adding a new broker

1. Subclass `app.parsers.base.BrokerParser`.
2. Implement `can_parse()` (sniff the file header) and `parse()`
   (yield `Transaction` objects).
3. Register the new class in `app/parsers/registry.py`.

The rest of the application (tax-lot engine, holdings, reports) is
broker-agnostic and requires no further changes.
