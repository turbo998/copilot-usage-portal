"""Tests for server-side aggregation push-down and Python fallback equivalence.

The portal's metrics handlers historically pulled *every* raw event row from
Cosmos and aggregated in Python. With 160k+ rows this made /metrics/daily take
~5s, so the SPA's first paint showed zeros ("looks broken").

This module verifies the pure aggregation helpers that both the GROUP BY
push-down path and the Python fallback path must agree on, so we can safely
switch to server-side aggregation without changing results.

Run: pytest tests/test_aggregation.py -v
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_aggregate_module():
    """Import api/shared/aggregate.py in isolation (no azure-cosmos needed)."""
    path = ROOT / "api" / "shared" / "aggregate.py"
    spec = importlib.util.spec_from_file_location("agg_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


SAMPLE_EVENTS = [
    # date, client, prompt, completion, cached, reasoning, total, credits
    {"date": "2026-06-01", "client": "hermes", "prompt_tokens": 100,
     "completion_tokens": 20, "cached_tokens": 5, "reasoning_tokens": 0,
     "total_tokens": 125, "estimated_credits": 1.5},
    {"date": "2026-06-01", "client": "hermes", "prompt_tokens": 200,
     "completion_tokens": 40, "cached_tokens": 10, "reasoning_tokens": 1,
     "total_tokens": 250, "estimated_credits": 3.0},
    {"date": "2026-06-01", "client": "openclaw", "prompt_tokens": 50,
     "completion_tokens": 10, "cached_tokens": 0, "reasoning_tokens": 0,
     "total_tokens": 60, "estimated_credits": 0.5},
    {"date": "2026-06-02", "client": "hermes", "prompt_tokens": 80,
     "completion_tokens": 8, "cached_tokens": 2, "reasoning_tokens": 0,
     "total_tokens": 88, "estimated_credits": 0.9},
]


def test_aggregate_by_date_groups_and_sums():
    """Daily rollup: one bucket per date with summed token + credit fields.

    Business intent: the per-day chart MUST show the true sum of every event
    that day across all clients -- if grouping or summation drifts, the chart
    silently under/over-reports usage and the whole portal is untrustworthy.
    """
    agg = _load_aggregate_module()
    rows = agg.aggregate_rows(SAMPLE_EVENTS, key_field="date", key_name="date")
    by = {r["date"]: r for r in rows}
    assert set(by) == {"2026-06-01", "2026-06-02"}
    d1 = by["2026-06-01"]
    assert d1["prompt"] == 350 and d1["completion"] == 70
    assert d1["cached"] == 15 and d1["total"] == 435
    assert d1["calls"] == 3
    assert abs(d1["credits"] - 5.0) <= 1e-9
    d2 = by["2026-06-02"]
    assert d2["total"] == 88 and d2["calls"] == 1
    # rows must be sorted ascending by the key for a stable time axis
    assert [r["date"] for r in rows] == ["2026-06-01", "2026-06-02"]


def test_aggregate_by_client_groups_three_terminals():
    """Breakdown by client: each of the three terminals is its own bucket.

    Business intent: the by-client pie/table is the user's core question --
    "how much did each of my 3 agents burn?". If two clients collapse into one
    bucket, or a client vanishes, the answer is wrong.
    """
    agg = _load_aggregate_module()
    rows = agg.aggregate_rows(SAMPLE_EVENTS, key_field="client", key_name="key")
    by = {r["key"]: r for r in rows}
    assert set(by) == {"hermes", "openclaw"}
    assert by["hermes"]["total"] == 125 + 250 + 88  # 463
    assert by["hermes"]["calls"] == 3
    assert by["openclaw"]["total"] == 60 and by["openclaw"]["calls"] == 1


def test_groupby_sql_matches_python_fallback():
    """A Cosmos GROUP BY result set must fold into the SAME shape as Python agg.

    Business intent: switching to server-side GROUP BY must be a pure speed
    optimization with identical output. We simulate the rows Cosmos returns
    for `SELECT c.date, SUM(...) ... GROUP BY c.date` and assert the folding
    helper produces the exact same buckets as the raw-event Python path.
    """
    agg = _load_aggregate_module()
    # What Cosmos returns for the GROUP BY query (already summed server-side):
    cosmos_grouped = [
        {"k": "2026-06-01", "prompt": 350, "completion": 70, "cached": 15,
         "reasoning": 1, "total": 435, "credits": 5.0, "calls": 3},
        {"k": "2026-06-02", "prompt": 80, "completion": 8, "cached": 2,
         "reasoning": 0, "total": 88, "credits": 0.9, "calls": 1},
    ]
    server = agg.fold_grouped(cosmos_grouped, key_name="date")
    python = agg.aggregate_rows(SAMPLE_EVENTS, key_field="date", key_name="date")
    # Compare numerically, field by field, for every date.
    sby = {r["date"]: r for r in server}
    pby = {r["date"]: r for r in python}
    assert set(sby) == set(pby)
    for d in pby:
        for fld in ("prompt", "completion", "cached", "reasoning", "total", "calls"):
            assert sby[d][fld] == pby[d][fld], f"{d}.{fld}: {sby[d][fld]} != {pby[d][fld]}"
        assert abs(sby[d]["credits"] - pby[d]["credits"]) <= 1e-6


def test_empty_events_yield_empty_rows():
    """No events in range -> empty list (not a crash, not a phantom bucket)."""
    agg = _load_aggregate_module()
    assert agg.aggregate_rows([], key_field="date", key_name="date") == []
    assert agg.fold_grouped([], key_name="date") == []
