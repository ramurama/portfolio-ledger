"""Report renderers (CSV, Excel, PDF) plus a thin orchestrator.

Public API:

    * `ReportFormat`     - enumeration of supported output formats.
    * `ReportPayload`    - bundle of the three logical reports we know
                           how to render.
    * `ReportManager`    - given a `ReportPayload` and a set of formats,
                           writes every requested file to disk.
"""

from app.reports.report_manager import ReportFormat, ReportManager, ReportPayload

__all__ = ["ReportFormat", "ReportManager", "ReportPayload"]
