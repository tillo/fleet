"""cribl-am-relay forwards to Alertmanager only for the fluentd login (infra-o4i3m.5.3).

The relay turns log lines POSTed to :9999/ingest into Alertmanager alerts. Until
2026-11-16 it took any POST: any pod could reach it and raise a fake ModSec, pod I/O,
postfix or Redis alert, i.e. page the operator with whatever text it liked, or drown a
real one (network-trust audit 2026-11-10, infra-o4i3m.5). Its only real sender is the
rancher-logging ClusterOutput `am-relay` (fluentd); Cribl's old `alertmanager_webhook`
output posts to `/`, which the relay has dropped since 2026-06.

What this pins:
  * a POST without the fluentd Basic login, or with a wrong one, gets 401 and nothing
    is forwarded, on every path (an unauthenticated 204 on `/` would still be a door);
  * the right login still turns a detector-matching line into an alert;
  * the relay refuses to start without its password (fail closed, never open);
  * the manifests agree: fluentd sends user `fluentd` with the password from the Secret
    the relay mounts, both Secrets come from the SAME Akeyless item, and the relay reads
    the file where the Deployment mounts it. If they drift apart, alerts stop with no
    error anywhere but fluentd's retry log.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests.fleetlib import load_docs

RELAY = "monitoring-cribl-am-relay/relay.yml"
AUTH_ES = "monitoring-cribl-am-relay/relay-ingest-auth-es.yml"
OUTPUT = "monitoring-logging-flows/output-am-relay.yml"
OUTPUT_ES = "monitoring-logging-flows/am-relay-ingest-auth-es.yml"
AKEYLESS_ITEM = "/mdapi/monitoring/am-relay/ingest-password"
USER = "fluentd"
PASSWORD = "s3cret-for-tests"

MODSEC_LINE = (
    '2026/11/16 22:00:00 [error] 1#1: *1 [client 10.52.0.5] ModSecurity: Access denied '
    'with code 403 (phase 2). [id "942100"] [msg "SQL Injection"] '
    '[hostname "mail.mdapi.ch"] [uri "/x"] Total Score: 5, request: "GET /x HTTP/1.1"'
)


def doc(rel: str, kind: str) -> dict:
    return next(d for d in load_docs(rel) if d and d.get("kind") == kind)


def relay_module():
    code = doc(RELAY, "ConfigMap")["data"]["server.py"]
    path = Path(tempfile.mkdtemp()) / "am_relay.py"
    path.write_text(code)
    spec = importlib.util.spec_from_file_location("am_relay", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


class RelayIngestAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = relay_module()
        cls.forwarded = []
        handler = cls.mod.make_handler(PASSWORD, lambda body: (cls.forwarded.append(body), (200, b""))[1])
        cls.server = cls.mod.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self.forwarded.clear()

    def post(self, path: str, auth: str | None) -> int:
        body = json.dumps([{"log": MODSEC_LINE}]).encode()
        headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        if auth is not None:
            headers["Authorization"] = auth
        req = urllib.request.Request(self.base + path, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_no_login_is_refused_and_nothing_forwarded(self):
        for path in ("/ingest", "/"):
            with self.subTest(path=path):
                self.assertEqual(self.post(path, None), 401)
        self.assertEqual(self.forwarded, [])

    def test_wrong_login_is_refused(self):
        for auth in (basic(USER, "wrong"), basic("admin", PASSWORD), "Bearer " + PASSWORD, "Basic !!!"):
            with self.subTest(auth=auth):
                self.assertEqual(self.post("/ingest", auth), 401)
        self.assertEqual(self.forwarded, [])

    def test_fluentd_login_still_raises_the_alert(self):
        self.assertEqual(self.post("/ingest", basic(USER, PASSWORD)), 200)
        alerts = json.loads(self.forwarded[0])
        self.assertEqual(alerts[0]["labels"]["alertname"], "ModSecBlockLikelyFalsePositive")

    def test_refuses_to_start_without_a_password(self):
        missing = Path(tempfile.mkdtemp()) / "password"
        with self.assertRaises(SystemExit):
            self.mod.load_password(missing)
        missing.write_text("\n")
        with self.assertRaises(SystemExit):
            self.mod.load_password(missing)
        missing.write_text(PASSWORD + "\n")
        self.assertEqual(self.mod.load_password(missing), PASSWORD)


class RelayAuthManifestsAgree(unittest.TestCase):
    def test_both_secrets_come_from_one_akeyless_item(self):
        for rel, ns in ((AUTH_ES, "cribl"), (OUTPUT_ES, "cattle-logging-system")):
            with self.subTest(rel=rel):
                es = doc(rel, "ExternalSecret")
                self.assertEqual(es["metadata"]["namespace"], ns)
                self.assertEqual(es["spec"]["target"]["name"], "am-relay-ingest-auth")
                [item] = es["spec"]["data"]
                self.assertEqual(item["remoteRef"]["key"], AKEYLESS_ITEM)
                self.assertEqual(item["secretKey"], "password")

    def test_fluentd_sends_the_login(self):
        auth = doc(OUTPUT, "ClusterOutput")["spec"]["http"]["auth"]
        self.assertEqual(auth["username"], {"value": USER})
        self.assertEqual(
            auth["password"]["valueFrom"]["secretKeyRef"],
            {"name": "am-relay-ingest-auth", "key": "password"},
        )

    def test_relay_reads_the_mounted_secret(self):
        pod = doc(RELAY, "Deployment")["spec"]["template"]["spec"]
        [ctr] = pod["containers"]
        mounts = {m["name"]: m["mountPath"] for m in ctr["volumeMounts"]}
        vols = {v["name"]: v for v in pod["volumes"]}
        self.assertEqual(vols["ingest-auth"]["secret"]["secretName"], "am-relay-ingest-auth")
        self.assertNotIn("optional", vols["ingest-auth"]["secret"])
        self.assertEqual(
            str(Path(mounts["ingest-auth"]) / "password"), self.mod_password_path()
        )

    def mod_password_path(self) -> str:
        return str(relay_module().PASSWORD_FILE)


if __name__ == "__main__":
    unittest.main()
