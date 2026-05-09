"""Centralised configuration for the Portfolio Ledger application.

All filesystem paths, decimal precision settings and report defaults
are kept here so that the rest of the codebase never needs to perform
ad-hoc path manipulation. Anything that might reasonably change in a
production deployment (e.g. when running inside Docker with mounted
volumes) lives in this single module and can be overridden via the
PORTFOLIO_LEDGER_INPUT_DIR / PORTFOLIO_LEDGER_OUTPUT_DIR environment
variables.
"""

from __future__ import annotations

import os
from decimal import getcontext
from pathlib import Path
from typing import Final

# ---------------------------------------------------------------------------
# Project root resolution
# ---------------------------------------------------------------------------
# `__file__` points at app/config.py. Going up one parent yields the project
# root regardless of the current working directory. This is critical when the
# CLI is invoked from a different folder or from inside Docker.
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent


def _resolve_dir(env_var: str, default: Path) -> Path:
    """Resolve a directory from an environment variable or fall back.

    The override is useful in containerised setups where input/output
    directories are mounted at well-known paths (e.g. /data/input).
    """

    raw = os.environ.get(env_var)
    return Path(raw).expanduser().resolve() if raw else default


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
