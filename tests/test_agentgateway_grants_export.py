"""The front door's grant matrix must be PUBLISHED, and its inconsistencies paged.

The incident this encodes: since fleet !1776 the MCP routes and their key policies
are rows in the gateway's database, so git cannot say what a seat may reach. A
seat's grant is `metadata.servers` on the key, and the route's authorization rule
reads exactly that (`"<server>" in apiKey.servers`). So:

  * a grant removed from the server's OWN route leaves the seat's client config
    untouched and every call answers 401 — silent in git, silent to the gateway's
    health probe, silent to the seat until someone tries;
  * the same `servers` list is copied onto every route row, so a grant added to
    one route and not to the server's own route is a seat that reaches one server
    and not the server it names — also silent.

CronJob agentgateway/agentgateway-catalog-export already reads the admin API from
inside the namespace (it is the only caller besides ingress-nginx, because that
API does not authenticate). It now also writes `agentgateway-grants` and pushes
the matrix to the Pushgateway, alerted by 77b-agentgateway-grants.yml. This test
pins that arithmetic and its plumbing, offline.
"""

from __future__ import annotations

import http.server
import json
import pathlib
import threading
import types
import unittest

import yaml

import fleetlib

WIRING = "agentgateway-standalone-wiring"
MANIFEST = pathlib.Path(__file__).resolve().parent.parent / WIRING / "agentgateway-catalog-export.yml"
RULES = pathlib.Path(__file__).resolve().parent.parent / "monitoring-rules" / "77b-agentgateway-grants.yml"


def load_export() -> types.ModuleType:
    cm = next(d for d in yaml.safe_load_all(MANIFEST.read_text()) if d and d["kind"] == "ConfigMap")
    mod = types.ModuleType("agentgateway_catalog_export")
    exec(compile(cm["data"]["export.py"], str(MANIFEST), "exec"), mod.__dict__)
    return mod


export = load_export()


def key(seat, *servers):
    return {"keyHash": "sha256:" + "0" * 64, "metadata": {"client": seat, "servers": list(servers)}}


def route(server, *keys, gateway="mcp", host=None):
    value = {
        "name": "mcp-" + server,
        "gateways": gateway,
        "matches": [{"path": {"pathPrefix": "/%s/" % server}}],
        "backends": [{"mcp": {"targets": [{"mcp": {"host": host or server + ".ns.svc.cluster.local",
                                                   "port": 8080, "path": "/mcp"}, "name": server}]},
                      "policies": {"backendAuth": {"key": {"value": {"file": "/mcp-upstream/" + server}}}}}],
        "policies": {"apiKey": {"mode": "strict", "keys": list(keys)},
                     "authorization": {"rules": [{"allow": '"%s" in apiKey.servers' % server}]}},
    }
    return {"kind": "traffic.route", "id": "mcp-" + server, "value": value}


def oauth_route(server):
    value = route(server)["value"]
    value["matches"] = [{"path": {"exact": "/oauth/%s/mcp" % server}}]
    del value["policies"]["apiKey"]
    return {"kind": "traffic.route", "id": "mcp-oauth-" + server, "value": value}


class Catalog(unittest.TestCase):
    def test_the_catalog_keeps_path_prefix_routes_only(self):
        doc = json.loads(export.catalog([route("memsearch", key("seat-jump", "memsearch")),
                                         oauth_route("memsearch")]))
        self.assertEqual([r["id"] for r in doc["resources"]], ["mcp-memsearch"])

    def test_the_catalog_carries_no_policies_and_no_key_hashes(self):
        text = export.catalog([route("memsearch", key("seat-jump", "memsearch"))])
        self.assertNotIn("sha256", text)
        self.assertNotIn("apiKey", text)
        self.assertNotIn("backendAuth", text)

    def test_the_catalog_names_the_upstream(self):
        doc = json.loads(export.catalog([route("memsearch", key("seat-jump", "memsearch"))]))
        self.assertEqual(doc["resources"][0]["upstream"],
                         {"host": "memsearch.ns.svc.cluster.local", "port": 8080, "path": "/mcp"})

    def test_a_route_without_a_target_is_not_a_catalog_entry(self):
        r = route("memsearch", key("seat-jump", "memsearch"))
        r["value"]["backends"] = []
        self.assertEqual(json.loads(export.catalog([r]))["resources"], [])

    def test_a_non_route_resource_is_ignored(self):
        self.assertEqual(json.loads(export.catalog([{"kind": "llm.model", "id": "x", "value": {}}]))["resources"], [])


