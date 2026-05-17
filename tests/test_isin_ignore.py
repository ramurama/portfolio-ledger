"""Tests for portfolio+ISIN exclusions (env parsing and holdings filtering)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.config import parse_portfolio_isin_ignore_rules
from app.models import OpenLot
from app.services.cost_basis import (
    apply_cost_basis_isin_exclusions,
    build_cost_basis_rows,
)
from app.services.holdings import (
    apply_portfolio_isin_exclusions,
    build_current_holdings,
)


def _lot(
    account: str,
    isin: str,
    symbol: str,
    shares: str,
    cost_per_share: str,
    when: datetime = datetime(2024, 1, 1),
) -> OpenLot:
    return OpenLot(
        account_name=account,
        isin=isin,
        symbol=symbol,
        buy_date=when,
        original_shares=Decimal(shares),
        remaining_shares=Decimal(shares),
        cost_per_share=Decimal(cost_per_share),
    )


class TestParsePortfolioIsinIgnoreRules:
    def test_empty_and_whitespace(self) -> None:
        assert parse_portfolio_isin_ignore_rules(None) == {}
        assert parse_portfolio_isin_ignore_rules("") == {}
        assert parse_portfolio_isin_ignore_rules("  ,  ") == {}

    def test_single_pair(self) -> None:
        assert parse_portfolio_isin_ignore_rules(
            "rakshana:DE000EWG2LD7",
        ) == {"rakshana": frozenset({"DE000EWG2LD7"})}

    def test_multiple_pairs_merge_same_account(self) -> None:
        out = parse_portfolio_isin_ignore_rules(
            "rakshana:DE000EWG2LD7,rakshana:US1234567890",
        )
        assert out["rakshana"] == frozenset({"DE000EWG2LD7", "US1234567890"})

    def test_case_normalisation(self) -> None:
        out = parse_portfolio_isin_ignore_rules(
            "Rakshana:de000ewg2ld7",
        )
        assert out["rakshana"] == frozenset({"DE000EWG2LD7"})

    def test_skips_malformed_segments(self) -> None:
        assert parse_portfolio_isin_ignore_rules("no-colon,ok:X1") == {
            "ok": frozenset({"X1"}),
        }


class TestApplyPortfolioIsinExclusions:
    def test_no_rules_returns_copy_semantics(self) -> None:
        rows = build_current_holdings([
            _lot("ramu", "ISIN_A", "A", "1", "100"),
        ])
        out = apply_portfolio_isin_exclusions(rows, {})
        assert out == rows
        assert out is not rows

    def test_drops_matching_row_and_reweights_percentages(self) -> None:
        rows = build_current_holdings([
            _lot("rakshana", "DROP", "X", "1", "250"),
            _lot("rakshana", "KEEP", "Y", "1", "750"),
        ])
        rules = {"rakshana": frozenset({"DROP"})}
        out = apply_portfolio_isin_exclusions(rows, rules)
        assert len(out) == 1
        assert out[0].isin == "KEEP"
        assert out[0].portfolio_percentage == Decimal("100")

    def test_only_affects_named_portfolio(self) -> None:
        rows = build_current_holdings([
            _lot("ramu", "ISIN_A", "A", "1", "500"),
            _lot("rakshana", "ISIN_A", "A", "1", "500"),
        ])
        rules = {"rakshana": frozenset({"ISIN_A"})}
        out = apply_portfolio_isin_exclusions(rows, rules)
        by_acc = {(r.account_name, r.isin): r for r in out}
        assert ("rakshana", "ISIN_A") not in by_acc
        assert by_acc[("ramu", "ISIN_A")].portfolio_percentage == Decimal("100")


class TestApplyCostBasisIsinExclusions:
    def test_drops_matching_lots_for_named_account(self) -> None:
        rows = build_cost_basis_rows([
            _lot("rakshana", "DROP", "X", "1", "10"),
            _lot("rakshana", "KEEP", "Y", "1", "20"),
            _lot("ramu", "DROP", "X", "1", "30"),
        ])
        rules = {
            "rakshana": frozenset({"DROP"}),
            "ramu": frozenset({"DROP"}),
        }
        out = apply_cost_basis_isin_exclusions(rows, rules)
        assert len(out) == 1
        assert out[0].account_name == "rakshana"
        assert out[0].isin == "KEEP"

    def test_no_rules_returns_equivalent_list(self) -> None:
        rows = build_cost_basis_rows([_lot("ramu", "ISIN_A", "A", "1", "100")])
        out = apply_cost_basis_isin_exclusions(rows, {})
        assert out == rows
