"""The litellm DeepSeek callback must make any tool-call history acceptable to DeepSeek.

WHY THIS EXISTS (2026-10-12):

Two parallel local agent sessions on the single-slot LM Studio server on mbp-mda: one
session's request was dropped (the server disconnected), litellm fell back from the
`*-nothink` local door to `cn-deepseek-pro`, and the fallback died too:

    400 The `reasoning_content` in the thinking mode must be passed back to the API.

28 such failures on POST /v1/messages in the 24 h to 2026-10-12. The session stopped
instead of continuing on DeepSeek. Cause: a `*-nothink` door (reasoning_effort none)
writes assistant `tool_use` with no thinking, and DeepSeek V4 requires reasoning on
every tool call of the turn still in progress. Measured against the live endpoint:

- an assistant tool call with no thinking, or a BLANK thinking block, is rejected (400);
- any `redacted_thinking` block, on any assistant turn, is rejected (422 "Failed to
  deserialize"), even with data;
- a thinking block / `reasoning_content` holding the fixed placeholder text is accepted
  with thinking enabled, disabled, adaptive or unset, on open AND closed turns.

The fix lives in `custom_callbacks.py`, a key of `litellm/litellm-cm.yml`, and it is
easy to break silently: litellm hands the hook a SHALLOW copy of kwargs, and the router
reuses the same `messages` list for every fallback hop, so editing a message in place
would leak DeepSeek's placeholder into the request a later non-DeepSeek hop sends. This
test therefore asserts the caller's messages are byte-for-byte untouched as well as
that the output is filled.

CI has no litellm, so `litellm.integrations.custom_logger` is stubbed with a bare base
class; the module needs nothing else from litellm. Its own docstring has the full story.
"""

import asyncio
import copy
import pathlib
import sys
import types
import unittest

import yaml

CONFIG = pathlib.Path(__file__).resolve().parent.parent / "litellm" / "litellm-cm.yml"
PLACEHOLDER = "(reasoning not recorded)"


def load_module() -> types.ModuleType:
    """Exec the ConfigMap's custom_callbacks.py against a stubbed litellm."""
    source = yaml.safe_load(CONFIG.read_text())["data"]["custom_callbacks.py"]
    stub = types.ModuleType("litellm.integrations.custom_logger")
    stub.CustomLogger = type("CustomLogger", (), {})
    saved = {k: sys.modules.get(k) for k in
             ("litellm", "litellm.integrations", "litellm.integrations.custom_logger")}
    sys.modules.update({"litellm": types.ModuleType("litellm"),
                        "litellm.integrations": types.ModuleType("litellm.integrations"),
                        "litellm.integrations.custom_logger": stub})
    try:
        module = types.ModuleType("custom_callbacks")
        exec(compile(source, "custom_callbacks.py", "exec"), module.__dict__)
        return module
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def ant_call(i, pre=()):
    return {"role": "assistant",
            "content": list(pre) + [{"type": "tool_use", "id": f"toolu_{i}", "name": "get_time", "input": {}}]}


def ant_result(i):
    return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"toolu_{i}", "content": "12:00"}]}


def oai_call(i, **extra):
    return {"role": "assistant", "content": None, **extra,
            "tool_calls": [{"id": f"call_{i}", "type": "function",
                            "function": {"name": "get_time", "arguments": "{}"}}]}


REAL = {"type": "thinking", "thinking": "I should call the tool.", "signature": "sig-1"}
REDACTED = {"type": "redacted_thinking", "data": "EmwKAhgBEgy3va3pzix"}
USER = {"role": "user", "content": "What time is it?"}

# (name, messages, expected reasoning per assistant tool call, in order: "placeholder"
# or the text that must survive). Each history is one shape DeepSeek was seen to reject.
HISTORIES = [
    ("nothink tool_use, the incident", [USER, ant_call(1), ant_result(1)], ["placeholder"]),
    ("blank thinking block", [USER, ant_call(1, [{"type": "thinking", "thinking": " ", "signature": ""}]), ant_result(1)], ["placeholder"]),
    ("redacted thinking only", [USER, ant_call(1, [REDACTED]), ant_result(1)], ["placeholder"]),
    ("real thinking beside redacted", [USER, ant_call(1, [REAL, REDACTED]), ant_result(1)], ["I should call the tool."]),
    ("closed turn then open turn",
     [USER, ant_call(1), ant_result(1), {"role": "assistant", "content": [{"type": "text", "text": "12:00"}]},
      {"role": "user", "content": "Again?"}, ant_call(2), ant_result(2)], ["placeholder", "placeholder"]),
    ("openai tool_calls, no reasoning",
     [USER, oai_call(1), {"role": "tool", "tool_call_id": "call_1", "content": "12:00"}], ["placeholder"]),
    ("openai blank reasoning, stored reasoning",
     [USER, oai_call(1, reasoning_content="", provider_specific_fields={"reasoning_content": "stored why", "x": 1}),
      {"role": "tool", "tool_call_id": "call_1", "content": "12:00"}], ["stored why"]),
]


def reasoning_of(msg):
    if isinstance(msg.get("content"), list):
        return [b["thinking"] for b in msg["content"] if b.get("type") == "thinking"]
    return [msg.get("reasoning_content")]


class DeepSeekReasoningFillTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hook = load_module().deepseek_tool_schema_sanitizer.async_pre_call_deployment_hook

    def run_hook(self, model, messages):
        return asyncio.run(self.hook({"model": model, "messages": messages}, "anthropic_messages"))

    def test_histories_are_filled_without_touching_the_callers_list(self):
        self.assertTrue(HISTORIES, "no histories to check")
        for name, messages, expected in HISTORIES:
            with self.subTest(name):
                before = copy.deepcopy(messages)
                out = self.run_hook("deepseek/deepseek-v4-pro", messages)["messages"]
                self.assertEqual(messages, before, "the caller's messages were modified in place")
                calls = [m for m in out if m["role"] == "assistant"
                         and (m.get("tool_calls") or any(b.get("type") == "tool_use" for b in m.get("content") or []
                                                          if isinstance(b, dict)))]
                self.assertEqual(len(calls), len(expected), "tool-call population changed")
                for msg, want in zip(calls, expected):
                    got = reasoning_of(msg)
                    self.assertEqual(got, [PLACEHOLDER if want == "placeholder" else want])
                    if isinstance(msg.get("content"), list):
                        self.assertEqual(msg["content"][0]["type"], "thinking", "thinking must lead the turn")
                for m in out:
                    if isinstance(m.get("content"), list):
                        self.assertFalse([b for b in m["content"] if b.get("type") == "redacted_thinking"],
                                         "a redacted_thinking block reached DeepSeek")
                if name.startswith("openai blank"):
                    self.assertEqual(out[1]["provider_specific_fields"], {"x": 1})

    def test_a_message_deepseek_accepts_is_passed_through_as_is(self):
        messages = [USER, ant_call(1, [REAL]), ant_result(1), {"role": "assistant", "content": "plain text"}]
        out = self.run_hook("deepseek/deepseek-v4-flash", messages)["messages"]
        self.assertTrue(all(a is b for a, b in zip(out, messages)), "an acceptable message was rebuilt")

    def test_other_providers_are_not_touched(self):
        for model in ("anthropic/claude-sonnet-5-5", "openai/tiel-coder-35b-a3b-mtp", "lm_studio/qwen3-4b"):
            with self.subTest(model):
                self.assertIsNone(self.run_hook(model, [USER, ant_call(1), ant_result(1)]))


if __name__ == "__main__":
    unittest.main()
