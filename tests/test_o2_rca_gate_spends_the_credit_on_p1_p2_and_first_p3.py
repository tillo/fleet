"""The O2 SRE agent must spend its Claude API budget on P1/P2 and the first P3 per title per day.

WHY THIS EXISTS (2026-11-07, infra-r9mzd.43, user decision on infra-3sua):

O2 launches one autonomous RCA per incident: 153 RCAs in 32 h for 106 incidents (~73 a
day, 41 distinct titles, 82% P3/P4), 28.5 LLM calls each at a 68k-token average prompt.
That was ~6 USD/day on DeepSeek (89% of all DeepSeek spend). Priced on Claude Haiku 5.5
the same load is 431-708 USD/month, because ~half the calls cross Haiku's 100k-token
tier (5x price). The user chose: analyse P1/P2 always, the FIRST P3 per title per day,
re-analyses on request, nothing else; and cap every model call under 100k tokens.

The gate lives in `rca_gate.py`, a key of the `o2-sre-agent-shim` ConfigMap in
`openobserve/o2-sre-agent.yml`, installed by that ConfigMap's `entrypoint.py` in front of
the stock app. A gated request is answered with a short policy note in the agent's own
QueryResponse shape, so O2 stores it as the RCA text instead of retrying. The key budget
(openobserve-ai, 30 USD/30d) is the hard stop if the gate is ever not installed.

CI has no o2-sre-agent, httpx or starlette: the module is exec'd on its own and driven
with a hand-built ASGI scope, exactly as the server would call it.
"""

import asyncio
import json
import pathlib
import types
import unittest

import yaml

CONFIG = pathlib.Path(__file__).resolve().parent.parent / "openobserve" / "o2-sre-agent.yml"
DAY = 86400


def shim_data() -> dict:
    docs = [d for d in yaml.safe_load_all(CONFIG.read_text()) if d]
    (cm,) = [d for d in docs if d.get("kind") == "ConfigMap" and d["metadata"]["name"] == "o2-sre-agent-shim"]
    return cm["data"]


def load_gate() -> types.ModuleType:
    module = types.ModuleType("rca_gate")
    exec(compile(shim_data()["rca_gate.py"], "rca_gate.py", "exec"), module.__dict__)
    return module


G = load_gate()


class Decide(unittest.TestCase):
    def test_p1_p2_always_analysed(self):
        for sev in ("P1", "P2", 1, 2, "p2"):
            allow, _ = G.decide(sev, "X on y", False, {"X on y": 0.0}, now=10.0)
            self.assertTrue(allow, sev)

    def test_first_p3_per_title_per_day(self):
        seen = {}
        self.assertTrue(G.decide("P3", "A on b", False, seen, now=1000.0)[0])
        G.record("P3", "A on b", seen, now=1000.0)
        self.assertFalse(G.decide("P3", "A on b", False, seen, now=1000.0 + DAY - 1)[0])
        self.assertTrue(G.decide("P3", "A on b", False, seen, now=1000.0 + DAY)[0])
        self.assertTrue(G.decide("P3", "Other on b", False, seen, now=1001.0)[0])

    def test_p4_p5_and_unknown_are_skipped(self):
        for sev in ("P4", "P5", 4, 5, "Unknown", None, "bogus"):
            allow, reason = G.decide(sev, "T", False, {}, now=0.0)
            self.assertFalse(allow, sev)
            self.assertTrue(reason)

    def test_p3_without_title_is_skipped(self):
        self.assertFalse(G.decide("P3", None, False, {}, now=0.0)[0])

    def test_reanalysis_always_runs(self):
        self.assertTrue(G.decide("P4", None, True, {}, now=0.0)[0])

    def test_record_only_counts_p3(self):
        seen = {}
        G.record("P1", "A", seen, now=5.0)
        G.record("P3", "B", seen, now=5.0)
        self.assertEqual(seen, {"B": 5.0})

    def test_severity_normalisation(self):
        self.assertEqual(G.severity("P3"), "P3")
        self.assertEqual(G.severity(" p1 "), "P1")
        self.assertEqual(G.severity(2), "P2")
        self.assertEqual(G.severity("4"), "P4")
        self.assertIsNone(G.severity(True))
        self.assertIsNone(G.severity("Unknown"))

    def test_severity_range_is_one_to_five(self):
        for edge, want in ((1, "P1"), (5, "P5"), ("P1", "P1"), ("P5", "P5")):
            self.assertEqual(G.severity(edge), want)
        for outside in (0, 6, -1, "P0", "P6", "0", "6"):
            self.assertIsNone(G.severity(outside), outside)

    def test_skip_reason_names_the_raw_severity(self):
        self.assertIn("bogus", G.decide("bogus", "T", False, {}, now=0.0)[1])
        self.assertIn("P4", G.decide(4, "T", False, {}, now=0.0)[1])


