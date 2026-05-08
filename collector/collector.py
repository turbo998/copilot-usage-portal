"""Copilot Usage Collector — main entrypoint.

Usage:
    python collector.py --client copilot-cli|openclaw|hermes \
        --ingest-url https://func-xxx.azurewebsites.net/api/ingest?code=... \
        --ingest-key <X-Collector-Key> [--host MY-HOST] [--once]

Persists a cursor in ~/.copilot-usage-collector/cursor.json so each run only
sends new events. On HTTP failure, batches are spooled to .spool/ and retried
on the next tick.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

import requests

from cursor import Cursor

LOG = logging.getLogger("collector")

ROOT = Path(os.path.expanduser("~")) / ".copilot-usage-collector"
SPOOL_DIR = ROOT / "spool"
CURSOR_FILE = ROOT / "cursor.json"
BATCH_LIMIT = 100


def _load_adapter(client: str, cursor: Cursor, host: str | None,
                  source_dir: str | None):
    if client == "copilot-cli":
        from adapters.copilot_cli import CopilotCliAdapter
        return CopilotCliAdapter(cursor, host=host, logs_dir=source_dir)
    if client == "openclaw":
        from adapters.openclaw import OpenClawAdapter
        return OpenClawAdapter(cursor, host=host, sessions_dir=source_dir)
    if client == "hermes":
        from adapters.hermes import HermesAdapter
        return HermesAdapter(cursor, host=host, usage_file=source_dir)
    raise SystemExit(f"unknown client: {client}")


def _post_batch(url: str, key: str, events: list[dict]) -> tuple[bool, dict]:
    try:
        r = requests.post(
            url,
            headers={"Content-Type": "application/json", "X-Collector-Key": key},
            data=json.dumps({"events": events}),
            timeout=30,
        )
        if 200 <= r.status_code < 300:
            try:
                return True, r.json()
            except Exception:
                return True, {}
        LOG.warning("ingest HTTP %s: %s", r.status_code, r.text[:200])
        return False, {"status": r.status_code, "text": r.text[:200]}
    except Exception as exc:
        LOG.warning("ingest exception: %s", exc)
        return False, {"error": str(exc)}


def _spool(events: list[dict]) -> None:
    SPOOL_DIR.mkdir(parents=True, exist_ok=True)
    p = SPOOL_DIR / f"{int(time.time())}-{uuid.uuid4().hex[:6]}.json"
    p.write_text(json.dumps({"events": events}), encoding="utf-8")
    LOG.info("spooled %d events to %s", len(events), p)


def _drain_spool(url: str, key: str) -> None:
    if not SPOOL_DIR.exists():
        return
    for p in sorted(SPOOL_DIR.glob("*.json")):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            p.unlink(missing_ok=True)
            continue
        events = payload.get("events") or []
        if not events:
            p.unlink(missing_ok=True)
            continue
        ok, _ = _post_batch(url, key, events)
        if ok:
            p.unlink(missing_ok=True)
            LOG.info("drained spool %s (%d events)", p.name, len(events))
        else:
            return  # bail; will retry next tick


def _send(url: str, key: str, events: list[dict]) -> None:
    if not events:
        return
    for i in range(0, len(events), BATCH_LIMIT):
        batch = events[i:i + BATCH_LIMIT]
        ok, body = _post_batch(url, key, batch)
        if ok:
            LOG.info("ingested %d events (accepted=%s deduped=%s)",
                     len(batch), body.get("accepted"), body.get("deduped"))
        else:
            _spool(batch)


def run_once(args) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    cursor = Cursor(CURSOR_FILE)
    _drain_spool(args.ingest_url, args.ingest_key)

    adapter = _load_adapter(args.client, cursor, args.host, args.source)
    events = list(adapter.collect())
    LOG.info("collected %d events from %s", len(events), args.client)
    _send(args.ingest_url, args.ingest_key, events)
    cursor.save()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--client", required=True, choices=["copilot-cli", "openclaw", "hermes"])
    p.add_argument("--ingest-url", required=True,
                   help="Full /api/ingest URL with ?code=<function-key>")
    p.add_argument("--ingest-key", required=True,
                   help="X-Collector-Key value (must match Function App INGEST_KEY)")
    p.add_argument("--host", default=None, help="Override hostname tag in events")
    p.add_argument("--source", default=None,
                   help="Override the source path (logs dir / sessions dir / usage file)")
    p.add_argument("--once", action="store_true", help="Run once and exit (default)")
    p.add_argument("--interval", type=int, default=0,
                   help="If > 0, run a loop sleeping `interval` seconds between ticks")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if a.interval > 0 and not a.once:
        LOG.info("collector loop starting; interval=%ss", a.interval)
        while True:
            try:
                run_once(a)
            except Exception:
                LOG.exception("tick failed")
            time.sleep(a.interval)
    else:
        run_once(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
