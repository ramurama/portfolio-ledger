"""Portfolio Ledger application package.

Top-level package for the Scalable Capital portfolio ledger tool.

The package is organized into four cooperating layers:

    parsers/   - Raw broker file -> normalized `Transaction` objects.
    services/  - Pure business logic (ingestion, tax-lot engine,
                 holdings, combined portfolio aggregation).
    reports/   - Format-specific renderers (CSV, Excel, PDF) plus a
                 thin orchestrator that fans the same in-memory model
                 out to every requested format.
    utils/     - Stateless, framework-agnostic helpers (Decimal/date
                 parsing, logging configuration).

The boundaries are intentional: parsers know nothing about reports,
reports know nothing about parsers, and services depend only on the
unified `models` package. This keeps the codebase ready for future
broker integrations - a new broker only needs a new parser module.
"""

__version__ = "1.0.0"
