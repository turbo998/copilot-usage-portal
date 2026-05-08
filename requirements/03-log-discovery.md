# 03 — Log Source Discovery (verified 2026-05-07)

This document is the **authoritative ground truth** for what each client logs. The cloud-architect / data-architect / app-developer agents must trust these findings rather than re-investigating.

## Copilot CLI (local Windows machine)

- **Log path**: `%USERPROFILE%\.copilot\logs\process-*.log`
- **Format**: text logs with embedded multi-line JSON blocks (response bodies)
- **Volume**: ~10 KB to ~1 MB per CLI process; new file per process
- **Rotation**: none — files persist; collector must use byte-cursor

### Available fields per LLM call

| Field | JSON path | Type | Example |
|-------|-----------|------|---------|
| Model | top-level `model` | string | `gpt-5-mini-2025-08-07`, `claude-opus-4.6` |
| Prompt tokens | `usage.prompt_tokens` | int | 153 |
| Completion tokens | `usage.completion_tokens` | int | 101 |
| Total tokens | `usage.total_tokens` | int | 254 |
| Cached prompt tokens | `usage.prompt_tokens_details.cached_tokens` | int | 0 |
| Reasoning tokens | `usage.completion_tokens_details.reasoning_tokens` | int | 64 |
| Message ID | top-level `id` | string | `chatcmpl-...` or `msg_015...` |
| Timestamp | line prefix | ISO 8601 | `2026-02-12T10:19:53.534Z` |

### Parser strategy
- Scan log for occurrences of `[DEBUG] data:` or directly find `"usage": {`.
- For each match, locate the surrounding JSON object boundaries (`{` to matching `}`).
- Parse, extract the fields above, and emit one event per match.
- Maintain byte-offset cursor in `~/.copilot-usage-collector/cursor.json`.

## OpenClaw (openclaw-sg-vm — southeastasia, OPENCLAW-RG)

- **Log path**: `/home/azureuser/.openclaw/agents/main/sessions/*.trajectory.jsonl`
- **Format**: pure JSON Lines, one event per line
- **Schema**: `traceSchema: "openclaw-trajectory", schemaVersion: 1`
- **Volume**: 177 session files at time of probe; each session has ~10–50 events
- **Filter**: only events with `type == "model.completed"` are needed for usage tracking

### Sample `model.completed` event (verified)

```json
{
  "traceSchema": "openclaw-trajectory",
  "schemaVersion": 1,
  "type": "model.completed",
  "ts": "2026-05-07T06:00:06.076Z",
  "seq": 5,
  "sessionId": "ba9b2f73-34f4-4530-b825-ca186949adc5",
  "runId": "b9a337bf-0bc0-42a3-a2f4-0636dc5d95c6",
  "provider": "github-copilot",
  "modelId": "claude-opus-4-6",
  "modelApi": "anthropic-messages",
  "data": {
    "usage": {
      "input": 3,
      "output": 28,
      "cacheRead": 0,
      "cacheWrite": 18922,
      "total": 18953
    }
  }
}
```

### Field mapping (OpenClaw → unified schema)

| Unified field | Source path |
|---|---|
| `model` | `modelId` (prefix `github-copilot/` if `provider == "github-copilot"`) |
| `prompt_tokens` | `data.usage.input` |
| `completion_tokens` | `data.usage.output` |
| `cached_tokens` | `data.usage.cacheRead` (cacheWrite is one-time write cost) |
| `total_tokens` | `data.usage.total` |
| `session_id` | `sessionId` |
| `id` | `sessionId + ":" + seq` (deduplication key) |
| `ts` | `ts` |

## Hermes (vm-hermes-agent-sg — southeastasia, RG-HERMES-AGENT-SG)

- **Status**: ⚠ **Hermes does NOT currently log token usage**. Native session logs (`~/.hermes/sessions/*.jsonl`) only contain user/assistant/tool messages with no `usage` field.
- **Mitigation (DECIDED)**: Add a small instrumentation hook in the Hermes source code at the place where it dispatches calls to GitHub Copilot, writing one JSON line per call to `~/.hermes/usage.jsonl`. Hermes source is checked out at `/home/azureuser/.hermes/hermes-agent/`.

### Required `~/.hermes/usage.jsonl` line format

The Hermes hook MUST emit lines in this shape (so the collector can use the same parser as OpenClaw with minor adapter):

```json
{
  "ts": "2026-05-07T17:39:00.123Z",
  "session_id": "<hermes-session-id>",
  "request_id": "<unique per call>",
  "provider": "github-copilot",
  "model": "claude-opus-4.7",
  "prompt_tokens": 0,
  "completion_tokens": 0,
  "cached_tokens": 0,
  "total_tokens": 0
}
```

The instrumentation hook design must be defined by the application-architect / python-developer agent. The plan is to identify the Copilot client constructor in the `hermes_cli` / `agent` / `gateway` module and wrap its response handling.

## Per-client deployment of collector

| Client | OS | Scheduling | Path |
|---|---|---|---|
| copilot-cli | Windows 11 ARM64 | Task Scheduler (every 5 min) | `%USERPROFILE%\.copilot-usage-collector\collector.py` |
| openclaw | Ubuntu 22.04 (VM) | systemd timer (5 min) | `/opt/copilot-usage-collector/collector.py` |
| hermes | Ubuntu 22.04 (VM) | systemd timer (5 min) | `/opt/copilot-usage-collector/collector.py` |