"""Copilot Usage Portal — Azure Functions (Python v2 programming model)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import azure.functions as func

from shared.auth import require_collector_key, require_principal
from shared.cosmos_repo import events_container, query, upsert_event
from shared.cost_table import estimate_credits, model_family, normalise_model_id
from shared.time_utils import filter_clause, parse_range

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# /ingest
# ---------------------------------------------------------------------------

ALLOWED_CLIENTS = {"copilot-cli", "openclaw", "hermes"}
REQUIRED_FIELDS = ("id", "client", "ts", "model")


def _normalise_event(ev: dict) -> dict:
    """Normalise + enrich one event in-place; returns the normalised dict."""
    ev = dict(ev)  # defensive copy
    # Normalise model
    raw_model = ev.get("model", "")
    nm = normalise_model_id(raw_model)
    ev["model"] = nm
    ev["model_family"] = model_family(raw_model)
    # Tokens
    pt = int(ev.get("prompt_tokens") or 0)
    ct = int(ev.get("completion_tokens") or 0)
    cached = int(ev.get("cached_tokens") or 0)
    reasoning = int(ev.get("reasoning_tokens") or 0)
    ev["prompt_tokens"] = pt
    ev["completion_tokens"] = ct
    ev["cached_tokens"] = cached
    ev["reasoning_tokens"] = reasoning
    ev["total_tokens"] = int(ev.get("total_tokens") or (pt + ct))
    # Date partition helper from ts
    ts = ev.get("ts") or ""
    try:
        ev["date"] = ts[:10] if ts else datetime.now(timezone.utc).date().isoformat()
    except Exception:
        ev["date"] = datetime.now(timezone.utc).date().isoformat()
    # Cost
    ev["estimated_credits"] = estimate_credits(raw_model, pt, ct, cached)
    return ev


@app.route(route="ingest", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def ingest(req: func.HttpRequest) -> func.HttpResponse:
    if not require_collector_key(req):
        return func.HttpResponse(json.dumps({"error": "invalid X-Collector-Key"}),
                                 status_code=401, mimetype="application/json")
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(json.dumps({"error": "invalid json"}),
                                 status_code=400, mimetype="application/json")

    events = body.get("events") if isinstance(body, dict) else None
    if not isinstance(events, list):
        return func.HttpResponse(json.dumps({"error": "events must be a list"}),
                                 status_code=400, mimetype="application/json")
    if len(events) > 100:
        return func.HttpResponse(json.dumps({"error": "max 100 events per batch"}),
                                 status_code=413, mimetype="application/json")

    accepted = 0
    duped = 0
    rejected: list[dict] = []
    for raw in events:
        if not isinstance(raw, dict):
            rejected.append({"reason": "not an object"})
            continue
        missing = [f for f in REQUIRED_FIELDS if not raw.get(f)]
        if missing:
            rejected.append({"id": raw.get("id"), "reason": f"missing {missing}"})
            continue
        if raw["client"] not in ALLOWED_CLIENTS:
            rejected.append({"id": raw["id"], "reason": "unknown client"})
            continue
        try:
            ev = _normalise_event(raw)
            ok, dup = upsert_event(ev)
            if ok and dup:
                duped += 1
            elif ok:
                accepted += 1
        except Exception as exc:  # pragma: no cover
            log.exception("Failed to ingest event %s", raw.get("id"))
            rejected.append({"id": raw.get("id"), "reason": str(exc)})

    return func.HttpResponse(
        json.dumps({"accepted": accepted, "deduped": duped, "rejected": rejected}),
        status_code=200,
        mimetype="application/json",
    )


# ---------------------------------------------------------------------------
# /api/metrics/* helpers
# ---------------------------------------------------------------------------

def _guard(req: func.HttpRequest) -> tuple[bool, func.HttpResponse | None]:
    if require_principal(req) is None:
        return False, func.HttpResponse(json.dumps({"error": "unauthorized"}),
                                        status_code=401, mimetype="application/json")
    return True, None


def _resolve_filters(req: func.HttpRequest, default_days: int = 30) -> dict:
    f, t = parse_range(dict(req.params), default_days=default_days)
    return {"from": f, "to": t,
            "client": req.params.get("client"), "model": req.params.get("model"),
            "host": req.params.get("host")}


def _json(payload: Any, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(payload, default=str), status_code=status,
                             mimetype="application/json")


def _fetch_raw_events(filters: dict, fields: tuple[str, ...]) -> list[dict]:
    """Fetch raw events as a list of dicts; aggregation is done client-side.

    Cosmos rejects cross-partition GROUP BY on the events container
    (partition key = /client) unless you use 'SELECT VALUE <agg>' alone.
    Pulling raw rows + aggregating in Python keeps the handlers simple and
    works with serverless Cosmos. Volume is bounded by the date-range filter.
    """
    where, params = filter_clause(filters)
    select = ", ".join(f"c.{f}" for f in fields)
    sql = f"SELECT {select} FROM c WHERE {where}"
    return query(sql, params=params)


def _new_bucket() -> dict:
    return {"prompt": 0, "completion": 0, "cached": 0, "reasoning": 0,
            "total": 0, "credits": 0.0, "calls": 0}


def _accumulate(bucket: dict, ev: dict) -> None:
    bucket["prompt"] += int(ev.get("prompt_tokens") or 0)
    bucket["completion"] += int(ev.get("completion_tokens") or 0)
    bucket["cached"] += int(ev.get("cached_tokens") or 0)
    bucket["reasoning"] += int(ev.get("reasoning_tokens") or 0)
    bucket["total"] += int(ev.get("total_tokens") or 0)
    bucket["credits"] += float(ev.get("estimated_credits") or 0)
    bucket["calls"] += 1


# ---------------------------------------------------------------------------
# /api/metrics/daily
# ---------------------------------------------------------------------------

@app.route(route="metrics/daily", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def metrics_daily(req: func.HttpRequest) -> func.HttpResponse:
    ok, err = _guard(req)
    if not ok:
        return err  # type: ignore[return-value]
    filters = _resolve_filters(req)
    events = _fetch_raw_events(filters, ("date", "prompt_tokens", "completion_tokens",
                                         "cached_tokens", "reasoning_tokens",
                                         "total_tokens", "estimated_credits"))
    by_date: dict[str, dict] = {}
    for ev in events:
        d = ev.get("date") or ""
        bucket = by_date.setdefault(d, {"date": d, **_new_bucket()})
        _accumulate(bucket, ev)
    rows = sorted(by_date.values(), key=lambda r: r["date"])
    for r in rows:
        r["credits"] = round(r["credits"], 4)
    return _json({"from": filters["from"], "to": filters["to"], "data": rows})


# ---------------------------------------------------------------------------
# /api/metrics/weekly
# ---------------------------------------------------------------------------

def _iso_week(d: str) -> str:
    dt = datetime.strptime(d, "%Y-%m-%d").date()
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


@app.route(route="metrics/weekly", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def metrics_weekly(req: func.HttpRequest) -> func.HttpResponse:
    ok, err = _guard(req)
    if not ok:
        return err  # type: ignore[return-value]
    filters = _resolve_filters(req, default_days=84)
    events = _fetch_raw_events(filters, ("date", "prompt_tokens", "completion_tokens",
                                         "cached_tokens", "reasoning_tokens",
                                         "total_tokens", "estimated_credits"))
    weekly: dict[str, dict] = {}
    for ev in events:
        d = ev.get("date") or ""
        if not d:
            continue
        try:
            wk = _iso_week(d)
        except Exception:
            continue
        bucket = weekly.setdefault(wk, {"week": wk, **_new_bucket()})
        _accumulate(bucket, ev)
    out = sorted(weekly.values(), key=lambda r: r["week"])
    for r in out:
        r["credits"] = round(r["credits"], 4)
    return _json({"from": filters["from"], "to": filters["to"], "data": out})


# ---------------------------------------------------------------------------
# /api/metrics/breakdown?dim=client|model|host|model_family
# ---------------------------------------------------------------------------

ALLOWED_DIMS = {"client", "model", "host", "model_family"}


@app.route(route="metrics/breakdown", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def metrics_breakdown(req: func.HttpRequest) -> func.HttpResponse:
    ok, err = _guard(req)
    if not ok:
        return err  # type: ignore[return-value]
    dim = req.params.get("dim", "client")
    if dim not in ALLOWED_DIMS:
        return _json({"error": f"dim must be one of {sorted(ALLOWED_DIMS)}"}, 400)
    filters = _resolve_filters(req)
    events = _fetch_raw_events(filters, (dim, "prompt_tokens", "completion_tokens",
                                         "cached_tokens", "reasoning_tokens",
                                         "total_tokens", "estimated_credits"))
    by_key: dict[str, dict] = {}
    for ev in events:
        k = ev.get(dim) or "unknown"
        bucket = by_key.setdefault(k, {"key": k, **_new_bucket()})
        _accumulate(bucket, ev)
    grand = sum(b["total"] for b in by_key.values()) or 1
    rows = []
    for b in by_key.values():
        rows.append({"key": b["key"], "total": b["total"],
                     "credits": round(b["credits"], 4), "calls": b["calls"],
                     "share": round(b["total"] / grand, 6)})
    rows.sort(key=lambda r: r["total"], reverse=True)
    return _json({"dim": dim, "from": filters["from"], "to": filters["to"], "data": rows})


# ---------------------------------------------------------------------------
# /api/metrics/heatmap
# ---------------------------------------------------------------------------

@app.route(route="metrics/heatmap", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def metrics_heatmap(req: func.HttpRequest) -> func.HttpResponse:
    ok, err = _guard(req)
    if not ok:
        return err  # type: ignore[return-value]
    filters = _resolve_filters(req)
    events = _fetch_raw_events(filters, ("client", "model", "total_tokens",
                                         "estimated_credits"))
    pairs: dict[tuple[str, str], dict] = {}
    for ev in events:
        c = ev.get("client")
        m = ev.get("model")
        if not c or not m:
            continue
        bucket = pairs.setdefault((c, m), {"total": 0, "credits": 0.0, "calls": 0})
        bucket["total"] += int(ev.get("total_tokens") or 0)
        bucket["credits"] += float(ev.get("estimated_credits") or 0)
        bucket["calls"] += 1
    clients = sorted({c for c, _ in pairs.keys()})
    models = sorted({m for _, m in pairs.keys()})
    matrix = [[0] * len(models) for _ in clients]
    credit_matrix = [[0.0] * len(models) for _ in clients]
    call_matrix = [[0] * len(models) for _ in clients]
    for (c, m), b in pairs.items():
        i = clients.index(c)
        j = models.index(m)
        matrix[i][j] = b["total"]
        credit_matrix[i][j] = round(b["credits"], 4)
        call_matrix[i][j] = b["calls"]
    return _json({"from": filters["from"], "to": filters["to"],
                  "rows": clients, "cols": models,
                  "tokens": matrix, "credits": credit_matrix, "calls": call_matrix})


# ---------------------------------------------------------------------------
# /api/metrics/top-sessions
# ---------------------------------------------------------------------------

@app.route(route="metrics/top-sessions", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def metrics_top_sessions(req: func.HttpRequest) -> func.HttpResponse:
    ok, err = _guard(req)
    if not ok:
        return err  # type: ignore[return-value]
    try:
        n = max(1, min(100, int(req.params.get("n", "10"))))
    except ValueError:
        n = 10
    filters = _resolve_filters(req)
    events = _fetch_raw_events(filters, ("session_id", "client", "model", "ts",
                                         "total_tokens", "estimated_credits"))
    by_sess: dict[tuple[str, str, str], dict] = {}
    for ev in events:
        sid = ev.get("session_id")
        if not sid:
            continue
        key = (sid, ev.get("client") or "", ev.get("model") or "")
        bucket = by_sess.setdefault(key, {"session_id": key[0], "client": key[1],
                                          "model": key[2], "total": 0, "credits": 0.0,
                                          "calls": 0, "first_ts": None, "last_ts": None})
        bucket["total"] += int(ev.get("total_tokens") or 0)
        bucket["credits"] += float(ev.get("estimated_credits") or 0)
        bucket["calls"] += 1
        ts = ev.get("ts")
        if ts:
            if bucket["first_ts"] is None or ts < bucket["first_ts"]:
                bucket["first_ts"] = ts
            if bucket["last_ts"] is None or ts > bucket["last_ts"]:
                bucket["last_ts"] = ts
    rows = sorted(by_sess.values(), key=lambda r: r["total"], reverse=True)
    for r in rows:
        r["credits"] = round(r["credits"], 4)
    return _json({"from": filters["from"], "to": filters["to"], "data": rows[:n]})


# ---------------------------------------------------------------------------
# /api/metrics/cost
# ---------------------------------------------------------------------------

@app.route(route="metrics/cost", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def metrics_cost(req: func.HttpRequest) -> func.HttpResponse:
    ok, err = _guard(req)
    if not ok:
        return err  # type: ignore[return-value]
    filters = _resolve_filters(req)
    events = _fetch_raw_events(filters, ("date", "model", "total_tokens",
                                         "estimated_credits"))
    by_date: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    for ev in events:
        d = ev.get("date") or ""
        m = ev.get("model") or "unknown"
        tt = int(ev.get("total_tokens") or 0)
        cc = float(ev.get("estimated_credits") or 0)
        b1 = by_date.setdefault(d, {"date": d, "credits": 0.0, "total": 0})
        b1["credits"] += cc
        b1["total"] += tt
        b2 = by_model.setdefault(m, {"model": m, "credits": 0.0, "total": 0})
        b2["credits"] += cc
        b2["total"] += tt
    daily = sorted(by_date.values(), key=lambda r: r["date"])
    for r in daily:
        r["credits"] = round(r["credits"], 4)
    by_model_rows = sorted(by_model.values(), key=lambda r: r["credits"], reverse=True)
    for r in by_model_rows:
        r["credits"] = round(r["credits"], 4)

    total_credits = round(sum(float(r.get("credits") or 0) for r in daily), 4)

    today = datetime.now(timezone.utc).date()
    month_start = today.replace(day=1)
    if today.month == 12:
        next_month_start = today.replace(year=today.year + 1, month=1, day=1)
    else:
        next_month_start = today.replace(month=today.month + 1, day=1)
    month_total_days = (next_month_start - month_start).days

    last = daily[-14:] if daily else []
    avg = (sum(float(r.get("credits") or 0) for r in last) / len(last)) if last else 0.0
    elapsed_days = (today - month_start).days + 1
    mtd = sum(float(r.get("credits") or 0) for r in daily if r.get("date", "") >= month_start.isoformat())
    projected = round(mtd + avg * max(0, month_total_days - elapsed_days), 4)

    return _json({
        "from": filters["from"], "to": filters["to"],
        "daily": daily, "by_model": by_model_rows,
        "total_credits": total_credits,
        "month_to_date_credits": round(mtd, 4),
        "projected_month_credits": projected,
        "notes": "estimated_credits is computed at ingest time from cost_table seed; verify rates against GitHub docs.",
    })


# ---------------------------------------------------------------------------
# /api/health (anonymous, used by SWA/probe)
# ---------------------------------------------------------------------------

@app.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health(req: func.HttpRequest) -> func.HttpResponse:
    return _json({"status": "ok", "ts": datetime.now(timezone.utc).isoformat()})


# ---------------------------------------------------------------------------
# /admin/seed-cost-table — gated by INGEST_KEY (collectors' shared secret).
# Reads cost_table_seed.json bundled inside the function app and upserts each
# row into the cost_table Cosmos container using the function app's MI.
# Idempotent — safe to call repeatedly.
# ---------------------------------------------------------------------------

@app.route(route="metrics/seed-cost-table", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def admin_seed_cost_table(req: func.HttpRequest) -> func.HttpResponse:
    if not require_collector_key(req):
        return _json({"error": "invalid X-Collector-Key"}, status=401)
    import os
    from pathlib import Path
    from shared.cosmos_repo import cost_container

    candidates = [
        Path(__file__).parent / "cost_table_seed.json",
        Path(__file__).parent.parent / "infra" / "cost_table_seed.json",
    ]
    seed_path = next((p for p in candidates if p.exists()), None)
    if seed_path is None:
        return _json({"error": "cost_table_seed.json not bundled with deployment",
                      "tried": [str(p) for p in candidates]}, status=500)
    try:
        rows = json.loads(seed_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _json({"error": f"failed to parse seed file: {exc}"}, status=500)
    if not isinstance(rows, list):
        return _json({"error": "seed file must be a JSON array"}, status=500)

    container = cost_container()
    upserted = 0
    failed: list[dict] = []
    for row in rows:
        try:
            container.upsert_item(row)
            upserted += 1
        except Exception as exc:  # pragma: no cover
            failed.append({"id": row.get("id"), "error": str(exc)})

    return _json({"source": str(seed_path), "upserted": upserted,
                  "total": len(rows), "failed": failed})


# ---------------------------------------------------------------------------
# Admin: wipe events for a given client.
# Path uses metrics/ prefix so SWA linkedBackend allow-list lets it through.
# Body: {"client": "openclaw"}  (matches partition key /client exactly)
# ---------------------------------------------------------------------------

@app.route(route="metrics/admin-wipe-client", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def admin_wipe_client(req: func.HttpRequest) -> func.HttpResponse:
    if not require_collector_key(req):
        return _json({"error": "invalid X-Collector-Key"}, status=401)
    try:
        body = req.get_json() or {}
    except Exception:
        body = {}
    client_name = (body.get("client") or "").strip()
    if not client_name:
        return _json({"error": "missing 'client' field"}, status=400)

    from shared.cosmos_repo import events_container
    c = events_container()
    deleted = 0
    failed = 0
    try:
        # Partition-key-scoped query (no cross-partition fanout)
        items = list(c.query_items(
            query="SELECT c.id FROM c",
            partition_key=client_name,
        ))
    except Exception as exc:  # pragma: no cover
        return _json({"error": f"query failed: {exc}"}, status=500)

    for it in items:
        try:
            c.delete_item(item=it["id"], partition_key=client_name)
            deleted += 1
        except Exception:
            failed += 1

    return _json({"client": client_name, "deleted": deleted,
                  "failed": failed, "queried": len(items)})
