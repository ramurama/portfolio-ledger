"""Centralised configuration for the Portfolio Ledger application.

All filesystem paths, decimal precision settings, report defaults and
transaction-type filters are kept here so that the rest of the codebase
never needs to read environment variables directly. Anything that might
reasonably change in a production deployment (e.g. when running inside
Docker with mounted volumes) lives in this single module and can be
overridden via environment variables or the project-level `.env` file.
"""

from __future__ import annotations

import os
from decimal import getcontext
from pathlib import Path
from typing import Final, Optional


# ---------------------------------------------------------------------------
# Project root resolution
# ---------------------------------------------------------------------------
# `__file__` points at app/config.py. Going up one parent yields the project
# root regardless of the current working directory. This is critical when the
# CLI is invoked from a different folder or from inside Docker.
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE pairs from `.env` without extra dependencies."""

    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            os.environ.setdefault(key, value)


_load_dotenv(PROJECT_ROOT / ".env")


def _resolve_dir(env_var: str, default: Path) -> Path:
    """Resolve a directory from an environment variable or fall back.

    The override is useful in containerised setups where input/output
    directories are mounted at well-known paths (e.g. /data/input).
    """

    raw = os.environ.get(env_var)
    return Path(raw).expanduser().resolve() if raw else default


def _resolve_csv_list(env_var: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Resolve a comma-separated environment variable into a clean tuple."""

    raw = os.environ.get(env_var)
    if not raw:
        return default

    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    return values or default


def parse_portfolio_isin_ignore_rules(raw: Optional[str]) -> dict[str, frozenset[str]]:
    """Parse ``PORTFOLIO_LEDGER_IGNORE_ISINS``-style text into a lookup map.

    Each comma-separated entry must be ``<account_folder>:<ISIN>`` where
    ``account_folder`` matches an ``input/<name>/`` directory (compared
    case-insensitively). ISINs are normalised to upper case.

    Example::

        rakshana:DE000EWG2LD7,ramu:US5949181045
    """

    if not raw:
        return {}

    by_account_lower: dict[str, set[str]] = {}
    for piece in raw.split(","):
        entry = piece.strip()
        if not entry or ":" not in entry:
            continue
        account_part, isin_part = entry.split(":", 1)
        account_key = account_part.strip().lower()
        isin_norm = isin_part.strip().upper()
        if not account_key or not isin_norm:
            continue
        by_account_lower.setdefault(account_key, set()).add(isin_norm)

    return {k: frozenset(v) for k, v in by_account_lower.items()}


# ---------------------------------------------------------------------------
# Filesystem layout
# ---------------------------------------------------------------------------
INPUT_DIR: Final[Path] = _resolve_dir(
    "PORTFOLIO_LEDGER_INPUT_DIR", PROJECT_ROOT / "input"
)
OUTPUT_DIR: Final[Path] = _resolve_dir(
    "PORTFOLIO_LEDGER_OUTPUT_DIR", PROJECT_ROOT / "output"
)

# Sub-folders for each output format. Created lazily by the report manager.
OUTPUT_CSV_DIR: Final[Path] = OUTPUT_DIR / "csv"
OUTPUT_EXCEL_DIR: Final[Path] = OUTPUT_DIR / "excel"
OUTPUT_PDF_DIR: Final[Path] = OUTPUT_DIR / "pdf"

# ---------------------------------------------------------------------------
# Decimal configuration
# ---------------------------------------------------------------------------
# All money math goes through `decimal.Decimal`. We bump the precision well
# above what any realistic share price needs so we never silently lose
# precision while computing weighted averages or pro-rata cost basis.
DECIMAL_PRECISION: Final[int] = 28
getcontext().prec = DECIMAL_PRECISION

# Quantization templates used when displaying values. We keep these as
# strings so callers can pass them straight to Decimal.quantize().
MONEY_QUANTIZE: Final[str] = "0.01"      # two decimals for currency amounts
SHARE_QUANTIZE: Final[str] = "0.000001"  # six decimals for fractional shares
PRICE_QUANTIZE: Final[str] = "0.0001"    # four decimals for share prices

# ---------------------------------------------------------------------------
# Report defaults
# ---------------------------------------------------------------------------
# Filename timestamp format. Spec uses `{generated_date}` placeholders -
# we use a sortable, filesystem-safe format that includes the time so
# multiple runs in the same day don't collide.
REPORT_TIMESTAMP_FORMAT: Final[str] = "%Y-%m-%d_%H-%M-%S"

# Default currency assumed by the application. Scalable Capital exports
# everything in EUR, but we keep this configurable so a future broker
# integration that reports in a different currency can override it.
DEFAULT_CURRENCY: Final[str] = "EUR"

# ---------------------------------------------------------------------------
# Transaction filtering
# ---------------------------------------------------------------------------
# Raw broker `type` values admitted during parsing. Anything not listed here
# is ignored before it can affect tax-lot, holdings or portfolio reports.
SUPPORTED_TRANSACTION_TYPES: Final[tuple[str, ...]] = _resolve_csv_list(
    "PORTFOLIO_LEDGER_TRANSACTION_TYPES",
    (
        "Buy",
        "Sell",
        "Savings plan",
        "Distribution",
        "Taxes",
        "Tax",
        "Security transfer",
        "Corporate action",
    ),
)

# Per-account ISINs to omit from current-holdings and combined-family reports
# when the CLI enables exclusions (see ``--apply-isin-ignore``). Parsed at
# import time from the environment; an empty / unset variable means no rules.
PORTFOLIO_LEDGER_ISIN_IGNORE_RULES: Final[dict[str, frozenset[str]]] = (
    parse_portfolio_isin_ignore_rules(
        os.environ.get("PORTFOLIO_LEDGER_IGNORE_ISINS"),
    )
)

# Optional OpenFIGI key for ISIN → ticker resolution when fetching live
# quotes on the combined portfolio report (higher rate limits).
OPENFIGI_API_KEY: Final[Optional[str]] = os.environ.get("OPENFIGI_API_KEY") or None
