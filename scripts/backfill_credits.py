#!/usr/bin/env python3
"""Backfill ``estimated_credits`` on historical ``usage_events`` documents.

Why
---
The 2026-06-01 GitHub Copilot AI Credits migration changed the cost
algorithm in two ways:

1.  ``credits = USD * 100`` (new flat conversion).
2.  Anthropic ``cache_creation_input_tokens`` is billed at its own
    ``cache_write`` rate instead of being folded into ``prompt_tokens``.

Events ingested before commit ``fix(cost): separate cache_write
pricing`` therefore carry stale ``estimated_credits`` values. This
script recomputes them in-place using the current cost table.

Cosmos auth
-----------
* Production / Function App identity: ``DefaultAzureCredential`` (managed
  identity in Azure; ``az login`` locally with RBAC).
* Local dev: set ``COSMOS_KEY`` to fall back to key-based auth.

Required env
------------
  COSMOS_ENDPOINT          https://<acct>.documents.azure.com:443/
  COSMOS_DATABASE          copilot_usage
  COSMOS_EVENTS_CONTAINER  usage_events
  COSMOS_KEY               (optional — dev only)

Defaults to dry-run. Pass ``--apply`` to actually write back.

Examples
--------
  # Dry-run last 30 days, group diffs by model
  python scripts/backfill_credits.py --from 2026-05-01 --to 2026-06-01

  # Apply changes, 500 docs per batch (default)
  python scripts/backfill_credits.py --from 2026-05-01 --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# Allow ``from shared.cost_table import estimate_credits`` when run from repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))


def _load_estimate_credits():
    """Lazy import so ``--help`` works without azure-cosmos installed."""
    from shared.cost_table import estimate_credits  # noqa: WPS433

    return estimate_credits


# ---------------------------------------------------------------------------
# Cosmos client
# ---------------------------------------------------------------------------
def _cosmos_container():
    """Return the usage_events container client.

    Prefers managed identity / az-login (RBAC) and falls back to a key when
    ``COSMOS_KEY`` is present (local dev only — do not ship keys to prod).
    """
    from azure.cosmos import CosmosClient  # type: ignore

    endpoint = os.environ["COSMOS_ENDPOINT"]
    db_name = os.environ["COSMOS_DATABASE"]
    container_name = os.environ["COSMOS_EVENTS_CONTAINER"]

    key = os.environ.get("COSMOS_KEY")
    if key:
        client = CosmosClient(url=endpoint, credential=key)
    else:
        from azure.identity import DefaultAzureCredential  # type: ignore

        cred = DefaultAzureCredential(exclude_interactive_browser_credential=False)
        client = CosmosClient(url=endpoint, credential=cred)

    return client.get_database_client(db_name).get_container_client(container_name)


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
def _recompute(doc: dict, estimate_credits) -> float:
    return estimate_credits(
        doc.get("model") or "unknown",
        int(doc.get("prompt_tokens") or 0),
        int(doc.get("completion_tokens") or 0),
        int(doc.get("cached_tokens") or 0),
        int(doc.get("cache_write_tokens") or 0),
    )


def _query(container, date_from: str | None, date_to: str | None) -> Iterable[dict]:
    """Stream usage events in the [date_from, date_to) range."""
    clauses, params = [], []
    if date_from:
        clauses.append("c.date >= @from")
        params.append({"name": "@from", "value": date_from})
    if date_to:
        clauses.append("c.date < @to")
        params.append({"name": "@to", "value": date_to})
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM c {where}"
    return container.query_items(
        query=sql, parameters=params, enable_cross_partition_query=True
    )


def _diff_report(per_model_diffs: dict[str, list[tuple[str, float, float]]]) -> None:
    """Print top-10 largest absolute diffs per model."""
    print("\n=== per-model diff report (top 10 by |Δcredits|) ===")
    for model in sorted(per_model_diffs):
        rows = per_model_diffs[model]
        rows.sort(key=lambda x: abs(x[2] - x[1]), reverse=True)
        total_old = sum(r[1] for r in rows)
        total_new = sum(r[2] for r in rows)
        delta = total_new - total_old
        print(
            f"\n  {model}  ({len(rows)} events, "
            f"Σold={total_old:.2f} → Σnew={total_new:.2f}, Δ={delta:+.2f})"
        )
        for doc_id, old_c, new_c in rows[:10]:
            d = new_c - old_c
            print(f"    {doc_id:40s}  {old_c:12.4f} → {new_c:12.4f}  Δ={d:+.4f}")


def run(date_from: str | None, date_to: str | None, batch_size: int, apply: bool) -> int:
    estimate_credits = _load_estimate_credits()
    container = _cosmos_container()
    per_model: dict[str, list[tuple[str, float, float]]] = defaultdict(list)

    scanned = 0
    changed = 0
    written = 0
    batch: list[dict] = []

    def flush(batch: list[dict]) -> int:
        if not apply or not batch:
            return 0
        n = 0
        for doc in batch:
            container.replace_item(item=doc["id"], body=doc)
            n += 1
        return n

    for doc in _query(container, date_from, date_to):
        scanned += 1
        model = doc.get("model") or "unknown"
        old_credits = float(doc.get("estimated_credits") or 0.0)
        new_credits = _recompute(doc, estimate_credits)
        if abs(new_credits - old_credits) > 1e-9:
            changed += 1
            per_model[model].append((doc.get("id", "?"), old_credits, new_credits))
            doc["estimated_credits"] = new_credits
            # Backward-compat: ensure new field is present so future passes are stable.
            doc.setdefault("cache_write_tokens", 0)
            batch.append(doc)
            if len(batch) >= batch_size:
                written += flush(batch)
                batch.clear()
        if scanned % 5000 == 0:
            print(f"  scanned={scanned} changed={changed} written={written}", flush=True)

    written += flush(batch)

    print(
        f"\nDone. scanned={scanned} changed={changed} "
        f"written={written} apply={apply}"
    )
    _diff_report(per_model)

    if not apply and changed:
        print("\n(dry-run) re-run with --apply to persist the changes above.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Recompute estimated_credits on usage_events using the current cost table.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--from",
        dest="date_from",
        metavar="YYYY-MM-DD",
        help="Recompute events with date >= this value.",
    )
    p.add_argument(
        "--to",
        dest="date_to",
        metavar="YYYY-MM-DD",
        help="Recompute events with date < this value.",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Number of docs to replace before logging a progress line.",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Persist changes. Without this flag the script is dry-run only.",
    )
    args = p.parse_args()
    for k in ("date_from", "date_to"):
        v = getattr(args, k)
        if v:
            try:
                datetime.strptime(v, "%Y-%m-%d")
            except ValueError:
                p.error(f"--{k.replace('date_', '')} must be YYYY-MM-DD, got {v!r}")
    return args


def main() -> int:
    args = _parse_args()
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(
        f"[{started}] backfill_credits {mode} "
        f"from={args.date_from or '-∞'} to={args.date_to or '+∞'} "
        f"batch_size={args.batch_size}"
    )
    return run(args.date_from, args.date_to, args.batch_size, args.apply)


if __name__ == "__main__":
    sys.exit(main())
