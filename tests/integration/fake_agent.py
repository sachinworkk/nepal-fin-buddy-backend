"""A scripted Strands model standing in for OpenRouter in the chat agent."""

import json

from strands.models.model import Model


def flatten(messages: list[dict], system_prompt: str | None) -> list[dict]:
    """Strands messages as simple {"role", "content"} dicts (tool blocks as JSON text)."""
    out = [{"role": "system", "content": system_prompt or ""}]
    for m in messages:
        parts = []
        for block in m["content"]:
            if "text" in block:
                parts.append(block["text"])
            elif "toolUse" in block:
                parts.append(json.dumps({"tool_use": block["toolUse"]["name"],
                                         "input": block["toolUse"]["input"]}))
            elif "toolResult" in block:
                parts.append(" ".join(c.get("text", "") for c in block["toolResult"]["content"]))
        out.append({"role": m["role"], "content": "\n".join(parts)})
    return out


class FakeAgentModel(Model):
    """Each model call takes the next script step: {"text": ...} answers, {"tools": [(name,
    args), ...]} requests tool calls. A callable step gets the flattened messages and returns
    one of those. With the script used up it answers `default`; "{last_tool}" in a text step is
    replaced with the latest tool result."""

    def __init__(self, script=None, default="The rent bill was Rs. 20,000.", calls=None):
        self.script = list(script or [])
        self.default = default
        self.calls = calls if calls is not None else []
        self._n = 0

    def update_config(self, **kw):
        pass

    def get_config(self):
        return {"model_id": "fake"}

    def structured_output(self, *a, **kw):
        raise NotImplementedError

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kw):
        flat = flatten(messages, system_prompt)
        self.calls.append(flat)
        step = self.script.pop(0) if self.script else {"text": self.default}
        if callable(step):
            step = step(flat)
        yield {"messageStart": {"role": "assistant"}}
        if "tools" in step:
            for name, args in step["tools"]:
                self._n += 1
                yield {"contentBlockStart": {"start": {"toolUse": {
                    "toolUseId": f"call{self._n}", "name": name}}}}
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(args)}}}}
                yield {"contentBlockStop": {}}
            stop = "tool_use"
        else:
            last_tool = next((m["content"] for m in reversed(flat[1:])
                              if m["role"] == "user"), "")
            yield {"contentBlockDelta": {"delta": {"text": step["text"].replace(
                "{last_tool}", last_tool)}}}
            yield {"contentBlockStop": {}}
            stop = "end_turn"
        yield {"messageStop": {"stopReason": stop}}
        yield {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
                            "metrics": {"latencyMs": 1}}}
