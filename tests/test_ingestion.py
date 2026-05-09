"""Tests for `app.services.ingestion`.

The latest-file picker is the most important piece to pin down here:
Scalable Capital exports are cumulative, so accidentally ingesting two
exports would double-count every transaction.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.services.ingestion import (
    extract_source_date,
    ingest_input_directory,
    select_latest_export,
)


# Minimal valid Scalable Capital CSV so the parser actually runs.
_CSV_HEADER = (
    "date;time;status;reference;description;assetType;type;isin;"
    "shares;price;amount;fee;tax;currency\n"
)
_CSV_BODY = (
    '2024-01-15;10:00:00;Executed;"R1";"ServiceNow";Security;Buy;'
    'US81762P1021;10;77,75;-778,49;0,99;0,00;EUR\n'
)


def _write_csv(path: Path, body: str = _CSV_BODY) -> Path:
    path.write_text(_CSV_HEADER + body, encoding="utf-8")
    return path


class TestSelectLatestExport:
    def test_picks_newest_by_filename_date(self, tmp_path: Path) -> None:
        old = _write_csv(tmp_path / "2024-01-01_ScalableCapital.csv")
        mid = _write_csv(tmp_path / "2025-06-15_ScalableCapital.csv")
        new = _write_csv(tmp_path / "2026-05-09_ScalableCapital.csv")

        assert select_latest_export(tmp_path) == new
        # Sanity - the older files exist and were considered.
        assert old.exists() and mid.exists()

    def test_uses_time_component_for_same_day_exports(self, tmp_path: Path) -> None:
        morning = _write_csv(tmp_path / "2026-05-09_08-00-00_export.csv")
        evening = _write_csv(tmp_path / "2026-05-09_19-30-00_export.csv")

        assert select_latest_export(tmp_path) == evening
        assert morning.exists()

    def test_ignores_hidden_files(self, tmp_path: Path) -> None:
        (tmp_path / ".DS_Store").write_text("noise")
        real = _write_csv(tmp_path / "2026-05-09_export.csv")

        assert select_latest_export(tmp_path) == real

    def test_timestamped_file_beats_un_timestamped(self, tmp_path: Path) -> None:
        """A file with a date prefix always wins over one without."""
        plain = _write_csv(tmp_path / "manual_export.csv")
        dated = _write_csv(tmp_path / "2024-01-01_export.csv")

        assert select_latest_export(tmp_path) == dated
        assert plain.exists()

    def test_falls_back_to_name_when_no_dates(self, tmp_path: Path) -> None:
        a = _write_csv(tmp_path / "alpha.csv")
        b = _write_csv(tmp_path / "beta.csv")

        # Deterministic fallback: alphabetical "last" wins.
        assert select_latest_export(tmp_path) == b
        assert a.exists()

    def test_empty_directory_returns_none(self, tmp_path: Path) -> None:
        assert select_latest_export(tmp_path) is None


class TestIngestInputDirectory:
    def test_only_latest_file_is_processed(self, tmp_path: Path) -> None:
        """End-to-end: superseded files must not contribute transactions."""
        ramu = tmp_path / "ramu"
        ramu.mkdir()

        old = _write_csv(ramu / "2024-01-01_export.csv", body=_CSV_BODY * 2)
        new = _write_csv(ramu / "2026-05-09_export.csv", body=_CSV_BODY)

        result = ingest_input_directory(input_dir=tmp_path)

        # Only one Buy from the newest file should land in the result.
        assert len(result.transactions) == 1
        assert result.files_processed == [new]
        assert old in result.files_skipped

    def test_per_account_latest_selection(self, tmp_path: Path) -> None:
        """Each account folder picks its own latest file independently."""
        (tmp_path / "ramu").mkdir()
        (tmp_path / "rakshana").mkdir()

        ramu_old = _write_csv(tmp_path / "ramu" / "2024-01-01_export.csv")
        ramu_new = _write_csv(tmp_path / "ramu" / "2026-05-09_export.csv")
        rakshana_only = _write_csv(
            tmp_path / "rakshana" / "2025-12-31_export.csv"
        )

        result = ingest_input_directory(input_dir=tmp_path)

        assert sorted(result.files_processed) == sorted(
            [ramu_new, rakshana_only]
        )
        assert ramu_old in result.files_skipped
        assert set(result.accounts) == {"ramu", "rakshana"}

    def test_source_dates_populated_per_account(self, tmp_path: Path) -> None:
        """Each account's source date should reflect the chosen export."""
        (tmp_path / "ramu").mkdir()
        (tmp_path / "rakshana").mkdir()

        _write_csv(tmp_path / "ramu" / "2024-01-01_export.csv")
        _write_csv(tmp_path / "ramu" / "2026-05-09_11-05-03_export.csv")
        _write_csv(tmp_path / "rakshana" / "2025-12-31_export.csv")

        result = ingest_input_directory(input_dir=tmp_path)

        assert result.source_dates == {
            "ramu": datetime(2026, 5, 9, 11, 5, 3),
            "rakshana": datetime(2025, 12, 31),
        }


class TestExtractSourceDate:
    def test_date_only(self, tmp_path: Path) -> None:
        path = tmp_path / "2026-05-09_export.csv"
        path.write_text("noop")
        assert extract_source_date(path) == datetime(2026, 5, 9)

    def test_date_and_time(self, tmp_path: Path) -> None:
        path = tmp_path / "2026-05-09_11-05-03_export.csv"
        path.write_text("noop")
        assert extract_source_date(path) == datetime(2026, 5, 9, 11, 5, 3)

    def test_no_prefix_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "manual_export.csv"
        path.write_text("noop")
        assert extract_source_date(path) is None

    def test_invalid_calendar_date_returns_none(self, tmp_path: Path) -> None:
        # Regex shape passes but month 13 is not a real month.
        path = tmp_path / "2026-13-09_export.csv"
        path.write_text("noop")
        assert extract_source_date(path) is None
