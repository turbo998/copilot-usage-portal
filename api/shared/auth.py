"""Auth helpers — Easy Auth principal validation for /api/metrics/*."""
from __future__ import annotations

import base64
import json
import os

import azure.functions as func


def require_principal(req: func.HttpRequest) -> dict | None:
    """Return the parsed principal or None if missing/invalid.

    SWA Easy Auth injects the header `x-ms-client-principal` (base64 JSON).
    For local dev, allow bypass when `LOCAL_DEV_ALLOW_ANON=1`.
    """
    raw = req.headers.get("x-ms-client-principal")
    if raw:
        try:
            return json.loads(base64.b64decode(raw))
        except Exception:
            return None
    if os.getenv("LOCAL_DEV_ALLOW_ANON") == "1":
        return {"userId": "local-dev", "userDetails": "local"}
    return None


def require_collector_key(req: func.HttpRequest) -> bool:
    expected = os.environ.get("INGEST_KEY", "")
    if not expected:
        return False
    return req.headers.get("x-collector-key", "") == expected
