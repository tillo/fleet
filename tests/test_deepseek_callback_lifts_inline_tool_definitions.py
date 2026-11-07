"""The litellm DeepSeek callback must turn inline tool definitions into references.

WHY THIS EXISTS (2026-10-20):

`work` hit the Anthropic account cap, litellm fell back to `cn-deepseek-flash`, and the
fallback died:

    422 Failed to deserialize the JSON body into the target type:
        messages[4].content: unknown variant `tool_definition`, expected `tool_reference`

Claude Code declares tools that arrive mid-conversation (MCP servers, deferred tools)
inline, as a `role: system` message carrying `{"type": "tool_addition", "tool":
{"type": "tool_definition", "definition": {...}}}`. DeepSeek's Anthropic endpoint only
knows `{"type": "tool_reference", "name": ...}` there, and only for a tool that is in
`tools[]` (else 400 "references unknown tool"). The session worked again only with
every MCP server disabled. The fix lifts each definition into `tools[]` and leaves a
reference behind, so no tool is lost.

Like the reasoning fill, it must never edit the caller's messages or tools in place: the
router reuses them for every fallback hop. The module is loaded the same way as in
test_deepseek_callback_fills_tool_call_reasoning.py.
"""

import asyncio
import copy
import unittest

from test_deepseek_callback_fills_tool_call_reasoning import load_module

USER = {"role": "user", "content": [{"type": "text", "text": "Search for cats."}]}
READ = {"name": "Read", "description": "Read a file", "input_schema": {"type": "object"}}
SEARCH = {"name": "mcp__web__search", "description": "Search the web",
          "input_schema": {"type": "object", "properties": {"q": {"type": "string", "pattern": "^[^\\0]*$"}}}}


def inline(definition):
    return {"type": "tool_addition", "tool": {"type": "tool_definition", "definition": definition}}


def reference(name):
    return {"type": "tool_addition", "tool": {"type": "tool_reference", "name": name}}


def system(*blocks):
    return {"role": "system", "content": list(blocks)}


class DeepSeekInlineToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hook = load_module().deepseek_tool_schema_sanitizer.async_pre_call_deployment_hook

    def run_hook(self, model, messages, tools=None):
        kwargs = {"model": model, "messages": messages}
        if tools is not None:
            kwargs["tools"] = tools
        return asyncio.run(self.hook(kwargs, "anthropic_messages"))

    def test_inline_definition_becomes_a_reference_to_a_lifted_tool(self):
        messages = [USER, {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
                    system(inline(SEARCH)), USER]
        tools = [READ]
        before = copy.deepcopy((messages, tools))
        out = self.run_hook("deepseek/deepseek-v4-flash", messages, tools)
        self.assertEqual((messages, tools), before, "the caller's messages or tools were modified in place")
        self.assertEqual(out["messages"][2]["content"], [reference("mcp__web__search")])
        self.assertEqual([t["name"] for t in out["tools"]], ["Read", "mcp__web__search"])
        self.assertNotIn("pattern", out["tools"][1]["input_schema"]["properties"]["q"],
                         "a lifted tool skipped the NUL-pattern strip")
        for m in out["messages"]:
            for b in m["content"]:
                self.assertNotEqual(b.get("tool", {}).get("type"), "tool_definition")

    def test_a_tool_already_declared_is_not_lifted_twice(self):
        out = self.run_hook("deepseek/deepseek-v4-pro",
                            [system(inline(READ)), USER, system(inline(SEARCH), inline(SEARCH))], [READ])
        self.assertEqual([t["name"] for t in out["tools"]], ["Read", "mcp__web__search"])
        self.assertEqual(out["messages"][2]["content"], [reference("mcp__web__search")] * 2)

    def test_no_tools_key_gets_one(self):
        out = self.run_hook("deepseek/deepseek-v4-flash", [USER, system(inline(SEARCH))])
        self.assertEqual([t["name"] for t in out["tools"]], ["mcp__web__search"])

    def test_messages_without_inline_definitions_are_passed_through_as_is(self):
        messages = [system({"type": "text", "text": "reminder"}, reference("Read")), USER]
        out = self.run_hook("deepseek/deepseek-v4-flash", messages, [READ])
        self.assertTrue(all(a is b for a, b in zip(out["messages"], messages)), "an acceptable message was rebuilt")
        self.assertEqual(out["tools"], [READ])

    def test_other_providers_are_not_touched(self):
        for model in ("anthropic/claude-opus-5-5", "openai/ornith-reap-19b"):
            with self.subTest(model):
                self.assertIsNone(self.run_hook(model, [USER, system(inline(SEARCH))], [READ]))


if __name__ == "__main__":
    unittest.main()
