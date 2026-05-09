# Portfolio Ledger

Production-grade Python 3 tool for processing brokerage transaction
exports from **Scalable Capital Germany**, computing FIFO tax lots and
generating tax / portfolio reports in CSV, Excel and PDF.

The codebase is structured for long-term maintainability and is ready
for additional broker integrations - new brokers only need a new parser
class registered in `app/parsers/registry.py`.

---

## Features

- Parses Scalable Capital DE CSV exports (German number formatting).
- Computes FIFO tax lots using `collections.deque` with full support
  for partial lot consumption.
- Aggregates current holdings per account and across the family.
- Renders 3 standard reports (FIFO realized gains, current holdings,
  combined family portfolio) in 3 output formats (CSV, Excel, PDF).
- Generates an opt-in **per-lot Cost Basis Transfer** report for
  broker-to-broker transfers (e.g. Scalable Capital -> IBKR).
- All money math uses `Decimal` - never `float`.
- Output uses US decimal formatting (`.` decimal, `,` thousands).
- Single-file Typer CLI with `process`, `generate-reports`, and
  `generate-cost-basis` commands.

## Project layout

```
app/
  config.py              Constants and filesystem paths.
  main.py                Typer CLI entrypoint (python -m app.main).
  models/                Pydantic Transaction model + FIFO dataclasses.
  parsers/               Broker-specific CSV parsers (+ registry).
  services/              Pure business logic: ingestion, FIFO, holdings.
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
# Smoke-test: parse + run FIFO and print a summary
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
report is not enough - you need one row per still-open FIFO lot.

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

## Running tests

```bash
python -m pytest tests/ -q
```

## Environment overrides

Two env vars let you point the tool at non-default directories - useful
when running inside Docker:

| Variable                          | Purpose                            |
| --------------------------------- | ---------------------------------- |
| `PORTFOLIO_LEDGER_INPUT_DIR`      | Override the default `./input/`.   |
| `PORTFOLIO_LEDGER_OUTPUT_DIR`     | Override the default `./output/`.  |

## Adding a new broker

1. Subclass `app.parsers.base.BrokerParser`.
2. Implement `can_parse()` (sniff the file header) and `parse()`
   (yield `Transaction` objects).
3. Register the new class in `app/parsers/registry.py`.

The rest of the application (FIFO, holdings, reports) is broker-agnostic
and requires no further changes.
