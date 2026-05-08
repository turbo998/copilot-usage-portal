"""Cost table — loaded once per worker, looked up by model id."""
from __future__ import annotations

import logging
from functools import lru_cache

from .cosmos_repo import cost_container

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _cost_table() -> dict[str, dict]:
    """Load all rate cards into memory. Cosmos cost_container is tiny (<50 docs)."""
    try:
        items = list(cost_container().read_all_items())
    except Exception:  # pragma: no cover
        log.exception("Failed to load cost table from Cosmos")
        return {}
    return {it["id"]: it for it in items}


def normalise_model_id(model: str) -> str:
    """Normalise a raw model id from any client into a stable cost-table key.

    Handles:
      * GitHub Copilot provider prefix ("github-copilot/...").
      * Microsoft internal compute routing prefix ("capi-noe-ptuc-h200-ib-...").
      * Trailing date stamps: "...-2025-08-07" or "...-20251001".
      * Dotted-version variants: "gpt-5.4", "claude-opus-4.6".
      * 1M-context variants: "claude-opus-4.6-1m".
    """
    if not model:
        return "unknown"
    m = model.lower().strip()
    if m.startswith("github-copilot/"):
        m = m[len("github-copilot/"):]
    # Microsoft internal compute routing wrapper: keep only from the last known model marker
    if m.startswith("capi-"):
        for marker in ("gpt-", "claude-", "o1-", "o3-", "o4-"):
            i = m.find(marker)
            if i != -1:
                m = m[i:]
                break
    parts = m.split("-")
    # Locate a year-like segment whose tail is entirely digits, then drop it + everything after.
    year_idx: int | None = None
    for i, p in enumerate(parts):
        if not p.isdigit() or i == 0:
            continue
        is_year = (len(p) == 4 and 2000 <= int(p) <= 2099)
        is_yyyymmdd = (len(p) == 8 and 20000000 <= int(p) <= 20991231)
        if (is_year or is_yyyymmdd) and all(q.isdigit() for q in parts[i + 1:]):
            year_idx = i
            break
    if year_idx is not None:
        parts = parts[:year_idx]
    m = "-".join(parts)
    aliases = {
        "claude-opus-4.6": "claude-opus-4-6",
        "claude-opus-4.7": "claude-opus-4-7",
        "claude-opus-4.6-1m": "claude-opus-4-6-1m",
        "claude-opus-4.7-1m": "claude-opus-4-7-1m",
        "claude-sonnet-4.5": "claude-sonnet-4-5",
        "claude-sonnet-4.6": "claude-sonnet-4-6",
        "claude-haiku-4.5": "claude-haiku-4-5",
        "gpt-4.1": "gpt-4-1",
        "gpt-5.3-codex": "gpt-5-codex",
        "gpt-5.4": "gpt-5",
        "gpt-5.4-mini": "gpt-5-mini",
        "gpt-5.4-nano": "gpt-5-nano",
        "gpt-5.5": "gpt-5",
    }
    return aliases.get(m, m)


def model_family(model: str) -> str:
    nm = normalise_model_id(model)
    if nm.startswith("claude"):
        return "claude"
    if nm.startswith(("gpt", "o1", "o3", "o4")):
        return "gpt"
    return "other"


def estimate_credits(model: str, prompt_tokens: int, completion_tokens: int,
                     cached_tokens: int = 0) -> float:
    """Return AI Credits estimate. Falls back to 0 if model unknown."""
    table = _cost_table()
    rate = table.get(normalise_model_id(model))
    if not rate:
        return 0.0
    uncached = max(0, prompt_tokens - cached_tokens)
    return round(
        (uncached / 1_000_000.0) * rate.get("credits_per_million_input", 0.0)
        + (cached_tokens / 1_000_000.0) * rate.get("credits_per_million_cached_input", 0.0)
        + (completion_tokens / 1_000_000.0) * rate.get("credits_per_million_output", 0.0),
        6,
    )
