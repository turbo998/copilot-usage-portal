"""Hermes adapter — reads token usage from Hermes sqlite state DBs.

Hermes-agent persists per-session token counts in `sessions` tables. In a
multi-profile setup the rows are spread across:
  * the legacy global  ~/.hermes/state.db
  * one DB per profile ~/.hermes/profiles/<name>/state.db

Newer models (e.g. claude-opus-4.8) are recorded only in the per-profile DBs,
so we must discover and read EVERY state.db, not just the global one. Each DB
keeps an independent watermark (max `ended_at` epoch seen) so subsequent runs
only emit new completions. Session ids are globally unique, so merged events
never collide.
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

def _default_home() -> Path:
    """Resolve the Hermes home dir.

    Honour an explicit HERMES_HOME env var first (robust when the collector runs
    under a sandboxed HOME), else fall back to ~/.hermes.
    """
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    return Path(os.path.expanduser("~")) / ".hermes"


HERMES_HOME = _default_home()
DEFAULT_PATH = HERMES_HOME / "state.db"

_QUERY = """
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
      AND COALESCE(ended_at, started_at) < (strftime('%s','now') - 300)
    ORDER BY COALESCE(ended_at, started_at) ASC
    LIMIT 5000
"""


def _iso(epoch: float | None) -> str:
    if not epoch:
        return ""
    return _dt.datetime.fromtimestamp(float(epoch), tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")


class HermesAdapter:
    name = "hermes"

    def __init__(self, cursor: Cursor, host: str | None = None,
                 usage_file: str | None = None,
                 hermes_home: str | Path | None = None) -> None:
        self._cursor = cursor
        self._host = host or socket.gethostname()
        # Explicit single-DB mode (legacy callers / tests) takes precedence.
        self._explicit_db = Path(usage_file) if usage_file else None
        self._home = Path(hermes_home) if hermes_home else _default_home()

    def _discover_dbs(self) -> list[Path]:
        """Return every Hermes state.db: global root + each profile, de-duped."""
        if self._explicit_db is not None:
            return [self._explicit_db] if self._explicit_db.exists() else []
        seen: dict[Path, None] = {}
        candidates = [self._home / "state.db"]
        profiles_dir = self._home / "profiles"
        if profiles_dir.is_dir():
            for child in sorted(profiles_dir.iterdir()):
                if child.is_dir():
                    candidates.append(child / "state.db")
        for db in candidates:
            try:
                if db.exists():
                    seen[db.resolve()] = None
            except OSError:
                continue
        return list(seen.keys())

    def _collect_one(self, db: Path) -> list[dict]:
        key = f"hermes-statedb:{db.resolve()}"
        state = self._cursor.get(key)
        watermark = float(state.get("pos") or 0)

        uri = f"file:{db}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=30)
        except sqlite3.Error as exc:
            log.warning("Hermes: cannot open %s: %s", db, exc)
            return []
        try:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(_QUERY, (watermark,)).fetchall()
            except sqlite3.Error as exc:
                # A DB without the expected `sessions` schema is skipped, not fatal.
                log.warning("Hermes: query failed on %s: %s", db, exc)
                return []
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
            prompt_tokens = input_t
            cached_tokens = cache_r
            cache_write_tokens = cache_w
            completion_tokens = output_t
            reasoning_tokens = reason
            total_tokens = (prompt_tokens + cached_tokens + cache_write_tokens
                            + completion_tokens + reasoning_tokens)

            events.append({
                "id": str(r["id"]),
                "client": "hermes",
                "host": self._host,
                "ts": _iso(r["effective_ts"]),
                "model": r["model"] or "unknown",
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cached_tokens": cached_tokens,
                "cache_write_tokens": cache_write_tokens,
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

    def collect(self) -> Iterable[dict]:
        dbs = self._discover_dbs()
        if not dbs:
            log.info("Hermes: no state.db found under %s", self._home)
            return []
        events: list[dict] = []
        for db in dbs:
            events.extend(self._collect_one(db))
        return events
