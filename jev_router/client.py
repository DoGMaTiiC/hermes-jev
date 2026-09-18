"""Jev client — TypeSafe System One via the Vercel AI Gateway evaluation endpoint.

Pure stdlib: POSTs {state, questions} to {base_url}/evaluation-model and returns
typed answers with probabilities. Every failure path returns None (fail-open).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://ai-gateway.vercel.sh/v4/ai"
DEFAULT_MODEL = "typesafe-ai/jev"
PROTOCOL_VERSION = "0.0.1"
SPEC_VERSION = "4"


def api_key() -> str:
    """The gateway key from the environment, falling back to <HERMES_HOME>/.env."""
    key = os.environ.get("AI_GATEWAY_API_KEY", "").strip()
    if key:
        return key
    env_file = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / ".env"
    try:
        m = re.search(
            r'^AI_GATEWAY_API_KEY=["\']?([^"\'\n]+)', env_file.read_text(), re.MULTILINE
        )
        return m.group(1).strip() if m else ""
    except OSError:
        return ""


class JevClient:
    """One call = one request = typed answers (boolean / choice / score)."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = 4.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = float(timeout)
        self._cache: dict[str, dict] = {}

    def evaluate(self, state, questions: dict) -> dict | None:
        """Return {"answers", "confidence", "cost", "latency_ms"} or None (fail-open)."""
        key = api_key()
        if not key:
            return None

        cache_key = hashlib.sha256(
            json.dumps(
                [state, questions], sort_keys=True, ensure_ascii=False, default=str
            ).encode()
        ).hexdigest()
        if cache_key in self._cache:
            return self._cache[cache_key]

        payload = json.dumps(
            {"state": state, "questions": questions}, ensure_ascii=False, default=str
        ).encode()
        req = urllib.request.Request(
            f"{self.base_url}/evaluation-model",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {key}",
                "ai-gateway-protocol-version": PROTOCOL_VERSION,
                "ai-evaluation-model-specification-version": SPEC_VERSION,
                "ai-model-id": self.model,
                "content-type": "application/json",
            },
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read())
        except Exception as exc:  # timeout, 429, 5xx, network, parse — always fail-open
            logger.debug(
                "jev-skill-router: evaluate failed (%s): %s", type(exc).__name__, exc
            )
            return None

        result = {
            "answers": body.get("answers", {}),
            "confidence": (
                body.get("providerMetadata", {}).get("typesafe", {}) or {}
            ).get("confidence", {}),
            "cost": (body.get("providerMetadata", {}).get("gateway", {}) or {}).get(
                "cost"
            ),
            "usage": body.get("usage"),
            "latency_ms": round((time.monotonic() - started) * 1000),
        }
        self._cache[cache_key] = result
        return result
