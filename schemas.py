"""Tool schema — what the LLM sees for jev_ask."""

JEV_ASK = {
    "name": "jev_ask",
    "description": (
        "Ask TypeSafe Jev a typed question about a piece of state and get back probabilities, "
        "not prose. Use it for closed decisions: classify, route, score against a rubric, or check "
        "a yes/no property — when a number with confidence is more useful than a written answer. "
        "Questions are answered in parallel in one request (~1s). Returning probability + confidence "
        "lets you branch with thresholds instead of guessing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "state": {
                "type": "string",
                "description": (
                    "The content to judge: the text, or a JSON object/array serialized as a string "
                    "(logs, records, a message, a diff). Everything Jev sees about the situation."
                ),
            },
            "questions": {
                "type": "string",
                "description": (
                    "JSON object of typed questions. Each key is an id you choose; answers come back "
                    "under the same ids. Question shapes: "
                    '{"type":"boolean","instructions":"Is X true?"} · '
                    '{"type":"choice","instructions":"Which one?","criteria":{"optA":"desc","optB":"desc"}} · '
                    '{"type":"score","instructions":"How severe?","criteria":["low","medium","high"]}. '
                    'Example: {"urgent": {"type":"boolean","instructions":"Does this convey urgency?"}}'
                ),
            },
        },
        "required": ["state", "questions"],
    },
}
