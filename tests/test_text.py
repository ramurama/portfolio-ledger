"""Tests for `app.utils.text`."""

from __future__ import annotations

from app.utils.text import display_account_name


class TestDisplayAccountName:
    def test_capitalizes_first_letter(self) -> None:
        assert display_account_name("ramu") == "Ramu"
        assert display_account_name("rakshana") == "Rakshana"

    def test_already_capitalized_is_idempotent(self) -> None:
        assert display_account_name("Ramu") == "Ramu"

    def test_preserves_internal_capitalisation(self) -> None:
        # Stylised names (e.g. JPMorgan-style) must NOT be lower-cased.
        assert display_account_name("jPMorgan") == "JPMorgan"
        assert display_account_name("mcDonald") == "McDonald"

    def test_handles_single_character(self) -> None:
        assert display_account_name("a") == "A"

    def test_empty_string_unchanged(self) -> None:
        assert display_account_name("") == ""
