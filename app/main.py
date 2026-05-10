"""Typer-based CLI entry point.

Commands:

    process               Parse + run the tax-lot engine and print a
                          summary (smoke test).

    generate-reports      Parse + tax-lot match + write selected reports.
                          Chooses reports and formats interactively unless
                          ``--reports`` and ``--format`` are both supplied.

    generate-cost-basis   Per-lot cost-basis transfer report only.

Shared options: ``--account``, ``--input-dir``, ``--verbose``.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Optional

import typer

from app.cli.prompts import (
    merge_cli_report_selection,
    parse_cash_cli_entries,
    prompt_current_cash_interactive,
)
from app.config import (
    DEFAULT_CURRENCY,
    INPUT_DIR,
    PORTFOLIO_LEDGER_ISIN_IGNORE_RULES,
)
from app.models import Transaction
from app.reports import ReportFormat, ReportKind, ReportManager, ReportPayload
from app.services import (
    HoldingRow,
    TaxLotEngine,
    apply_portfolio_isin_exclusions,
    build_combined_portfolio,
    build_cost_basis_rows,
    build_current_holdings,
    ingest_input_directory,
    merge_cash_into_combined,
)
from app.utils.decimal_utils import format_money
from app.utils.logging import configure_logging, get_logger

# `add_completion=False` keeps the CLI footprint minimal - we do not
# need shell completion for an internal financial tool.
app = typer.Typer(
    add_completion=False,
    help="Portfolio Ledger - Scalable Capital transaction processor.",
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Shared option types - declared once for reuse across commands.
# ---------------------------------------------------------------------------
AccountOption = typer.Option(
    None,
    "--account",
    "-a",
    help="Process only the named account folder (e.g. 'ramu').",
)
InputDirOption = typer.Option(
    None,
    "--input-dir",
    help="Override the default input directory.",
    show_default=False,
)
VerboseOption = typer.Option(
    False, "--verbose", "-v", help="Enable DEBUG-level logging."
)
ReportsOption = typer.Option(
    None,
    "--reports",
    "-r",
    help=(
        "Logical report to generate; repeatable: tax-lots, holdings, "
        "combined. Use together with --format for non-interactive runs."
    ),
)
GenerateFormatsOption = typer.Option(
    None,
    "--format",
    "-f",
    help=(
        "Output format(s); repeatable: csv, excel, pdf, all. "
        "Use together with --reports for non-interactive runs."
    ),
)
CashEntriesOption = typer.Option(
    None,
    "--cash",
    help=(
        "Current idle cash per account folder (non-interactive). "
        "Repeatable as account:amount."
    ),
)
CostBasisFormatOption = typer.Option(
    [ReportFormat.CSV, ReportFormat.EXCEL, ReportFormat.PDF],
    "--format",
    "-f",
    help=(
        "Output format(s). Repeatable: `-f csv -f pdf`. "
        "Use `-f all` for every format."
    ),
)
ApplyIsinIgnoreOption = typer.Option(
    False,
    "--apply-isin-ignore",
    help=(
        "Omit holdings matched by PORTFOLIO_LEDGER_IGNORE_ISINS from current "
        "holdings and combined portfolio reports (see .env)."
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _resolve_input_dir(override: Optional[Path]) -> Path:
    """Apply the `--input-dir` override or fall back to config."""
    return override.resolve() if override else INPUT_DIR


def _detect_currency(transactions: Iterable[Transaction]) -> str:
    """Pick the dominant currency code present in the ingested data.

    Scalable Capital DE always reports EUR, but writing this generically
    lets us load future broker exports without hard-coding a currency.
    If no transactions are present (e.g. an empty filtered run) we fall
    back to the project default. Mixed-currency exports log a warning
    so the operator knows the report uses one currency throughout.
    """

    counter: Counter[str] = Counter(
        tx.currency.upper() for tx in transactions if tx.currency
    )
    if not counter:
        return DEFAULT_CURRENCY

    if len(counter) > 1:
        logger.warning(
            "Multiple currencies present in ingestion (%s). Reports will "
            "use the most common one; consider splitting accounts that "
            "trade in different currencies.",
            dict(counter),
        )

    return counter.most_common(1)[0][0]


def _apply_isin_ignore_if_requested(
    holdings: list[HoldingRow],
    apply_ignore: bool,
) -> list[HoldingRow]:
    """Filter holdings when ``--apply-isin-ignore`` is set."""

    if not apply_ignore:
        return holdings
    return apply_portfolio_isin_exclusions(
        holdings,
        PORTFOLIO_LEDGER_ISIN_IGNORE_RULES,
    )


def _expand_formats(formats: list[ReportFormat]) -> list[ReportFormat]:
    """Expand :attr:`~ReportFormat.ALL` and deduplicate."""

    expanded: list[ReportFormat] = []
    for fmt in formats:
        if fmt is None:  # pragma: no cover - defensive
            continue
        if fmt == ReportFormat.ALL:
            expanded.extend(ReportFormat.all())
        else:
            expanded.append(fmt)

    seen: set[ReportFormat] = set()
    unique: list[ReportFormat] = []
    for fmt in expanded:
        if fmt not in seen:
            seen.add(fmt)
            unique.append(fmt)
    return unique


# ---------------------------------------------------------------------------
# `process` command
# ---------------------------------------------------------------------------
@app.command()
def process(
    account: Optional[str] = AccountOption,
    input_dir: Optional[Path] = InputDirOption,
    apply_isin_ignore: bool = ApplyIsinIgnoreOption,
    verbose: bool = VerboseOption,
) -> None:
    """Parse transactions and run the tax-lot engine, then print a summary."""

    configure_logging(verbose=verbose)

    ingestion = ingest_input_directory(
        input_dir=_resolve_input_dir(input_dir),
        account_filter=account,
    )

    engine = TaxLotEngine()
    tax_lot_result = engine.process(ingestion.transactions)

    holdings = build_current_holdings(
        tax_lot_result.open_lots,
        cost_adjustments=tax_lot_result.cost_adjustments,
    )
    holdings = _apply_isin_ignore_if_requested(holdings, apply_isin_ignore)
    combined = build_combined_portfolio(holdings)
    currency = _detect_currency(ingestion.transactions)

    _print_summary(
        accounts=ingestion.accounts,
        n_transactions=len(ingestion.transactions),
        n_realized=len(tax_lot_result.realized_trades),
        total_realized=tax_lot_result.total_realized_gain,
        currency=currency,
        n_holdings=len(holdings),
        n_combined=len(combined),
    )


# ---------------------------------------------------------------------------
# `generate-reports` command
# ---------------------------------------------------------------------------
@app.command("generate-reports")
def generate_reports(
    account: Optional[str] = AccountOption,
    input_dir: Optional[Path] = InputDirOption,
    reports: Optional[list[ReportKind]] = ReportsOption,
    formats: Optional[list[ReportFormat]] = GenerateFormatsOption,
    cash_entries: Optional[list[str]] = CashEntriesOption,
    apply_isin_ignore: bool = ApplyIsinIgnoreOption,
    verbose: bool = VerboseOption,
) -> None:
    """Generate selected reports (interactive prompts unless flags fully specify)."""

    configure_logging(verbose=verbose)

    report_plan = merge_cli_report_selection(
        reports_arg=reports,
        formats_arg=formats,
    )
    if not report_plan:
        typer.echo("No reports selected.", err=True)
        raise typer.Exit(code=1)

    ingestion = ingest_input_directory(
        input_dir=_resolve_input_dir(input_dir),
        account_filter=account,
    )

    engine = TaxLotEngine()
    tax_lot_result = engine.process(ingestion.transactions)

    holdings = build_current_holdings(
        tax_lot_result.open_lots,
        cost_adjustments=tax_lot_result.cost_adjustments,
    )
    holdings = _apply_isin_ignore_if_requested(holdings, apply_isin_ignore)
    combined = build_combined_portfolio(holdings)

    non_interactive_cash = (
        reports is not None
        and formats is not None
        and len(reports) > 0
        and len(formats) > 0
    )
    cash_by_account: dict[str, Decimal] = {}
    if ReportKind.COMBINED in report_plan:
        if non_interactive_cash:
            cash_by_account = parse_cash_cli_entries(
                cash_entries, ingestion.accounts,
            )
        elif typer.confirm(
            "Include current cash as a position in the combined portfolio "
            "report (family allocation %)? You will enter idle cash per folder.",
            default=False,
        ):
            cash_by_account = prompt_current_cash_interactive(
                ingestion.accounts,
            )

    combined = merge_cash_into_combined(
        combined,
        cash_by_account,
        ingestion.accounts,
    )

    payload = ReportPayload(
        realized_trades=tax_lot_result.realized_trades,
        holdings=holdings,
        combined_portfolio=combined,
        account_names=ingestion.accounts,
        source_dates=ingestion.source_dates,
        currency=_detect_currency(ingestion.transactions),
    )

    expanded_plan = {
        kind: _expand_formats(list(fmt_list))
        for kind, fmt_list in report_plan.items()
    }

    manager = ReportManager()
    written = manager.write(
        payload=payload,
        report_formats=expanded_plan,
    )

    typer.echo("\nGenerated reports:")
    for path in written:
        typer.echo(f"  - {path}")
    typer.echo("")


# ---------------------------------------------------------------------------
# `generate-cost-basis` command
# ---------------------------------------------------------------------------
@app.command("generate-cost-basis")
def generate_cost_basis(
    account: Optional[str] = AccountOption,
    input_dir: Optional[Path] = InputDirOption,
    formats: list[ReportFormat] = CostBasisFormatOption,
    verbose: bool = VerboseOption,
) -> None:
    """Generate the per-lot Cost Basis Transfer report.

    This is a specialised, infrequent artefact used when transferring
    assets between brokers (e.g. Scalable Capital -> IBKR). The
    receiving broker requires the acquisition price of EACH still-held
    lot - not the per-ISIN average shown by the regular holdings report
    - so this command projects every still-open tax lot onto its own
    row and writes one file per requested format under
    `output/{csv,excel,pdf}/cost_basis_transfer_{stamp}.*`.

    Reuses the same ingestion + tax-lot pipeline as `generate-reports`,
    but writes only the cost-basis report.
    """

    configure_logging(verbose=verbose)

    ingestion = ingest_input_directory(
        input_dir=_resolve_input_dir(input_dir),
        account_filter=account,
    )

    engine = TaxLotEngine()
    tax_lot_result = engine.process(ingestion.transactions)

    cost_basis = build_cost_basis_rows(tax_lot_result.open_lots)

    payload = ReportPayload(
        cost_basis=cost_basis,
        account_names=ingestion.accounts,
        source_dates=ingestion.source_dates,
        currency=_detect_currency(ingestion.transactions),
    )

    manager = ReportManager()
    written = manager.write_cost_basis(
        payload=payload,
        formats=_expand_formats(formats),
    )

    typer.echo("\nGenerated cost-basis report(s):")
    for path in written:
        typer.echo(f"  - {path}")
    typer.echo("")


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
def _print_summary(
    *,
    accounts: list[str],
    n_transactions: int,
    n_realized: int,
    total_realized,
    currency: str,
    n_holdings: int,
    n_combined: int,
) -> None:
    """Pretty-print the post-processing summary to stdout."""

    typer.echo("")
    typer.echo("Portfolio Ledger - Processing Summary")
    typer.echo("=" * 50)
    typer.echo(f"Accounts processed     : {', '.join(accounts) or '(none)'}")
    typer.echo(f"Transactions ingested  : {n_transactions}")
    typer.echo(f"Reporting currency     : {currency}")
    typer.echo(f"Realized trades        : {n_realized}")
    typer.echo(
        "Total realized G/L     : "
        + format_money(total_realized, currency)
        + "  (PRE-TAX; withholding tax tracked separately)"
    )
    typer.echo(f"Open positions         : {n_holdings}")
    typer.echo(f"Combined ISINs         : {n_combined}")
    typer.echo("")


# ---------------------------------------------------------------------------
# Entry point - allows `python -m app.main`.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app()
