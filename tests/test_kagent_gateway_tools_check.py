"""kagent's tools must be callable through the MCP front door — checked LIVE.

The incident (fleet !1711): the gateway's Backstage tool allowlist named the
tools bare (`get-catalog-entity`) while Backstage serves them as
`catalog.<action>`. Nothing errored: tools/list came back `[]` and every call
"unknown resource". A route the `kagent` key holds no grant for fails the same
quiet way (403 inside the agent pod, while the Agent CR still says Accepted).

A static test read the gateway's policies from git until fleet !1776 moved the
routes and their policies into the gateway's database, so git can no longer
say what the `kagent` key is allowed. The CronJob kagent-gateway-tools-check
(kagent-mdapi/kagent-gateway-tools-check.yml) asks the gateway instead:
tools/list with the kagent key on every RemoteMCPServer behind it, compared
with each Agent's toolNames, pushed to the Pushgateway and alerted by
KagentGatewayToolMissing. This test pins the comparison, offline.
"""

from __future__ import annotations

import http.server
import json
import pathlib
import threading
import types
import unittest

import yaml

MANIFEST = pathlib.Path(__file__).resolve().parent.parent / "kagent-mdapi" / "kagent-gateway-tools-check.yml"


def load_check() -> types.ModuleType:
    cm = next(d for d in yaml.safe_load_all(MANIFEST.read_text()) if d and d["kind"] == "ConfigMap")
    mod = types.ModuleType("kagent_gateway_tools_check")
    exec(compile(cm["data"]["check.py"], str(MANIFEST), "exec"), mod.__dict__)
    return mod


check = load_check()
GW = "http://mcp.agentgateway.svc.cluster.local:8080"


def server(name, url):
    return {"metadata": {"name": name}, "spec": {"url": url}}


def agent(name, *tools):
    return {"metadata": {"name": name}, "spec": {"declarative": {"tools": list(tools)}}}


def mcp(server_name, *names, kind="RemoteMCPServer"):
    return {"type": "McpServer", "mcpServer": {"name": server_name, "kind": kind, "toolNames": list(names)}}


SERVERS = [server("cat", f"{GW}/cat/mcp"), server("far", "http://elsewhere:80/far/mcp")]


class Expectations(unittest.TestCase):
    def test_each_agent_tool_behind_the_gateway_is_expected(self):
        agents = [agent("a", mcp("cat", "read", "list"), {"type": "Agent", "agent": {"name": "b"}})]
        self.assertEqual(check.expectations(agents, SERVERS),
                         [check.Want("a", "cat", f"{GW}/cat/mcp", ("list", "read"))])

    def test_a_server_not_behind_the_gateway_is_not_checked(self):
        self.assertEqual(check.expectations([agent("a", mcp("far", "x"))], SERVERS), [])

    def test_a_tool_on_an_unknown_server_is_named_not_skipped(self):
        with self.assertRaisesRegex(ValueError, "a: RemoteMCPServer 'gone' does not exist"):
            check.expectations([agent("a", mcp("gone", "x"))], SERVERS)

    def test_another_server_kind_is_ignored(self):
        self.assertEqual(check.expectations([agent("a", mcp("cat", "x", kind="MCPServer"))], SERVERS), [])


class Missing(unittest.TestCase):
    WANT = [check.Want("a", "cat", f"{GW}/cat/mcp", ("list", "read"))]

    def test_every_tool_listed_is_clean(self):
        self.assertEqual(check.missing(self.WANT, {f"{GW}/cat/mcp": {"read", "list", "extra"}}), [])

    def test_a_filtered_tool_is_named(self):
        self.assertEqual(check.missing(self.WANT, {f"{GW}/cat/mcp": {"read"}}), [("a", "cat", "list")])

    def test_a_server_that_listed_nothing_misses_every_tool(self):
        self.assertEqual(check.missing(self.WANT, {f"{GW}/cat/mcp": set()}),
                         [("a", "cat", "list"), ("a", "cat", "read")])


