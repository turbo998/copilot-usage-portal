"""OpenClaw adapter — parses ~/.openclaw/agents/main/sessions/*.trajectory.jsonl"""
from __future__ import annotations

import json
import logging
import os
import socket
from pathlib import Path
from typing import Iterable

from cursor import Cursor

log = logging.getLogger(__name__)


def _root() -> Path:
    return Path(os.path.expanduser("~")) / ".openclaw" / "agents" / "main" / "sessions"


def _collect_file(path: Path, cursor: Cursor, host: str) -> list[dict]:
    key = str(path.resolve())
    state = cursor.get(key)
    pos = int(state.get("pos") or 0)
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return []
    if size < pos:
        pos = 0
    if size == pos:
        return []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        f.seek(pos)
        text = f.read()
        new_pos = f.tell()
    out: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") != "model.completed":
            continue
        usage = (ev.get("data") or {}).get("usage") or {}
        if not usage:
            continue
        input_t = int(usage.get("input") or 0)
        output_t = int(usage.get("output") or 0)
        cache_read = int(usage.get("cacheRead") or 0)
        cache_write_tokens = int(usage.get("cacheWrite") or 0)
        # cacheWrite tokens are billed at a dedicated cache_write rate
        # (separate from fresh prompt input).
        prompt_tokens = input_t
        cached_tokens = cache_read
        completion_tokens = output_t
        total_tokens = (
            int(usage.get("total") or 0)
            or (prompt_tokens + cached_tokens + cache_write_tokens + completion_tokens)
        )
        model = ev.get("modelId") or "unknown"
        sid = ev.get("sessionId") or path.stem
        # Each model call is uniquely identified by (sessionId, runId);
        # `seq` resets per source-stream and is NOT unique across the file.
        run_id = ev.get("runId") or ""
        seq = ev.get("seq") or 0
        ev_id = f"{sid}:{run_id}" if run_id else f"{sid}:seq{seq}:{ev.get('ts','')}"
        out.append({
            "id": ev_id,
            "client": "openclaw",
            "host": host,
            "ts": ev.get("ts") or "",
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": cached_tokens,
            "cache_write_tokens": cache_write_tokens,
            "reasoning_tokens": 0,
            "total_tokens": total_tokens,
            "session_id": sid,
        })
    cursor.set(key, new_pos, path.stat().st_mtime)
    return out


class OpenClawAdapter:
    name = "openclaw"

    def __init__(self, cursor: Cursor, host: str | None = None,
                 sessions_dir: str | None = None) -> None:
        self._cursor = cursor
        self._host = host or socket.gethostname()
        self._dir = Path(sessions_dir) if sessions_dir else _root()

    def collect(self) -> Iterable[dict]:
        if not self._dir.exists():
            log.info("OpenClaw sessions dir not found: %s", self._dir)
            return []
        events: list[dict] = []
        for p in sorted(self._dir.glob("*.trajectory.jsonl")):
            events.extend(_collect_file(p, self._cursor, self._host))
        return events