class Grants(unittest.TestCase):
    def test_a_grant_is_the_servers_list_on_the_servers_own_route(self):
        grants, invalid = export.grant_matrix([route("memsearch", key("seat-jump", "memsearch"))])
        self.assertEqual(grants, {"seat-jump": ["memsearch"]})
        self.assertEqual(invalid, [])

    def test_a_seat_granted_on_several_routes_holds_all_of_them(self):
        grants, invalid = export.grant_matrix([
            route("memsearch", key("seat-jump", "memsearch", "kubernetes")),
            route("kubernetes", key("seat-jump", "memsearch", "kubernetes")),
        ])
        self.assertEqual(grants, {"seat-jump": ["kubernetes", "memsearch"]})
        self.assertEqual(invalid, [])

    def test_a_grant_only_on_another_route_is_invalid_not_a_grant(self):
        grants, invalid = export.grant_matrix([
            route("memsearch", key("seat-jump", "memsearch", "notes")),
            route("notes"),
        ])
        self.assertEqual(grants, {"seat-jump": ["memsearch"]})
        self.assertEqual(invalid, [{"seat": "seat-jump", "server": "notes", "route": "mcp-memsearch"}])

    def test_a_grant_for_a_server_with_no_route_at_all_is_invalid(self):
        grants, invalid = export.grant_matrix([route("memsearch", key("seat-jump", "memsearch", "gone"))])
        self.assertEqual(grants, {"seat-jump": ["memsearch"]})
        self.assertEqual(invalid, [{"seat": "seat-jump", "server": "gone", "route": "mcp-memsearch"}])

    def test_keys_without_a_client_name_grant_nothing(self):
        grants, invalid = export.grant_matrix([route("memsearch", {"keyHash": "sha256:aa"})])
        self.assertEqual((grants, invalid), ({}, []))

    def test_a_route_without_an_api_key_grants_nothing(self):
        self.assertEqual(export.grant_matrix([oauth_route("memsearch")]), ({}, []))

    def test_seats_and_servers_are_sorted_so_the_push_is_stable(self):
        grants, _ = export.grant_matrix([route("b", key("seat-z", "b")), route("a", key("seat-a", "a"))])
        self.assertEqual(list(grants), ["seat-a", "seat-z"])


class Metrics(unittest.TestCase):
    def test_every_grant_is_a_series(self):
        text = export.metrics_text({"seat-jump": ["memsearch", "notes"]}, [], now=7)
        self.assertIn('agentgateway_mcp_grant{seat="seat-jump",server="memsearch"} 1', text)
        self.assertIn('agentgateway_mcp_grant{seat="seat-jump",server="notes"} 1', text)

    def test_the_run_stamp_and_outcome_are_pushed(self):
        text = export.metrics_text({}, [], now=1762000000)
        self.assertIn("agentgateway_grants_last_run_timestamp_seconds 1762000000", text)
        self.assertIn("agentgateway_grants_last_run_success 1", text)
        self.assertIn("agentgateway_grants_last_run_success 0", export.metrics_text({}, [], success=0, now=1))

    def test_a_seat_reports_how_much_it_holds(self):
        text = export.metrics_text({"seat-jump": ["a", "b", "c"], "kagent": ["a"]}, [], now=1)
        self.assertIn('agentgateway_mcp_seat_granted{seat="seat-jump"} 3', text)
        self.assertIn('agentgateway_mcp_seat_granted{seat="kagent"} 1', text)

    def test_an_invalid_grant_is_its_own_series(self):
        text = export.metrics_text({}, [{"seat": "s", "server": "n", "route": "mcp-m"}], now=1)
        self.assertIn('agentgateway_mcp_grant_invalid{seat="s",server="n",route="mcp-m"} 1', text)

    def test_no_invalid_series_is_pushed_when_there_is_nothing_to_report(self):
        self.assertNotIn("grant_invalid", export.metrics_text({}, [], now=1))

    def test_label_values_are_escaped(self):
        text = export.metrics_text({"a\"b\\c": ["x"], }, [], now=1)
        self.assertIn('seat="a\\"b\\\\c"', text)

    def test_the_text_is_prometheus_exposition(self):
        text = export.metrics_text({"s": ["x"]}, [], now=1)
        self.assertTrue(text.endswith("\n"))
        self.assertIn("# TYPE agentgateway_mcp_grant gauge", text)