class Listings(unittest.TestCase):
    WANT = [check.Want("a", "cat", f"{GW}/cat/mcp", ("read",)), check.Want("b", "cat", f"{GW}/cat/mcp", ("list",)),
            check.Want("a", "dog", f"{GW}/dog/mcp", ("bark",))]

    def test_each_url_is_listed_once(self):
        calls = []
        listed, ok = check.listings(self.WANT, lambda url: calls.append(url) or {"read"})
        self.assertEqual(calls, [f"{GW}/cat/mcp", f"{GW}/dog/mcp"])
        self.assertEqual(listed, {f"{GW}/cat/mcp": {"read"}, f"{GW}/dog/mcp": {"read"}})
        self.assertEqual(ok, {"cat": True, "dog": True})

    def test_a_refused_listing_lists_nothing_and_is_not_ok(self):
        refused = check.urllib.error.HTTPError(f"{GW}/dog/mcp", 403, "Forbidden", {}, None)
        self.addCleanup(refused.close)

        def lister(url):
            if "dog" in url:
                raise refused
            return {"read", "list"}
        listed, ok = check.listings(self.WANT, lister)
        self.assertEqual(listed[f"{GW}/dog/mcp"], set())
        self.assertEqual(ok, {"cat": True, "dog": False})
        self.assertEqual(check.missing(self.WANT, listed), [("a", "dog", "bark")])


class FakeGateway(http.server.BaseHTTPRequestHandler):
    """An MCP endpoint that answers like agentgateway: event stream, session id, key check."""
    seen: list = []

    def do_POST(self):
        msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeGateway.seen.append((msg.get("method"), self.headers.get("Mcp-Session-Id"), self.headers["Accept"]))
        if self.headers.get("Authorization") != "Bearer good":
            self.send_response(401)
            self.end_headers()
            return
        if "id" not in msg:
            self.send_response(202)
            self.end_headers()
            return
        result = {"tools": [{"name": "read"}, {"name": "list"}]} if msg["method"] == "tools/list" else {}
        body = f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result})}\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Mcp-Session-Id", "s-1")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


class ToolsList(unittest.TestCase):
    def setUp(self):
        FakeGateway.seen = []
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), FakeGateway)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/cat/mcp"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_initialize_then_list_in_one_session(self):
        self.assertEqual(check.tools_list(self.url, "Bearer good"), {"read", "list"})
        self.assertEqual([(m, s) for m, s, _ in FakeGateway.seen],
                         [("initialize", None), ("notifications/initialized", "s-1"), ("tools/list", "s-1")])
        self.assertTrue(all(a == "application/json, text/event-stream" for _, _, a in FakeGateway.seen))

    def test_a_wrong_key_is_an_error_not_an_empty_list(self):
        with self.assertRaises(check.urllib.error.HTTPError) as caught:
            check.tools_list(self.url, "Bearer bad")
        caught.exception.close()


class Exposition(unittest.TestCase):
    def test_counts_and_one_series_per_missing_tool(self):
        want = [check.Want("a", "cat", f"{GW}/cat/mcp", ("list", "read"))]
        text = check.exposition(want, [("a", "cat", "list")], {"cat": False})
        self.assertIn("kagent_gateway_tools_checked 2\n", text)
        self.assertIn("kagent_gateway_tools_missing_total 1\n", text)
        self.assertIn('kagent_gateway_tool_missing{agent="a",server="cat",tool="list"} 1\n', text)
        self.assertIn('kagent_gateway_tools_list_ok{server="cat"} 0\n', text)
        self.assertTrue(text.endswith("\n"))

    def test_a_clean_run_carries_no_missing_series(self):
        text = check.exposition([], [], {})
        self.assertIn("kagent_gateway_tools_missing_total 0\n", text)
        self.assertNotIn("kagent_gateway_tool_missing{", text)


class Wire(unittest.TestCase):
    def test_an_event_stream_answer_is_read(self):
        body = 'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"x"}]}}\n\n'
        self.assertEqual(check.rpc_result(body, "text/event-stream", 2), {"tools": [{"name": "x"}]})

    def test_a_json_answer_is_read(self):
        self.assertEqual(check.rpc_result('{"jsonrpc":"2.0","id":1,"result":{}}', "application/json", 1), {})

    def test_an_rpc_error_fails_loudly(self):
        with self.assertRaisesRegex(RuntimeError, "unknown"):
            check.rpc_result('{"jsonrpc":"2.0","id":1,"error":{"code":-1,"message":"unknown"}}',
                             "application/json", 1)

    def test_an_answer_without_the_id_fails_loudly(self):
        with self.assertRaisesRegex(RuntimeError, "no answer to id 3"):
            check.rpc_result('event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\n',
                             "text/event-stream", 3)


if __name__ == "__main__":
    unittest.main()
