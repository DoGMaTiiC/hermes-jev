"""jev-judge — TypeSafe Jev as a Hermes judgment layer.

pre_tool_call gate (shadow by default: judge + log, never interfere; enforce:
escalate triggered calls to the human approval gate) and a `jev_ask` tool so
the model can request typed judgments itself. Every path fails open.
"""

from __future__ import annotations

import logging

from . import gate, schemas, tools
from .jev import JevClient

logger = logging.getLogger(__name__)

_clients: dict[tuple, JevClient] = {}


def _settings(ctx) -> dict:
    return {
        "mode": ctx.get_config("mode", default="shadow"),
        "tools": ctx.get_config("tools", default=["terminal", "write_file", "patch"]),
        "timeout_s": ctx.get_config("timeout_s", default=3.0),
        "cache_seconds": ctx.get_config("cache_seconds", default=120),
        "destructive_threshold": ctx.get_config("destructive_threshold", default=0.90),
        "exfiltration_threshold": ctx.get_config(
            "exfiltration_threshold", default=0.70
        ),
        "impact_threshold": ctx.get_config("impact_threshold", default=2.5),
        "jev_model": ctx.get_config("jev_model", default="typesafe-ai/jev"),
        "jev_base_url": ctx.get_config(
            "jev_base_url", default="https://ai-gateway.vercel.sh/v4/ai"
        ),
        "log_path": ctx.get_config("log_path", default=""),
    }


def _client_for(s: dict) -> JevClient:
    key = (
        s["jev_base_url"],
        s["jev_model"],
        float(s["timeout_s"]),
        int(s["cache_seconds"]),
    )
    if key not in _clients:
        _clients[key] = JevClient(
            base_url=key[0], model=key[1], timeout=key[2], cache_seconds=key[3]
        )
    return _clients[key]


def register(ctx):
    """Wire the gate hook and the jev_ask tool."""

    def on_pre_tool_call(tool_name=None, args=None, task_id=None, **kwargs):
        try:
            s = _settings(ctx)
            if tool_name not in (s["tools"] or []):
                return None
            verdict = gate.judge(_client_for(s), tool_name, args or {}, s)
            if verdict is None:  # fail-open: no key, timeout, 429, transport error
                gate.log_decision(
                    s["log_path"],
                    {
                        "source": "gate",
                        "tool": tool_name,
                        "outcome": "fail_open",
                        "task_id": task_id,
                    },
                )
                return None
            verdict.update(source="gate", mode=s["mode"], task_id=task_id)
            if not verdict["triggered"]:
                verdict["outcome"] = "clear"
                gate.log_decision(s["log_path"], verdict)
                return None
            if s["mode"] == "enforce":
                verdict["outcome"] = "escalated"
                gate.log_decision(s["log_path"], verdict)
                return {
                    "action": "approve",
                    "message": (
                        f"Jev flagged this {tool_name} call ({', '.join(verdict['triggered'])}); "
                        "confirm before it runs."
                    ),
                }
            verdict["outcome"] = "flagged_shadow"
            gate.log_decision(s["log_path"], verdict)
            return None
        except Exception as exc:  # a broken gate must never break a turn
            logger.debug("jev-judge: gate error (fail-open): %s", exc)
            try:
                gate.log_decision(
                    None,
                    {
                        "source": "gate",
                        "tool": tool_name,
                        "outcome": "error",
                        "error": str(exc),
                    },
                )
            except Exception:
                pass
            return None

    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_tool(
        name="jev_ask", toolset="jev", schema=schemas.JEV_ASK, handler=tools.jev_ask
    )
