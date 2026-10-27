"""Presentation of a finished report. Never reads or changes CSV data."""

import copy
from typing import Any

from config import REPORT_VIEWS

COMPACT_ISSUE_LIMIT = 20
_COMPACT_KEYS = (
    "schema_version", "request_id", "generated_at", "app_version", "profile",
    "options", "source", "valid", "summary", "checks", "issues", "normalization",
)


def apply_report_view(report: dict[str, Any], view: str) -> dict[str, Any]:
    """Return a new report in the requested view; the input is never mutated.

    full    - the complete report, unchanged.
    compact - the same verdict, counters, checks and normalization plan, but
              without header and column statistics, and at most 20 issues.
    """
    if view not in REPORT_VIEWS:
        raise ValueError("report view must be full or compact")
    if view == "full":
        return copy.deepcopy(report)

    result = {key: copy.deepcopy(report[key]) for key in _COMPACT_KEYS}
    result["view"] = "compact"
    result["table"] = {
        "row_count": report["table"]["row_count"],
        "column_count": report["table"]["column_count"],
    }
    result["issues"] = result["issues"][:COMPACT_ISSUE_LIMIT]
    summary = result["summary"]
    summary["shown_issue_count"] = len(result["issues"])
    summary["omitted_issue_count"] = summary["issue_count"] - len(result["issues"])
    return result