class ContextLimit(unittest.TestCase):
    def test_every_gateway_model_gets_the_limit(self):
        provider = {"npm": "x", "models": {"claude-api-haiku": {"name": "claude-api-haiku"}}}
        out = G.with_context_limit(provider)
        limit = out["models"]["claude-api-haiku"]["limit"]
        self.assertLessEqual(limit["context"], 100_000)
        self.assertGreater(limit["output"], 0)
        self.assertEqual(out["models"]["claude-api-haiku"]["name"], "claude-api-haiku")
        self.assertNotIn("limit", provider["models"]["claude-api-haiku"], "input must not be mutated")

    def test_provider_without_models_is_returned_unchanged(self):
        self.assertEqual(G.with_context_limit({"npm": "x"}), {"npm": "x"})


def run(coro):
    return asyncio.run(coro)


async def call(app, method, path, body: bytes, root_path=""):
    """Drive an ASGI app like uvicorn; return (status, json_body)."""
    sent = []
    chunks = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return chunks.pop(0) if chunks else {"type": "http.disconnect"}

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "method": method, "path": path, "root_path": root_path, "headers": []}
    await app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(raw) if raw else None


async def call_text(app, method, path, body: bytes, root_path=""):
    """Drive a STREAMING ASGI app; return (status, headers, raw text)."""
    sent = []
    chunks = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return chunks.pop(0) if chunks else {"type": "http.disconnect"}

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "method": method, "path": path, "root_path": root_path, "headers": []}
    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], {k.lower(): v for k, v in start["headers"]}, raw.decode()


def frames_of(text: str) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in text.splitlines() if line.startswith("data: ")]


class Downstream:
    """Stands in for the stock FastAPI app: records the body it was given."""

    def __init__(self):
        self.bodies = []

    async def __call__(self, scope, receive, send):
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        self.bodies.append(body)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"response":"stock"}'})


def rca_body(incident_id="inc1", severity="P3", **extra):
    ctx = {"incident_id": incident_id, "severity": severity, **extra}
    return json.dumps({"query": "analyse", "context": ctx}).encode()


def lookup_of(table):
    async def lookup(incident_id):
        return table.get(incident_id)
    return lookup


