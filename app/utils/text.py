"""Text-formatting helpers used by the report layer.

Account / person names live in the codebase exactly as they appear on
disk (the input folder name) so all internal lookups - dict keys, CLI
filters, tax-lot queue keys - stay deterministic. For *display* purposes
however we want the friendlier "Capitalized" form. This module owns
that one transformation so every report applies it consistently.
"""

from __future__ import annotations


def display_account_name(name: str) -> str:
    """Capitalize the first letter of an account/person name.

    We deliberately do NOT use `str.capitalize()` because that also
    lowercases the rest of the string - it would mangle stylised names
    such as "JPMorgan" into "Jpmorgan". Instead we only touch the
    first character, leaving everything else untouched.

    Empty / whitespace-only inputs are returned unchanged so callers
    do not need to guard against degenerate cases.
    """

    if not name:
        return name
    return name[:1].upper() + name[1:]
