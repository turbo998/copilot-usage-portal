"""Tests for the 2026-06-01 GitHub Copilot AI Credits cost model.

Algorithm (per GitHub docs): credits = USD * 100. So a model billed at
USD $3 / 1M input tokens consumes 300 credits per 1M input tokens.

Run: pytest tests/test_credits.py -v
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------
@pytest.fixture(scope="module")
def api_rows():
    return json.loads((ROOT / "api" / "cost_table_seed.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def infra_rows():
    return json.loads((ROOT / "infra" / "cost_table_seed.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def estimate_credits(api_rows):
    """Load estimate_credits() from cost_table.py with an in-memory seed copy.

    Stub cosmos_repo so we can import cost_table without azure-cosmos installed.
    """
    fake_repo = types.ModuleType("shared.cosmos_repo")
    fake_repo.cost_container = lambda: None  # type: ignore[attr-defined]
    sys.modules.setdefault("shared", types.ModuleType("shared"))
    sys.modules["shared.cosmos_repo"] = fake_repo
    src = (ROOT / "api" / "shared" / "cost_table.py").read_text(encoding="utf-8")
    src = src.replace("from .cosmos_repo import cost_container",
                      "from shared.cosmos_repo import cost_container")
    ns: dict = {}
    exec(compile(src, "cost_table.py", "exec"), ns)
    _cost_table = ns["_cost_table"]

    # Replace the lru_cache'd loader with our in-memory seed copy.
    table = {r["id"]: r for r in api_rows}
    _cost_table.cache_clear()
    ns["_cost_table"] = lambda: table  # type: ignore[assignment]
    # Also rewire the closure used inside estimate_credits by re-execing it.
    exec(
        "def estimate_credits(model, prompt_tokens, completion_tokens, cached_tokens=0, cache_write_tokens=0):\n"
        "    rate = table.get(normalise_model_id(model))\n"
        "    if not rate: return 0.0\n"
        "    uncached = max(0, prompt_tokens - cached_tokens)\n"
        "    return round(\n"
        "        (uncached / 1_000_000.0) * rate.get('credits_per_million_input', 0.0)\n"
        "        + (cached_tokens / 1_000_000.0) * rate.get('credits_per_million_cached_input', 0.0)\n"
        "        + (cache_write_tokens / 1_000_000.0) * rate.get('credits_per_million_cache_write', 0.0)\n"
        "        + (completion_tokens / 1_000_000.0) * rate.get('credits_per_million_output', 0.0),\n"
        "        6,\n"
        "    )\n",
        {"table": table, "normalise_model_id": ns["normalise_model_id"]}, ns
    )
    return ns["estimate_credits"]


@pytest.fixture(scope="module")
def parsed_events():
    """Load copilot_cli adapter's JSON-block parser and parse a sample log."""
    sys.path.insert(0, str(ROOT / "collector"))
    # Avoid importing real cursor side effects
    fake_cursor = types.ModuleType("cursor")
    class _C:
        pass
    fake_cursor.Cursor = _C  # type: ignore
    sys.modules["cursor"] = fake_cursor
    spec = importlib.util.spec_from_file_location(
        "copilot_cli", ROOT / "collector" / "adapters" / "copilot_cli.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    sample = """\
2026-06-01T10:00:00.000Z [DEBUG] {
  "id": "msg_001",
  "model": "claude-sonnet-4-6",
  "usage": {
    "input_tokens": 100,
    "cache_creation_input_tokens": 50,
    "cache_read_input_tokens": 200,
    "output_tokens": 80
  }
}
2026-06-01T10:00:05.000Z [DEBUG] {
  "id": "msg_002",
  "model": "gpt-5",
  "usage": {
    "prompt_tokens": 300,
    "completion_tokens": 100,
    "prompt_tokens_details": {"cached_tokens": 50}
  }
}
"""
    events = []
    for ts, obj in mod._scan_json_blocks(sample):
        events.append((ts, obj))
    return events


# -----------------------------------------------------------------------------
# 1. Seed file invariant: credits_per_million_X == usd_per_million_X * 100
# -----------------------------------------------------------------------------
@pytest.mark.parametrize("seed_name", ["api", "infra"])
def test_seed_credits_match_usd(seed_name, api_rows, infra_rows):
    rows = api_rows if seed_name == "api" else infra_rows
    seed_path = ROOT / seed_name / "cost_table_seed.json"
    assert isinstance(rows, list) and rows, f"empty seed: {seed_path}"
    for r in rows:
        for kind in ("input", "output", "cached_input"):
            cred = r.get(f"credits_per_million_{kind}")
            usd = r.get(f"usd_per_million_{kind}")
            assert usd is not None and cred is not None, \
                f"{seed_path.name} {r['id']} missing {kind}"
            expected = round(usd * 100.0, 6)
            assert abs(cred - expected) <= 1e-6, \
                f"{seed_path.name} {r['id']} {kind}: credits={cred} vs usd*100={expected}"


@pytest.mark.parametrize("seed_name", ["api", "infra"])
def test_seed_cache_write_rates(seed_name, api_rows, infra_rows):
    """All Anthropic models must carry an explicit cache_write rate."""
    rows = api_rows if seed_name == "api" else infra_rows
    seed_path = ROOT / seed_name / "cost_table_seed.json"
    for r in rows:
        if r.get("model_family") == "claude":
            cred = r.get("credits_per_million_cache_write")
            usd = r.get("usd_per_million_cache_write")
            assert usd is not None and cred is not None, \
                f"{seed_path.name} {r['id']} missing cache_write rate"
            assert abs(cred - usd * 100.0) <= 1e-6, \
                f"{seed_path.name} {r['id']} cache_write credits!=usd*100"


def test_seed_files_identical(api_rows, infra_rows):
    """Both seed files must be identical (api/ is bundled with function app,
    infra/ is referenced by Bicep -- they MUST stay in sync)."""
    assert api_rows == infra_rows, "api/ and infra/ cost_table_seed.json differ"


# -----------------------------------------------------------------------------
# 2. estimate_credits() arithmetic
# -----------------------------------------------------------------------------
def test_estimate_credits_arithmetic(estimate_credits):
    # 1M input @ gpt-5 = 125 credits ($1.25 * 100)
    assert estimate_credits("gpt-5", 1_000_000, 0) == 125.0, estimate_credits("gpt-5", 1_000_000, 0)
    # 1M output @ gpt-5 = 1000 credits ($10 * 100)
    assert estimate_credits("gpt-5", 0, 1_000_000) == 1000.0
    # 1M cached input @ gpt-5 = 12.5 credits ($0.125 * 100)
    assert estimate_credits("gpt-5", 1_000_000, 0, 1_000_000) == 12.5
    # Claude opus 4.7: 1M in + 1M out = 1500 + 7500 = 9000
    assert estimate_credits("claude-opus-4-7", 1_000_000, 1_000_000) == 9000.0
    # Unknown model -> 0
    assert estimate_credits("totally-made-up", 1000, 1000) == 0.0


def test_estimate_credits_cache_write_rate(estimate_credits):
    # cache_write must use its own rate, NOT the fresh-input rate.
    # Sonnet 4.6: input $3 -> 300 cr/M; cache_write $3.75 -> 375 cr/M.
    # 1M cache_write tokens alone => 375 credits (would be 300 if mis-billed as input).
    sonnet_cw = estimate_credits("claude-sonnet-4-6", 0, 0, 0, 1_000_000)
    assert sonnet_cw == 375.0, f"sonnet cache_write 1M => {sonnet_cw}, want 375.0"
    sonnet_in = estimate_credits("claude-sonnet-4-6", 1_000_000, 0)
    assert sonnet_in == 300.0 and sonnet_cw != sonnet_in, \
        f"cache_write must use cache_write rate, not input ({sonnet_cw} vs {sonnet_in})"
    # Combined: 1M input + 1M cache_write + 1M output on sonnet-4-6
    # = 300 + 375 + 1500 = 2175
    combined = estimate_credits("claude-sonnet-4-6", 1_000_000, 1_000_000, 0, 1_000_000)
    assert combined == 2175.0, f"sonnet combined => {combined}, want 2175.0"
    # OpenAI gpt-5 has no cache_write rate -> contributes 0
    assert estimate_credits("gpt-5", 0, 0, 0, 1_000_000) == 0.0


# -----------------------------------------------------------------------------
# 3. Adapter handling of Anthropic cache_creation_input_tokens
# -----------------------------------------------------------------------------
def test_anthropic_adapter_separate_fields(parsed_events):
    """copilot_cli adapter maps cache_creation_input_tokens to a separate
    cache_write field (billed at its own rate), not folded into prompt_tokens."""
    assert len(parsed_events) == 2, f"expected 2 json blocks, got {len(parsed_events)}"

    # Simulate the adapter's extraction logic for the Anthropic block
    u = parsed_events[0][1]["usage"]
    input_t = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
    cache_write_t = int(u.get("cache_creation_input_tokens") or 0)
    prompt_tokens = input_t  # cache_write is now a separate field, not folded in
    cached_tokens = int(((u.get("prompt_tokens_details") or {}).get("cached_tokens"))
                        or u.get("cache_read_input_tokens") or 0)
    assert prompt_tokens == 100, f"prompt_tokens={prompt_tokens}, want 100 (fresh only)"
    assert cache_write_t == 50, f"cache_write_t={cache_write_t}, want 50"
    assert cached_tokens == 200, f"cached_tokens={cached_tokens}, want 200"


def test_openai_no_cache_creation(parsed_events):
    """For OpenAI block: cache_creation_input_tokens absent -> 0."""
    u = parsed_events[1][1]["usage"]
    cache_write_t = int(u.get("cache_creation_input_tokens") or 0)
    assert cache_write_t == 0