class Middleware(unittest.TestCase):
    def make(self, table, clock=lambda: 1000.0):
        down = Downstream()
        return G.RcaGate(down, lookup=lookup_of(table), clock=clock), down

    def test_skipped_p4_never_reaches_the_model(self):
        app, down = self.make({"inc1": {"title": "T", "severity": "P4"}})
        status, body = run(call(app, "POST", "/sre-rca/query", rca_body(severity="P4")))
        self.assertEqual(status, 200)
        self.assertEqual(down.bodies, [])
        self.assertIn("skipped", body["response"].lower())
        self.assertEqual(set(body["usage"].values()), {0})
        self.assertIn("llm_calls", body["usage"])
        self.assertEqual(body["history"], [])
        self.assertFalse(body["compacted"])

    def test_allowed_p1_passes_the_body_byte_for_byte(self):
        app, down = self.make({"inc1": {"title": "T", "severity": "P1"}})
        raw = rca_body(severity="P1")
        status, body = run(call(app, "POST", "/sre-rca/query", raw))
        self.assertEqual((status, body), (200, {"response": "stock"}))
        self.assertEqual(down.bodies, [raw])

    def test_second_p3_of_a_title_in_a_day_is_skipped(self):
        table = {"a": {"title": "Same on x", "severity": "P3"}, "b": {"title": "Same on x", "severity": "P3"}}
        app, down = self.make(table)
        run(call(app, "POST", "/sre-rca/query", rca_body("a")))
        _, body = run(call(app, "POST", "/sre-rca/query", rca_body("b")))
        self.assertEqual(len(down.bodies), 1)
        self.assertIn("skipped", body["response"].lower())

    def test_incident_severity_wins_over_context(self):
        app, down = self.make({"inc1": {"title": "T", "severity": "P4"}})
        run(call(app, "POST", "/sre-rca/query", rca_body(severity="P1")))
        self.assertEqual(down.bodies, [])

    def test_lookup_failure_falls_back_to_context_severity(self):
        app, down = self.make({})
        run(call(app, "POST", "/sre-rca/query", rca_body(severity=2)))
        self.assertEqual(len(down.bodies), 1)
        run(call(app, "POST", "/sre-rca/query", rca_body(severity="P3")))
        self.assertEqual(len(down.bodies), 1, "P3 with no readable title is skipped")

    def test_reanalysis_runs_whatever_the_severity(self):
        app, down = self.make({"inc1": {"title": "T", "severity": "P4"}})
        run(call(app, "POST", "/sre-rca/query", rca_body(severity="P4", is_reanalysis=True)))
        self.assertEqual(len(down.bodies), 1)

    def test_everything_else_is_untouched(self):
        app, down = self.make({"inc1": {"title": "T", "severity": "P4"}})
        chat = json.dumps({"query": "hi", "context": {}}).encode()
        for method, path, body in (
            ("POST", "/sre-rca/query/stream", chat),                     # interactive UI, no incident
            ("POST", "/o2-ai/query", chat),                              # no incident
            ("GET", "/health", b""),
            ("POST", "/sre-rca/query", b"not json"),                    # let the app 422
        ):
            run(call(app, method, path, body))
        self.assertEqual(len(down.bodies), 4)

    def test_alert_subject_id_is_gated_like_an_incident(self):
        app, down = self.make({"al1": {"title": "T", "severity": "P4"}})
        body = json.dumps({"query": "q", "context": {"subject_id": "al1", "severity": "P4"}}).encode()
        run(call(app, "POST", "/sre-rca/query", body))
        self.assertEqual(down.bodies, [])

    def test_non_gated_routes_keep_the_original_body_stream(self):
        """A pass-through request must reach the app chunk by chunk, not re-buffered."""
        calls = []

        async def down(scope, receive, send):
            while True:
                msg = await receive()
                calls.append(msg)
                if not msg.get("more_body"):
                    break

        app = G.RcaGate(down, lookup=lookup_of({}), clock=lambda: 0.0, log=lambda m: None)
        chunks = [{"type": "http.request", "body": b"a", "more_body": True},
                  {"type": "http.request", "body": b"b", "more_body": False}]

        async def receive():
            return chunks.pop(0)

        # Every POST to a query route is read (that is how the gate recognises an RCA),
        # so the untouched-body guarantee is asserted on routes that are not query routes.
        for method, path in (("POST", "/sre-rca/chat"), ("GET", "/sre-rca/query")):
            chunks[:] = [{"type": "http.request", "body": b"a", "more_body": True},
                         {"type": "http.request", "body": b"b", "more_body": False}]
            calls.clear()
            run(app({"type": "http", "method": method, "path": path}, receive, None))
            self.assertEqual(len(calls), 2, (method, path))

    def test_every_decision_is_logged_with_its_incident(self):
        lines = []
        down = Downstream()
        app = G.RcaGate(down, lookup=lookup_of({"inc9": {"title": "T", "severity": "P4"}}),
                        clock=lambda: 0.0, log=lines.append)
        run(call(app, "POST", "/sre-rca/query", rca_body("inc9", severity="P4")))
        self.assertEqual(len(lines), 1)
        self.assertIn("incident=inc9", lines[0])
        self.assertIn("SKIP", lines[0])

    # --- the SSE route: the one O2 actually uses for autonomous RCAs ---------
    # Measured 2026-11-07: 62 RCA launches in 12 h on /sre-rca/query/stream and
    # ZERO gate decisions, because the matcher only accepted /<agent>/query. The
    # gate failed OPEN and silent, which is the shape it exists to prevent.

    def test_stream_route_skips_a_p4_with_an_sse_policy_note(self):
        app, down = self.make({"inc1": {"title": "T", "severity": "P4"}})
        status, headers, text = run(
            call_text(app, "POST", "/sre-rca/query/stream", rca_body(severity="P4")))
        self.assertEqual(status, 200)
        self.assertEqual(down.bodies, [], "a skipped RCA must never reach the model")
        self.assertTrue(headers[b"content-type"].startswith(b"text/event-stream"),
                        headers.get(b"content-type"))
        frames = frames_of(text)
        self.assertEqual([f["type"] for f in frames], ["message", "complete"])
        self.assertIn("skipped", frames[0]["content"].lower())

    def test_stream_route_passes_an_allowed_p1_byte_for_byte(self):
        app, down = self.make({"inc1": {"title": "T", "severity": "P1"}})
        raw = rca_body(severity="P1")
        status, _, body = run(call_text(app, "POST", "/sre-rca/query/stream", raw))
        self.assertEqual((status, body), (200, '{"response":"stock"}'))
        self.assertEqual(down.bodies, [raw])

    def test_stream_route_skips_the_second_p3_of_a_title(self):
        table = {"a": {"title": "Same on x", "severity": "P3"}, "b": {"title": "Same on x", "severity": "P3"}}
        app, down = self.make(table)
        run(call_text(app, "POST", "/sre-rca/query/stream", rca_body("a")))
        _, _, text = run(call_text(app, "POST", "/sre-rca/query/stream", rca_body("b")))
        self.assertEqual(len(down.bodies), 1)
        self.assertIn("skipped", frames_of(text)[0]["content"].lower())

    def test_a_query_shaped_request_the_matcher_rejects_is_logged(self):
        """The failure mode: the gate ran, the traffic arrived on a path it did not
        accept, and NOTHING was logged. Any rejected query-shaped request must say so."""
        lines = []
        down = Downstream()
        app = G.RcaGate(down, lookup=lookup_of({}), clock=lambda: 0.0, log=lines.append)
        run(call(app, "POST", "/sre-rca/query/stream/", rca_body(severity="P4")))
        self.assertEqual(len(down.bodies), 1, "an unmatched path still reaches the app")
        self.assertTrue(any("UNMATCHED" in line and "/sre-rca/query/stream/" in line for line in lines),
                        lines)

    def test_every_query_shaped_post_the_gate_does_not_inspect_is_logged(self):
        """A query-shaped POST that escapes the matcher must leave a trace, or a silent
        bypass is indistinguishable from a silent match failure."""
        lines = []
        app = G.RcaGate(Downstream(), lookup=lookup_of({}), clock=lambda: 0.0, log=lines.append)
        run(call(app, "GET", "/health", b""))
        run(call(app, "POST", "/sre-rca/chat", b"{}"))
        self.assertEqual(lines, [], "health probes and non-query POSTs stay quiet")
        run(call(app, "POST", "/sre-rca/query/extra", b"{}"))
        self.assertEqual(len(lines), 1)
        self.assertIn("UNMATCHED POST /sre-rca/query/extra", lines[0])

    # --- root_path: what production actually sends -----------------------------
    # Measured 2026-11-08 on the live pod: `POST /o2_agent/sre-rca/query` with
    # root_path='/o2_agent'. Every unit test above drives the app WITHOUT a
    # root_path, which is exactly why the gate passed its tests for a day while
    # gating zero real requests.

    def test_a_request_behind_a_root_path_is_gated(self):
        app, down = self.make({"inc1": {"title": "T", "severity": "P4"}})
        status, body = run(call(app, "POST", "/o2_agent/sre-rca/query",
                                rca_body(severity="P4"), root_path="/o2_agent"))
        self.assertEqual(status, 200)
        self.assertEqual(down.bodies, [], "a P4 behind root_path must not reach the model")
        self.assertIn("skipped", body["response"].lower())

    def test_a_stream_request_behind_a_root_path_answers_with_sse(self):
        app, down = self.make({"inc1": {"title": "T", "severity": "P4"}})
        status, headers, text = run(call_text(app, "POST", "/o2_agent/sre-rca/query/stream",
                                              rca_body(severity="P4"), root_path="/o2_agent"))
        self.assertEqual(status, 200)
        self.assertEqual(down.bodies, [])
        self.assertTrue(headers[b"content-type"].startswith(b"text/event-stream"))
        self.assertIn("skipped", frames_of(text)[0]["content"].lower())

    def test_an_allowed_request_behind_a_root_path_still_passes(self):
        app, down = self.make({"inc1": {"title": "T", "severity": "P1"}})
        raw = rca_body(severity="P1")
        status, body = run(call(app, "POST", "/o2_agent/sre-rca/query", raw, root_path="/o2_agent"))
        self.assertEqual((status, body), (200, {"response": "stock"}))
        self.assertEqual(down.bodies, [raw])

    def test_root_path_is_stripped_only_when_it_is_a_prefix(self):
        app, down = self.make({"inc1": {"title": "T", "severity": "P4"}})
        run(call(app, "POST", "/sre-rca/query", rca_body(severity="P4"), root_path="/o2_agent"))
        self.assertEqual(down.bodies, [], "no prefix: the path is already a route path")

    def test_both_query_routes_are_gated(self):
        """The matcher must not depend on the exact suffix again."""
        for path in ("/sre-rca/query/stream", "/sre-rca/query"):
            app, down = self.make({"inc1": {"title": "T", "severity": "P4"}})
            run(call_text(app, "POST", path, rca_body(severity="P4")))
            self.assertEqual(down.bodies, [], path)

    def test_lifespan_and_websocket_scopes_pass_through(self):
        seen = []

        async def down(scope, receive, send):
            seen.append(scope["type"])

        app = G.RcaGate(down, lookup=lookup_of({}), clock=lambda: 0.0)
        run(app({"type": "lifespan"}, None, None))
        self.assertEqual(seen, ["lifespan"])


