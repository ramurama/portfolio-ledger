"""Pure business logic services.

These modules never touch the filesystem (other than ingestion, which
walks the input directory) and never produce reports - they only
transform domain models into other domain models. This makes them
trivially unit-testable and means the same logic can be reused by a
future API layer or background worker.
"""

from app.services.fifo_engine import FifoEngine, FifoResult
from app.services.holdings import HoldingRow, build_current_holdings
from app.services.ingestion import IngestionResult, ingest_input_directory
from app.services.portfolio import CombinedHoldingRow, build_combined_portfolio

__all__ = [
    "CombinedHoldingRow",
    "FifoEngine",
    "FifoResult",
    "HoldingRow",
    "IngestionResult",
    "build_combined_portfolio",
    "build_current_holdings",
    "ingest_input_directory",
]
