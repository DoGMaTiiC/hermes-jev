"""jev_ask handler — lets the model ask for a typed judgment itself."""

from __future__ import annotations

import json

from .gate import log_decision
from .jev import JevClient


def jev_ask(args: dict, **kwargs) -> str:
    """Return JSON string: answers + confidence, or a fail-open note. Never raises."""
    state = args.get("state", "")
    raw_questions = args.get("questions", {})
    if isinstance(raw_questions, str):
        try:
            questions = json.loads(raw_questions)
        except json.JSONDecodeError as exc:
            return json.dumps({"error": f"questions is not valid JSON: {exc}"})
    else:
        questions = raw_questions
    if not isinstance(questions, dict) or not questions:
        return json.dumps({"error": "questions must be a non-empty JSON object"})

    client = JevClient()
    result = client.evaluate(state, questions)
    if not result:
        log_decision(None, {"source": "ask", "outcome": "fail_open"})
        return json.dumps(
            {
                "error": "Jev unavailable (no key, timeout, or transport error)",
                "note": "fail-open: proceed without this judgment",
            }
        )

    log_decision(
        None,
        {
            "source": "ask",
            "outcome": "ok",
            "questions": list(questions.keys()),
            "latency_ms": result.get("latency_ms"),
            "cost": result.get("cost"),
        },
    )
    return json.dumps(
        {
            "answers": result["answers"],
            "confidence": result.get("confidence", {}),
            "cost_usd": result.get("cost"),
        },
        ensure_ascii=False,
    )
