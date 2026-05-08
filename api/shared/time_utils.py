"""Shared helpers for date-range parsing and SQL building."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def parse_range(req_params: dict, default_days: int = 30) -> tuple[str, str]:
    """Return (from_date, to_date) inclusive, ISO yyyy-mm-dd.

    Accepts ?from=YYYY-MM-DD&to=YYYY-MM-DD with sensible defaults.
    """
    today = datetime.now(timezone.utc).date()
    to_str = req_params.get("to") or today.isoformat()
    from_str = req_params.get("from") or (today - timedelta(days=default_days - 1)).isoformat()
    # Validate
    datetime.strptime(from_str, "%Y-%m-%d")
    datetime.strptime(to_str, "%Y-%m-%d")
    if from_str > to_str:
        from_str, to_str = to_str, from_str
    return from_str, to_str


def filter_clause(params: dict) -> tuple[str, list[dict]]:
    """Build WHERE conjuncts and parameter list from optional filters.

    Always includes `c.date BETWEEN @from AND @to` plus optional
    client / model / host filters.
    """
    conds = ["c.date >= @from", "c.date <= @to"]
    p = [{"name": "@from", "value": params["from"]}, {"name": "@to", "value": params["to"]}]
    if params.get("client"):
        conds.append("c.client = @client")
        p.append({"name": "@client", "value": params["client"]})
    if params.get("model"):
        conds.append("c.model = @model")
        p.append({"name": "@model", "value": params["model"]})
    if params.get("host"):
        conds.append("c.host = @host")
        p.append({"name": "@host", "value": params["host"]})
    return " AND ".join(conds), p
