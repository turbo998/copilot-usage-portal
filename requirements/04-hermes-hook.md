# Hermes Telemetry Hook — Source-Side Patch

> **Status (decided in design):** Hermes does not record token usage in any of its existing logs
> (`~/.hermes/sessions/*.jsonl`, `~/.hermes/profiles/*/logs/*`, `gateway-direct.log`).
> The cleanest workaround is a **2–8 line in-source telemetry hook** added to `hermes-agent`,
> writing one JSONL line per LLM call into `~/.hermes/usage.jsonl`. The collector then tails
> that file, exactly like it does for OpenClaw trajectories.

This document is the patch spec. Apply it once, on `vm-hermes-agent-sg`, in the `hermes-agent`
checkout that the systemd unit runs.

## 1. Locate the Copilot provider call site

The Hermes Copilot provider performs the API call and receives the raw HTTP response. Most
likely candidates (search inside the repo on the VM):

```bash
grep -RIn --include='*.py' --include='*.ts' --include='*.js' \
    -E 'usage|prompt_tokens|completion_tokens|messages\.create|chat\.completions' \
    ~/work/hermes-agent
```

Look for the function that *returns* the assistant message — the response object will have
a `.usage` (Anthropic-style: `input_tokens`, `output_tokens`, `cache_read_input_tokens`,
`cache_creation_input_tokens`) or `.usage` (OpenAI-style: `prompt_tokens`, `completion_tokens`,
`prompt_tokens_details.cached_tokens`, `completion_tokens_details.reasoning_tokens`).

## 2. Add the hook

### Python (most likely shape)

```python
# top of the provider module, once
import json, os, time
from pathlib import Path
_USAGE_LOG = Path(os.path.expanduser("~/.hermes/usage.jsonl"))
_USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)

def _emit_usage(*, session_id: str, message_id: str, model: str, api: str, usage: dict) -> None:
    """Write one line per LLM call. Schema must match collector/adapters/hermes.py."""
    try:
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            "sessionId": session_id,
            "messageId": message_id,
            "model": model,
            "api": api,                  # "openai-chat-completions" | "anthropic-messages"
            "usage": usage,
        }
        with _USAGE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # never let telemetry break the agent
```

Then, **immediately after** receiving the LLM response, add one line:

```python
_emit_usage(
    session_id=current_session_id,
    message_id=resp.id,                                  # whatever the provider exposes
    model=resp.model,
    api="anthropic-messages",                            # or "openai-chat-completions"
    usage=getattr(resp, "usage", None).model_dump() if hasattr(resp, "usage") else {},
)
```

### TypeScript / JavaScript variant

```ts
import { appendFile, mkdir } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join, dirname } from 'node:path';

const USAGE_LOG = join(homedir(), '.hermes', 'usage.jsonl');

async function emitUsage(rec: {
  sessionId: string; messageId: string; model: string;
  api: 'openai-chat-completions' | 'anthropic-messages';
  usage: Record<string, unknown>;
}) {
  try {
    await mkdir(dirname(USAGE_LOG), { recursive: true });
    const line = JSON.stringify({ ts: new Date().toISOString(), ...rec }) + '\n';
    await appendFile(USAGE_LOG, line, 'utf8');
  } catch { /* ignore */ }
}
```

Call site, right after `await client.messages.create(...)` (or `chat.completions.create`):

```ts
await emitUsage({
  sessionId, messageId: resp.id, model: resp.model,
  api: 'anthropic-messages',
  usage: resp.usage as any,
});
```

## 3. Schema expected by the collector

`collector/adapters/hermes.py` reads `~/.hermes/usage.jsonl` and recognises both shapes:

```jsonc
// Anthropic
{
  "ts": "2026-05-07T08:01:23.456Z",
  "sessionId": "abc-123",
  "messageId": "msg_01XYZ...",
  "model": "claude-opus-4.6",
  "api": "anthropic-messages",
  "usage": {
    "input_tokens": 1024,
    "output_tokens": 256,
    "cache_read_input_tokens": 800,
    "cache_creation_input_tokens": 0
  }
}
// OpenAI
{
  "ts": "2026-05-07T08:02:11.000Z",
  "sessionId": "def-456",
  "messageId": "chatcmpl-...",
  "model": "gpt-5-mini-2025-08-07",
  "api": "openai-chat-completions",
  "usage": {
    "prompt_tokens": 1100,
    "completion_tokens": 230,
    "prompt_tokens_details": { "cached_tokens": 880 },
    "completion_tokens_details": { "reasoning_tokens": 0 }
  }
}
```

Either shape is fine — the adapter normalises both to the canonical event schema.

## 4. Verification

```bash
# After making one Hermes request:
tail -1 ~/.hermes/usage.jsonl | jq

# Run collector locally once:
cd /opt/copilot-usage-collector
.venv/bin/python collector.py --client hermes \
  --source $HOME/.hermes/usage.jsonl \
  --ingest-url 'https://func-xxx.azurewebsites.net/ingest?code=…' \
  --ingest-key '<key>' --once -v
```

You should see `collected N events from hermes` and a 200 from `/ingest`.

## 5. Privacy guarantees

- **Never** include prompt or completion *text* in `usage.jsonl`. Only token counts +
  message id + session id.
- File mode is `0600` by default (created by the agent's user).
- Rotate via your existing `.hermes` log rotation policy. The collector keeps a
  cursor (`~/.copilot-usage-collector/cursor.json`) that survives rotation.
