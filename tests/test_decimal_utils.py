"""Tests for `app.utils.decimal_utils`.

These tests pin down the German <-> US decimal conversion behaviour
since that is the single most error-prone piece of the application.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.utils.decimal_utils import (
    currency_symbol,
    format_money,
    format_us_decimal,
    parse_german_decimal,
    parse_german_decimal_or_zero,
    parse_money_input,
    safe_divide,
)


class TestParseGermanDecimal:
    def test_simple_decimal(self) -> None:
        assert parse_german_decimal("0,225348") == Decimal("0.225348")

    def test_thousands_separator(self) -> None:
        # "1.200" must mean 1200 (not 1.2) because German uses `.` as
        # the thousands separator.
        assert parse_german_decimal("1.200") == Decimal("1200")

    def test_thousands_and_decimal(self) -> None:
        assert parse_german_decimal("5.064,36") == Decimal("5064.36")

    def test_negative(self) -> None:
        assert parse_german_decimal("-29,999903196") == Decimal("-29.999903196")

    def test_empty_returns_none(self) -> None:
        assert parse_german_decimal("") is None
        assert parse_german_decimal("   ") is None
        assert parse_german_decimal(None) is None

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_german_decimal("abc")

    def test_or_zero_treats_empty_as_zero(self) -> None:
        assert parse_german_decimal_or_zero("") == Decimal("0")
        assert parse_german_decimal_or_zero(None) == Decimal("0")
        assert parse_german_decimal_or_zero("12,34") == Decimal("12.34")


class TestFormatUsDecimal:
    def test_thousands_grouping(self) -> None:
        assert format_us_decimal(Decimal("1234567.89"), "0.01") == "1,234,567.89"

    def test_no_thousands(self) -> None:
        assert (
            format_us_decimal(Decimal("1234567.89"), "0.01", thousands=False)
            == "1234567.89"
        )

    def test_negative(self) -> None:
        assert format_us_decimal(Decimal("-1234.5"), "0.01") == "-1,234.50"

    def test_none_renders_empty(self) -> None:
        assert format_us_decimal(None) == ""

    def test_quantize_rounds(self) -> None:
        # Banker's rounding is the Decimal default; we just need to
        # verify quantize is being applied at all.
        assert format_us_decimal(Decimal("1.234"), "0.01") == "1.23"


class TestParseMoneyInput:
    def test_us_decimal(self) -> None:
        assert parse_money_input("1234.56") == Decimal("1234.56")

    def test_german_style(self) -> None:
        assert parse_money_input("1.234,56") == Decimal("1234.56")

    def test_comma_decimal_only(self) -> None:
        assert parse_money_input("50,25") == Decimal("50.25")

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_money_input("   ")


class TestSafeDivide:
    def test_normal_division(self) -> None:
        assert safe_divide(Decimal("10"), Decimal("4")) == Decimal("2.5")

    def test_zero_denominator_returns_zero(self) -> None:
        assert safe_divide(Decimal("10"), Decimal("0")) == Decimal("0")


class TestCurrencySymbol:
    def test_known_codes(self) -> None:
        assert currency_symbol("EUR") == "\u20ac"  # €
        assert currency_symbol("USD") == "$"
        assert currency_symbol("GBP") == "\u00a3"  # £

    def test_case_insensitive(self) -> None:
        assert currency_symbol("eur") == "\u20ac"

    def test_unknown_code_falls_back_to_iso(self) -> None:
        # Falls back to "<CODE> " so the value is still self-describing.
        assert currency_symbol("AUD") == "AUD "

    def test_empty_returns_empty(self) -> None:
        assert currency_symbol("") == ""
        assert currency_symbol(None) == ""


class TestFormatMoney:
    def test_eur_prefix(self) -> None:
        assert format_money(Decimal("1234.56"), "EUR") == "\u20ac1,234.56"

    def test_negative_keeps_sign_before_symbol(self) -> None:
        # Convention: -€1,234.56 (sign first, then symbol, then magnitude).
        assert format_money(Decimal("-1234.56"), "EUR") == "-\u20ac1,234.56"

    def test_usd_prefix(self) -> None:
        assert format_money(Decimal("99.9"), "USD") == "$99.90"

    def test_unknown_currency_uses_iso_prefix(self) -> None:
        assert format_money(Decimal("10"), "XAU") == "XAU 10.00"

    def test_none_value_returns_empty(self) -> None:
        assert format_money(None, "EUR") == ""

    def test_no_currency_returns_plain_number(self) -> None:
        # When no currency is given we still want a sensible string.
        assert format_money(Decimal("12.5"), None) == "12.50"
        assert format_money(Decimal("12.5"), "") == "12.50"

    def test_custom_quantize(self) -> None:
        assert format_money(Decimal("1.2345"), "EUR", "0.0001") == "\u20ac1.2345"

    def test_format_money_without_symbol(self) -> None:
        assert format_money(
            Decimal("1234.56"), "EUR", include_currency_symbol=False,
        ) == "1,234.56"
        assert format_money(
            Decimal("-99.9"), "USD", include_currency_symbol=False,
        ) == "-99.90"
