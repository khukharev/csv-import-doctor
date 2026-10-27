"""Report views: compact keeps the verdict and counters, never mutates input."""

import copy

import pytest

from checks import inspect_csv
from report_views import COMPACT_ISSUE_LIMIT, apply_report_view

# 30 rows with leading-zero IDs -> 30 C07 warnings, more than the compact limit.
MANY_WARNINGS = b"id,name\n" + b"".join(b"0%02d,x%d\n" % (i, i) for i in range(30))


@pytest.fixture
def report():
    return inspect_csv(MANY_WARNINGS, id_column="id")


def test_full_view_is_an_equal_independent_copy(report):
    original = copy.deepcopy(report)
    full = apply_report_view(report, "full")
    assert full == original
    full["issues"].clear()
    assert report == original


def test_compact_view_keeps_verdict_counters_and_checks(report):
    compact = apply_report_view(report, "compact")
    assert compact["view"] == "compact"
    assert compact["valid"] is report["valid"] is True
    assert compact["checks"] == report["checks"]
    assert compact["normalization"] == report["normalization"]
    assert compact["request_id"] == report["request_id"]
    assert compact["table"] == {"row_count": 30, "column_count": 2}
    assert "column_stats" not in compact
    summary = compact["summary"]
    assert summary["warning_count"] == report["summary"]["warning_count"] == 30
    assert summary["issue_count"] == 30
    assert summary["shown_issue_count"] == len(compact["issues"]) == COMPACT_ISSUE_LIMIT == 20
    assert summary["omitted_issue_count"] == 10
    assert compact["issues"] == report["issues"][:20]


def test_compact_view_does_not_mutate_input(report):
    original = copy.deepcopy(report)
    compact = apply_report_view(report, "compact")
    compact["summary"]["error_count"] = 99
    compact["issues"].clear()
    assert report == original


def test_compact_view_of_small_report_shows_everything():
    small = inspect_csv(b"id,name\n001,a\n", id_column="id")
    compact = apply_report_view(small, "compact")
    assert compact["issues"] == small["issues"]
    assert compact["summary"]["omitted_issue_count"] == 0


@pytest.mark.parametrize("view", ["", "Full", "short", None])
def test_unknown_view_is_rejected(report, view):
    with pytest.raises(ValueError):
        apply_report_view(report, view)
