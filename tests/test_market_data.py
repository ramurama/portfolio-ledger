"""Tests for OpenFIGI + Yahoo market-data helpers (HTTP mocked)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from app.services.market_data import (
    apply_market_quotes_to_combined,
    fetch_last_prices,
    fetch_market_quotes_for_isins,
    resolve_isins_to_yahoo_symbols,
)
from app.services.portfolio import CombinedHoldingRow


def _combined_row(isin: str, shares: str, invested: str) -> CombinedHoldingRow:
    return CombinedHoldingRow(
        isin=isin,
        symbol="Test",
        shares_per_account={"ramu": Decimal(shares)},
        combined_shares=Decimal(shares),
        combined_average_price=Decimal("10"),
        total_invested=Decimal(invested),
        family_percentage=Decimal("100"),
    )


class TestResolveIsinsToYahooSymbols:
    def test_openfigi_maps_german_exchange_to_de_suffix(self) -> None:
        openfigi_response = [
            {
                "data": [
                    {"ticker": "SAP", "exchCode": "GR"},
                ],
            },
        ]

        with patch(
            "app.services.market_data._http_json",
            return_value=openfigi_response,
        ):
            symbols = resolve_isins_to_yahoo_symbols(["DE0007164600"])

        assert symbols["DE0007164600"] == "SAP.DE"

    def test_yahoo_search_fallback_when_openfigi_empty(self) -> None:
        with patch(
            "app.services.market_data._http_json",
            side_effect=[
                [{"data": []}],
                {"quotes": [{"symbol": "IWDA.AS"}]},
            ],
        ):
            symbols = resolve_isins_to_yahoo_symbols(["IE00B4L5Y983"])

        assert symbols["IE00B4L5Y983"] == "IWDA.AS"

    def test_prefers_european_listing_when_openfigi_returns_multiple(self) -> None:
        openfigi_response = [
            {
                "data": [
                    {"ticker": "AAPL", "exchCode": "US"},
                    {"ticker": "APC", "exchCode": "GR"},
                ],
            },
        ]

        with patch(
            "app.services.market_data._http_json",
            return_value=openfigi_response,
        ):
            symbols = resolve_isins_to_yahoo_symbols(["US0378331005"])

        assert symbols["US0378331005"] == "APC.DE"


class TestFetchLastPrices:
    def test_reads_regular_market_price_from_chart(self) -> None:
        chart_payload = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "regularMarketPrice": 123.45,
                            "currency": "EUR",
                        },
                    },
                ],
            },
        }

        with patch(
            "app.services.market_data._http_json",
            return_value=chart_payload,
        ):
            quotes = fetch_last_prices(
                {"DE0007164600": "SAP.DE"},
                target_currency="EUR",
            )

        assert quotes["DE0007164600"].price == Decimal("123.45")
        assert quotes["DE0007164600"].yahoo_symbol == "SAP.DE"

    def test_converts_lse_gbp_pence_quote_to_eur(self) -> None:
        """LSE listings use GBp (pence); must not treat 1324 as pounds."""

        lse_stock = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "regularMarketPrice": 1324.0,
                            "currency": "GBp",
                        },
                    },
                ],
            },
        }
        fx = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "regularMarketPrice": 1.15,
                            "currency": "EUR",
                        },
                    },
                ],
            },
        }

        with patch(
            "app.services.market_data._http_json",
            side_effect=[lse_stock, fx],
        ):
            quotes = fetch_last_prices(
                {"GB0005405286": "HSBA.L"},
                target_currency="EUR",
            )

        # 1324 pence = 13.24 GBP; × 1.15 ≈ 15.226 EUR
        assert quotes["GB0005405286"].price == Decimal("13.24") * Decimal("1.15")

    def test_converts_usd_quote_to_eur(self) -> None:
        us_stock = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "regularMarketPrice": 100,
                            "currency": "USD",
                        },
                    },
                ],
            },
        }
        fx = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "regularMarketPrice": 0.5,
                            "currency": "EUR",
                        },
                    },
                ],
            },
        }

        with patch(
            "app.services.market_data._http_json",
            side_effect=[us_stock, fx],
        ):
            quotes = fetch_last_prices(
                {"US0378331005": "AAPL"},
                target_currency="EUR",
            )

        assert quotes["US0378331005"].price == Decimal("50")


class TestFetchMarketQuotesForIsins:
    def test_end_to_end_with_mocked_http(self) -> None:
        with patch(
            "app.services.market_data._http_json",
            side_effect=[
                [{"data": [{"ticker": "SAP", "exchCode": "GR"}]}],
                {
                    "chart": {
                        "result": [
                            {
                                "meta": {
                                    "regularMarketPrice": 200.5,
                                    "currency": "EUR",
                                },
                            },
                        ],
                    },
                },
            ],
        ):
            quotes = fetch_market_quotes_for_isins(
                ["DE0007164600"],
                target_currency="EUR",
            )

        assert quotes["DE0007164600"].price == Decimal("200.5")


class TestApplyMarketQuotesToCombined:
    def test_attaches_price_fields_and_skips_cash(self) -> None:
        from app.services.market_data import MarketQuote

        rows = [
            _combined_row("ISIN_A", "2", "100"),
            CombinedHoldingRow(
                isin="CASH",
                symbol="Cash",
                shares_per_account={"ramu": Decimal("50")},
                combined_shares=Decimal("0"),
                combined_average_price=Decimal("0"),
                total_invested=Decimal("50"),
                family_percentage=Decimal("33"),
                is_cash=True,
            ),
        ]
        quotes = {
            "ISIN_A": MarketQuote(
                isin="ISIN_A",
                price=Decimal("60"),
                yahoo_symbol="AAA.DE",
            ),
        }

        updated = apply_market_quotes_to_combined(rows, quotes)
        security = updated[0]
        cash = updated[1]

        assert security.current_price == Decimal("60")
        assert security.market_value == Decimal("120")
        assert security.unrealized_gain_loss == Decimal("20")
        assert cash.current_price is None