class Push(unittest.TestCase):
    def setUp(self):
        self.seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_PUT(self):
                self.rfile.read(int(self.headers["content-length"]))
                self.server.seen.append(("PUT", self.path))
                self.send_response(202)
                self.end_headers()

            def do_DELETE(self):
                self.server.seen.append(("DELETE", self.path))
                self.send_response(202)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.seen = self.seen
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_port

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_the_group_is_cleared_before_it_is_written(self):
        export.push("m 1\n", base=self.base, job="agentgateway_catalog_export")
        self.assertEqual([m for m, _ in self.seen], ["DELETE", "PUT"])
        self.assertEqual(self.seen[0][1], "/metrics/job/agentgateway_catalog_export")

    def test_a_missing_group_does_not_stop_the_push(self):
        class Gone(http.server.BaseHTTPRequestHandler):
            def do_PUT(self):
                self.rfile.read(int(self.headers["content-length"]))
                self.send_response(202)
                self.end_headers()

            def do_DELETE(self):
                self.send_response(404)
                self.end_headers()

            def log_message(self, *a):
                pass

        httpd = http.server.HTTPServer(("127.0.0.1", 0), Gone)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            export.push("m 1\n", base="http://127.0.0.1:%d" % httpd.server_port, job="j")
        finally:
            httpd.shutdown()
            httpd.server_close()


class Wiring(unittest.TestCase):
    """The plumbing the arithmetic above hangs on."""

    def docs(self):
        return list(yaml.safe_load_all(MANIFEST.read_text()))

    def test_the_role_may_write_the_grants_configmap(self):
        role = next(d for d in self.docs() if d["kind"] == "Role")
        named = {n for r in role["rules"] for n in r.get("resourceNames", [])}
        self.assertIn("agentgateway-catalog", named)
        self.assertIn("agentgateway-grants", named)

    def test_the_job_still_mounts_the_script_it_runs(self):
        cj = next(d for d in self.docs() if d["kind"] == "CronJob")
        c = cj["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(c["command"][-1], "/export/export.py")
        self.assertEqual(cj["spec"]["jobTemplate"]["spec"]["template"]["spec"]["volumes"][0]["configMap"]["name"],
                         "agentgateway-catalog-export")

    def test_the_alerts_the_export_feeds_exist(self):
        rules = next(d for d in yaml.safe_load_all(RULES.read_text()) if d and d["kind"] == "PrometheusRule")
        names = {r["alert"] for g in rules["spec"]["groups"] for r in g["rules"]}
        self.assertEqual(names, {"AgentgatewayGrantExportStale", "AgentgatewayGrantExportFailed",
                                 "AgentgatewayGrantInconsistent", "AgentgatewaySeatLostAllGrants",
                                 "AgentgatewayGrantMatrixEmpty"})

    def test_every_rule_reads_the_pushed_job(self):
        rules = next(d for d in yaml.safe_load_all(RULES.read_text()) if d and d["kind"] == "PrometheusRule")
        for g in rules["spec"]["groups"]:
            for r in g["rules"]:
                self.assertIn('job="agentgateway_catalog_export"', r["expr"], r["alert"])

    def test_the_job_name_is_what_the_rules_query(self):
        self.assertEqual(export.JOB, "agentgateway_catalog_export")


if __name__ == "__main__":
    unittest.main()
