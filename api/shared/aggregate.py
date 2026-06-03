"""Pure aggregation helpers shared by the metrics handlers.

These functions contain ZERO Cosmos/Azure dependencies so they can be unit
tested in isolation and reused by both code paths:

  * ``aggregate_rows``  -- fold a list of raw event dicts into per-key buckets
                           (the historical "pull rows, sum in Python" path).
  * ``fold_grouped``    -- fold the result set of a Cosmos ``GROUP BY`` query
                           (already summed server-side) into the SAME shape.

Keeping both paths funnel through one canonical bucket shape guarantees the
GROUP BY push-down is a pure speed optimization with identical output.
"""
from __future__ import annotations

from typing import Any, Iterable

# Canonical numeric fields every bucket carries.
_TOKEN_FIELDS = ("prompt", "completion", "cached", "reasoning", "total")

# Mapping from bucket field -> raw event field.
_EVENT_FIELD = {
    "prompt": "prompt_tokens",
    "completion": "completion_tokens",
    "cached": "cached_tokens",
    "reasoning": "reasoning_tokens",
    "total": "total_tokens",
}


def _empty_bucket() -> dict:
    b = {f: 0 for f in _TOKEN_FIELDS}
    b["credits"] = 0.0
    b["calls"] = 0
    return b


def aggregate_rows(events: Iterable[dict], *, key_field: str, key_name: str) -> list[dict]:
    """Group raw event dicts by ``key_field`` and sum token/credit fields.

    Returns a list of buckets sorted ascending by the key. Each bucket has the
    grouping value under ``key_name`` plus prompt/completion/cached/reasoning/
    total/credits/calls.
    """
    buckets: dict[str, dict] = {}
    for ev in events:
        k = ev.get(key_field) or "unknown"
        b = buckets.get(k)
        if b is None:
            b = {key_name: k, **_empty_bucket()}
            buckets[k] = b
        for fld in _TOKEN_FIELDS:
            b[fld] += int(ev.get(_EVENT_FIELD[fld]) or 0)
        b["credits"] += float(ev.get("estimated_credits") or 0)
        b["calls"] += 1
    rows = sorted(buckets.values(), key=lambda r: r[key_name])
    for r in rows:
        r["credits"] = round(r["credits"], 6)
    return rows


def fold_grouped(grouped: Iterable[dict], *, key_name: str) -> list[dict]:
    """Fold a Cosmos ``GROUP BY`` result set into canonical buckets.

    Each input row is expected to already be summed server-side with aliases:
    ``k`` (group key), ``prompt``/``completion``/``cached``/``reasoning``/
    ``total``/``credits``/``calls``. Missing aliases default to 0. Output shape
    is identical to :func:`aggregate_rows`.
    """
    rows: list[dict] = []
    for g in grouped:
        b = {key_name: g.get("k") if g.get("k") is not None else "unknown"}
        for fld in _TOKEN_FIELDS:
            b[fld] = int(g.get(fld) or 0)
        b["credits"] = round(float(g.get("credits") or 0), 6)
        b["calls"] = int(g.get("calls") or 0)
        rows.append(b)
    rows.sort(key=lambda r: r[key_name])
    return rows


def groupby_sql(key_expr: str, where: str) -> str:
    """Build a cross-partition GROUP BY query that sums every metric field.

    ``key_expr`` is the projected/grouped expression (e.g. ``c.date`` or
    ``c.client``). Cosmos requires the SELECT list and GROUP BY to reference
    the same expression. The aggregates are aliased to match
    :func:`fold_grouped`.
    """
    return (
        f"SELECT {key_expr} AS k, "
        "SUM(c.prompt_tokens ?? 0) AS prompt, "
        "SUM(c.completion_tokens ?? 0) AS completion, "
        "SUM(c.cached_tokens ?? 0) AS cached, "
        "SUM(c.reasoning_tokens ?? 0) AS reasoning, "
        "SUM(c.total_tokens ?? 0) AS total, "
        "SUM(c.estimated_credits ?? 0) AS credits, "
        "COUNT(1) AS calls "
        f"FROM c WHERE {where} "
        f"GROUP BY {key_expr}"
    )
