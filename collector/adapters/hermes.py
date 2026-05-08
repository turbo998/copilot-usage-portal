"""Hermes adapter — reads token usage from ~/.hermes/state.db (sqlite).

Hermes-agent persists per-session token counts in state.db (`sessions` table).
We emit one event per ENDED session whose billing_provider is 'copilot'.
Cursor watermark is the max `ended_at` epoch seen so subsequent runs only emit
new completions.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import socket
import sqlite3
from pathlib import Path
from typing import Iterable

from cursor import Cursor

log = logging.getLogger(__name__)

DEFAULT_PATH = Path(os.path.expanduser("~")) / ".hermes" / "state.db"


def _iso(epoch: float | None) -> str:
    if not epoch:
        return ""
    return _dt.datetime.fromtimestamp(float(epoch), tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")


class HermesAdapter:
    name = "hermes"

    def __init__(self, cursor: Cursor, host: str | None = None,
                 usage_file: str | None = None) -> None:
        self._cursor = cursor
        self._host = host or socket.gethostname()
        self._db = Path(usage_file) if usage_file else DEFAULT_PATH

    def collect(self) -> Iterable[dict]:
        if not self._db.exists():
            log.info("Hermes state.db not found: %s", self._db)
            return []

        key = f"hermes-statedb:{self._db.resolve()}"
        state = self._cursor.get(key)
        watermark = float(state.get("pos") or 0)

        # Open read-only via URI so we don't fight an active hermes writer.
        uri = f"file:{self._db}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """
                SELECT id, source, model, billing_provider,
                       started_at, ended_at,
                       COALESCE(ended_at, started_at) AS effective_ts,
                       input_tokens, output_tokens,
                       cache_read_tokens, cache_write_tokens,
                       reasoning_tokens,
                       estimated_cost_usd
                FROM sessions
                WHERE billing_provider = 'copilot'
                  AND (input_tokens + output_tokens + cache_read_tokens
                       + cache_write_tokens + reasoning_tokens) > 0
                  AND COALESCE(ended_at, started_at) > ?
                  AND COALESCE(ended_at, started_at)
                      < (strftime('%s','now') - 300)
                ORDER BY COALESCE(ended_at, started_at) ASC
                LIMIT 5000
                """,
                (watermark,),
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        events: list[dict] = []
        max_seen = watermark
        for r in rows:
            input_t = int(r["input_tokens"] or 0)
            output_t = int(r["output_tokens"] or 0)
            cache_r = int(r["cache_read_tokens"] or 0)
            cache_w = int(r["cache_write_tokens"] or 0)
            reason = int(r["reasoning_tokens"] or 0)
            # Match the canonical ingest schema (see api/function_app.py):
            # prompt_tokens = fresh prompt input;
            # cached_tokens = cache reads (cheap); cache_write counts toward prompt.
            prompt_tokens = input_t + cache_w
            cached_tokens = cache_r
            completion_tokens = output_t
            reasoning_tokens = reason
            total_tokens = prompt_tokens + cached_tokens + completion_tokens + reasoning_tokens

            events.append({
                "id": str(r["id"]),
                "client": "hermes",
                "host": self._host,
                "ts": _iso(r["effective_ts"]),
                "model": r["model"] or "unknown",
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cached_tokens": cached_tokens,
                "reasoning_tokens": reasoning_tokens,
                "total_tokens": total_tokens,
                "session_id": str(r["id"]),
                "source": r["source"] or "",
            })
            eff_ts = r["effective_ts"]
            if eff_ts and eff_ts > max_seen:
                max_seen = float(eff_ts)

        if max_seen != watermark:
            self._cursor.set(key, int(max_seen), max_seen)
        return events
