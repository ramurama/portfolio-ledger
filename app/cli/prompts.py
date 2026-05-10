"""Interactive and non-interactive selection of reports and output formats."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

import typer

from app.reports.report_manager import ReportFormat, ReportKind
from app.utils.decimal_utils import parse_money_input
from app.utils.text import display_account_name

_REPORT_LABELS: dict[ReportKind, str] = {
    ReportKind.TAX_LOTS: "Tax Lots (realized gains)",
    ReportKind.HOLDINGS: "Current Holdings",
    ReportKind.COMBINED: "Combined Family Portfolio",
}


def expand_format_list(formats: list[ReportFormat]) -> list[ReportFormat]:
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


def parse_formats_line(raw: str) -> list[ReportFormat]:
    """Parse user input like ``csv pdf``, ``all``, or ``csv,excel``."""

    cleaned = raw.strip().lower()
    if not cleaned:
        raise typer.BadParameter("Enter at least one format (csv, excel, pdf, all).")

    if cleaned == "all":
        return ReportFormat.all()

    tokens = cleaned.replace(",", " ").split()

    result: list[ReportFormat] = []
    for t in tokens:
        try:
            result.append(ReportFormat(t))
        except ValueError as exc:
            raise typer.BadParameter(
                f"Unknown format {t!r}; use csv, excel, pdf, or all."
            ) from exc
    return expand_format_list(result)


def prompt_formats_line(prompt_msg: str) -> list[ReportFormat]:
    raw = typer.prompt(prompt_msg, default="all")
    try:
        return parse_formats_line(raw)
    except typer.BadParameter as exc:
        typer.echo(str(exc), err=True)
        return prompt_formats_line(prompt_msg)


def collect_report_formats_interactive() -> dict[ReportKind, list[ReportFormat]]:
    """Ask which logical reports to emit and which file format(s) for each."""

    plan: dict[ReportKind, list[ReportFormat]] = {}
    typer.echo("")
    typer.echo("Select reports to generate (you will choose CSV / Excel / PDF per report).")

    for kind in ReportKind:
        label = _REPORT_LABELS[kind]
        if not typer.confirm(f"Generate {label}?", default=True):
            continue
        fmts = prompt_formats_line(
            f"  Formats for {label} [csv excel pdf / all, default: all]"
        )
        plan[kind] = fmts

    return plan


def merge_cli_report_selection(
    *,
    reports_arg: Optional[list[ReportKind]],
    formats_arg: Optional[list[ReportFormat]],
) -> dict[ReportKind, list[ReportFormat]]:
    """Build the report plan from CLI flags and/or interactive prompts."""

    if reports_arg is not None and len(reports_arg) == 0:
        raise typer.BadParameter("--reports requires at least one value.")
    if formats_arg is not None and len(formats_arg) == 0:
        raise typer.BadParameter("--format requires at least one value.")

    non_interactive = (
        reports_arg is not None
        and len(reports_arg) > 0
        and formats_arg is not None
        and len(formats_arg) > 0
    )

    if non_interactive:
        fmts = expand_format_list(list(formats_arg))
        if not fmts:
            raise typer.BadParameter("Provide at least one --format.")
        return {kind: fmts for kind in reports_arg}

    if reports_arg is None and formats_arg is None:
        return collect_report_formats_interactive()

    if reports_arg is not None and formats_arg is None:
        if not reports_arg:
            raise typer.BadParameter("--reports requires at least one value.")
        fmts = prompt_formats_line("Output formats for selected reports [csv excel pdf / all]")
        return {kind: fmts for kind in reports_arg}

    if reports_arg is None and formats_arg is not None:
        fmts = expand_format_list(list(formats_arg))
        if not fmts:
            raise typer.BadParameter("Provide at least one --format.")
        typer.echo("")
        typer.echo(
            "Choose reports (--format already set). "
            "Answer y/n for each."
        )
        plan: dict[ReportKind, list[ReportFormat]] = {}
        for kind in ReportKind:
            label = _REPORT_LABELS[kind]
            if typer.confirm(f"Generate {label}?", default=True):
                plan[kind] = fmts
        return plan


def prompt_current_cash_interactive(account_names: list[str]) -> dict[str, Decimal]:
    """Ask for current idle cash held in each portfolio folder."""

    balances: dict[str, Decimal] = {}
    typer.echo("")
    typer.echo(
        "Enter current idle cash (liquid balance not invested in securities) "
        "for each portfolio folder."
    )
    for name in account_names:
        label = display_account_name(name)
        while True:
            raw = typer.prompt(f"Current cash [{label}] ({name})", default="0")
            try:
                balances[name] = parse_money_input(raw)
            except ValueError as exc:
                typer.echo(f"  Invalid amount: {exc}", err=True)
                continue
            break
    return balances


def parse_cash_cli_entries(
    entries: Optional[list[str]],
    valid_accounts: list[str],
) -> dict[str, Decimal]:
    """Parse ``--cash name:amount`` as current idle cash for that folder."""

    if not entries:
        return {}

    valid = set(valid_accounts)
    out: dict[str, Decimal] = {}
    for line in entries:
        if ":" not in line:
            raise typer.BadParameter(
                f"Invalid --cash {line!r}; expected account:amount."
            )
        name, amount_raw = line.split(":", maxsplit=1)
        name = name.strip()
        if name not in valid:
            raise typer.BadParameter(
                f"Unknown account {name!r} in --cash (expected one of {sorted(valid)!r})."
            )
        out[name] = parse_money_input(amount_raw.strip())

    for name in valid_accounts:
        out.setdefault(name, Decimal("0"))

    return out
