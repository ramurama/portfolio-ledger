"""Tests for PDF-only presentation helpers."""

from app.reports.pdf_report import apply_pdf_money_spacing


class TestApplyPdfMoneySpacing:
    def test_inserts_space_after_euro(self) -> None:
        out = apply_pdf_money_spacing("€156.4200")
        assert "\u20ac" in out
        assert out == "\u20ac 156.4200"

    def test_negative_amounts(self) -> None:
        assert apply_pdf_money_spacing("-€1,234.56") == "-\u20ac 1,234.56"

    def test_idempotent(self) -> None:
        once = apply_pdf_money_spacing("€10.00")
        assert apply_pdf_money_spacing(once) == once

    def test_usd(self) -> None:
        assert apply_pdf_money_spacing("$99.90") == "$ 99.90"

    def test_unaffected_text(self) -> None:
        s = "ISIN US123"
        assert apply_pdf_money_spacing(s) == s