class Replay(unittest.TestCase):
    def test_replay_delivers_the_body_once_then_disconnects(self):
        receive = G._replay(b"xyz")
        first = run(receive())
        self.assertEqual(first, {"type": "http.request", "body": b"xyz", "more_body": False})
        self.assertEqual(run(receive()), {"type": "http.disconnect"})


class Wiring(unittest.TestCase):
    def test_entrypoint_installs_the_gate_and_the_limit_and_fails_open(self):
        src = shim_data()["entrypoint.py"]
        for needle in ("rca_gate", "build_gateway_provider", "create_app", "NOT applied", "run_app()"):
            self.assertIn(needle, src)

    def test_old_health_probe_shim_is_gone(self):
        docs = [d for d in yaml.safe_load_all(CONFIG.read_text()) if d]
        names = [d["metadata"]["name"] for d in docs if d.get("kind") == "ConfigMap"]
        self.assertNotIn("o2-sre-agent-health-probe", names)

    def test_agent_runs_on_the_haiku_api_route(self):
        docs = [d for d in yaml.safe_load_all(CONFIG.read_text()) if d]
        (dep,) = [d for d in docs if d.get("kind") == "Deployment"]
        env = {e["name"]: e.get("value") for e in dep["spec"]["template"]["spec"]["containers"][0]["env"]}
        self.assertEqual(env["O2_AI_MODEL"], "claude-api-haiku")


if __name__ == "__main__":
    unittest.main()
