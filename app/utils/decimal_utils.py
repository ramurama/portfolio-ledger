"""Decimal parsing and formatting helpers.

Scalable Capital (and most German broker exports) writes numbers in the
"de-DE" locale where:

    * `,` (comma)  is the decimal separator
    * `.` (period) is the thousands separator

Examples we have to handle:

    "1.200"       -> Decimal("1200")        # one thousand two hundred shares
    "5.064,36"    -> Decimal("5064.36")     # mixed thousand + decimal
    "0,225348"    -> Decimal("0.225348")    # fractional shares
    "-29,999903"  -> Decimal("-29.999903")  # negative amount
    ""            -> None                   # empty / unset cell

The output reports must be in US format (period as decimal, optional
comma as thousands separator). Polars writes Decimals in plain "1234.56"
form by default which is already valid US formatting; for Excel and PDF
reports we additionally apply a thousands separator.

This module is **the single source of truth** for that conversion - no
other code should call `str.replace` on numbers.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional

# A pre-built zero used as a safe default. Decimal is immutable so sharing
# the instance is cheap and avoids re-parsing "0" thousands of times.
ZERO: Decimal = Decimal("0")


def parse_german_decimal(raw: Optional[str]) -> Optional[Decimal]:
    """Convert a German-formatted numeric string into a `Decimal`.

    Returns `None` when the input is empty / whitespace, so callers can
    distinguish "no value provided" from "value is zero" - this matters
    in the CSV where Distribution rows have no `shares`/`price` while
    Sell rows always do.

    Raises `ValueError` if the string is non-empty but not parseable;
    callers should catch this and re-raise with row context.
    """

    if raw is None:
        return None

    cleaned = raw.strip()
    if not cleaned:
        return None

    # Strip the German thousands separator first, then swap the decimal
    # separator. Order matters: doing it the other way around would turn
    # "1.200,00" into "1200.0,0" which Decimal cannot parse.
    normalised = cleaned.replace(".", "").replace(",", ".")

    try:
        return Decimal(normalised)
    except InvalidOperation as exc:
        raise ValueError(f"Cannot parse German decimal: {raw!r}") from exc


def parse_german_decimal_or_zero(raw: Optional[str]) -> Decimal:
    """Like :func:`parse_german_decimal` but treats empty cells as zero.

    Useful for fields like `fee` and `tax` where an empty cell genuinely
    means "no fee charged" rather than "value unknown".
    """

    parsed = parse_german_decimal(raw)
    return parsed if parsed is not None else ZERO


def format_us_decimal(
    value: Optional[Decimal],
    quantize: Optional[str] = None,
    thousands: bool = True,
) -> str:
    """Format a Decimal in US locale: `.` decimal, optional `,` thousands.

    Parameters
    ----------
    value:
        The Decimal to render. `None` becomes an empty string so reports
        can distinguish "missing" from "zero".
    quantize:
        Optional quantization template (e.g. "0.01"). Applied with
        ROUND_HALF_UP semantics inherited from the active Decimal context.
    thousands:
        When True, insert `,` as the thousands separator. CSV outputs
        typically leave this off (one number = one cell, no ambiguity)
        while Excel/PDF use it for human readability.
    """

    if value is None:
        return ""

    if quantize is not None:
        value = value.quantize(Decimal(quantize))

    # Python's f-string `:,` format spec works for Decimals only via float
    # conversion which would defeat the whole point of using Decimal. We
    # therefore implement thousands grouping manually.
    sign = "-" if value < 0 else ""
    abs_str = str(abs(value))

    if "." in abs_str:
        int_part, frac_part = abs_str.split(".", 1)
    else:
        int_part, frac_part = abs_str, ""

    if thousands:
        # Group from the right in chunks of 3 to avoid surprises with
        # very large integer parts (millions, billions, etc.).
        grouped_chars: list[str] = []
        for idx, ch in enumerate(reversed(int_part)):
            if idx and idx % 3 == 0:
                grouped_chars.append(",")
            grouped_chars.append(ch)
        int_part = "".join(reversed(grouped_chars))

    if frac_part:
        return f"{sign}{int_part}.{frac_part}"
    return f"{sign}{int_part}"


def safe_divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Divide two Decimals, returning zero when the denominator is zero.

    Used by weighted-average price calculations where an account holding
    zero shares of an ISIN would otherwise blow up the report.
    """

    if denominator == 0:
        return ZERO
    return numerator / denominator
