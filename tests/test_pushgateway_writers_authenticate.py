"""Every in-cluster Pushgateway writer pushes through the authenticated vmauth front.

infra-o4i3m.5.2 (network-trust audit 2026-11-10): pushgateway.monitoring:9091 takes
PUT/POST/DELETE from any pod with no credential, so a compromised pod can fake or delete
the heartbeats BatchJobStale and the deadman alerts key on. monitoring-vm/
vmauth-pushgateway.yml put a write front in front of it (vmauth-pushgateway:8427, user
`pg-writer`); the direct path can only be closed once no writer uses it any more.

What breaks if it regresses:
  * a new or edited writer pointing at :9091 keeps the direct path in use, so it can
    never be closed -- the repo-wide grep below fails on any such line;
  * a writer that reaches the front without the credential gets 401 and its heartbeat
    goes stale -- each client is driven against a local server here and must send
    `Authorization: Basic pg-writer:<password>` on every request;
  * the password must come from the one Akeyless item through an ExternalSecret in the
    writer's own namespace, never a literal;
  * kube-bench's egress fence must open the front's port, or every weekly push is dropped.
"""

from __future__ import annotations

import base64
import http.server
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

import yaml

from tests.fleetlib import repo_root, tracked_files

ROOT = Path(repo_root())
FRONT = "http://vmauth-pushgateway.monitoring.svc.cluster.local:8427"
SECRET, SECRET_KEY = "pushgw-writer", "password"
AKEYLESS = "/mdapi/monitoring/vmauth/pg-writer-password"
ENV = "PUSHGW_WRITER_PASSWORD"
PASSWORD = "s3cr:et/+="
EXPECTED = "Basic " + base64.b64encode(("pg-writer:" + PASSWORD).encode()).decode()

DIRECT = re.compile(r"pushgateway(\.monitoring)?(\.svc)?(\.cluster\.local)?:9091")
# The front's own upstream is the one place that must keep naming :9091.
DIRECT_ALLOWED = {"monitoring-vm/vmauth-pushgateway.yml"}

# (bundle, file holding the pod template, kind, name, container, file holding the ExternalSecret)
WRITERS = [
    ("agentgateway-standalone-wiring", "agentgateway-catalog-export.yml", "CronJob",
     "agentgateway-catalog-export", "export", "agentgateway-catalog-export.yml"),
    ("findmy", "findmy-deploy.yml", "Deployment", "findmy-collector", "collector", "findmy-es.yml"),
    ("kagent-mdapi", "kagent-gateway-tools-check.yml", "CronJob",
     "kagent-gateway-tools-check", "check", "kagent-gateway-tools-check.yml"),
    ("kube-bench", "cronjob.yml", "CronJob", "kube-bench", "kube-bench", "cronjob.yml"),
    ("mac-rules", "lsrules-watch-cron.yml", "CronJob", "lsrules-watch", "watch", "lsrules-es.yml"),
]


def docs(rel: str) -> list[dict]:
    return [d for d in yaml.safe_load_all((ROOT / rel).read_text()) if d]


def one(rel: str, kind: str, name: str | None = None) -> dict:
    (d,) = [d for d in docs(rel) if d["kind"] == kind and (name is None or d["metadata"]["name"] == name)]
    return d


def pod_spec(doc: dict) -> dict:
    spec = doc["spec"]
    if doc["kind"] == "CronJob":
        spec = spec["jobTemplate"]["spec"]
    return spec["template"]["spec"]


def load_script(rel: str, key: str, stubs: dict | None = None) -> types.ModuleType:
    src = one(rel, "ConfigMap")["data"][key]
    mod = types.ModuleType(key.rsplit(".", 1)[0])
    with mock.patch.dict(sys.modules, stubs or {}):
        exec(compile(src, key, "exec"), mod.__dict__)
    return mod


class Recorder:
    """A local Pushgateway stand-in that records the Authorization of every request."""

    def __enter__(self):
        seen = self.seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def answer(self):
                n = int(self.headers.get("content-length") or 0)
                if n:
                    self.rfile.read(n)
                seen.append((self.command, self.path, self.headers.get("Authorization")))
                self.send_response(200)
                self.end_headers()

            do_PUT = do_POST = do_DELETE = answer

            def log_message(self, *a):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_port
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


class NoWriterUsesTheDirectPath(unittest.TestCase):
    def test_no_tracked_manifest_or_script_pushes_to_9091(self):
        hits = []
        for rel in tracked_files():
            if rel in DIRECT_ALLOWED or rel.startswith(("tests/", "gitlab/", "docs/")) or rel.endswith(".md"):
                continue
            path = ROOT / rel
            if path.suffix not in (".yml", ".yaml", ".py", ".sh", ".json", ".ts", ".js"):
                continue
            for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if DIRECT.search(line) and not line.lstrip().startswith(("#", "//")):
                    hits.append(f"{rel}:{n}: {line.strip()}")
        self.assertEqual(hits, [])


class ClientsSendTheWriterCredential(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {ENV: PASSWORD})
        env.start()
        self.addCleanup(env.stop)

    def assert_authenticated(self, rec, methods):
        self.assertEqual([m for m, _, _ in rec.seen], methods)
        self.assertTrue(all(a == EXPECTED for _, _, a in rec.seen), rec.seen)

    def test_agentgateway_catalog_export(self):
        mod = load_script("agentgateway-standalone-wiring/agentgateway-catalog-export.yml", "export.py")
        self.assertEqual(mod.PUSH, FRONT)
        with Recorder() as rec:
            mod.push("m 1\n", base=rec.base, job="j")
        self.assert_authenticated(rec, ["DELETE", "PUT"])

    def test_kagent_gateway_tools_check(self):
        mod = load_script("kagent-mdapi/kagent-gateway-tools-check.yml", "check.py")
        self.assertTrue(mod.PUSH.startswith(FRONT + "/metrics/job/"), mod.PUSH)
        with Recorder() as rec:
            mod.push("m 1\n", url=rec.base + "/metrics/job/j")
        self.assert_authenticated(rec, ["PUT"])

    def test_findmy_collector(self):
        stubs = {"paramiko": types.ModuleType("paramiko"), "findmy": types.ModuleType("findmy")}
        stubs["findmy"].AppleAccount = stubs["findmy"].FindMyAccessory = object
        mod = load_script("findmy/findmy-code-cm.yml", "collector.py", stubs)
        self.assertEqual(mod.PUSHGATEWAY, FRONT)
        with Recorder() as rec:
            mod.PUSHGATEWAY = rec.base
            mod.push_metrics(findmy_collector_extract_ok=1)
        self.assert_authenticated(rec, ["POST"])

    def test_lsrules_watch(self):
        mod = load_script("mac-rules/lsrules-watch-cm.yml", "lsrules_watch.py")
        self.assertEqual(mod.PUSHGATEWAY, FRONT)
        with Recorder() as rec:
            mod.PUSHGATEWAY = rec.base
            mod.push("m 1")
        self.assert_authenticated(rec, ["POST"])

    def test_kube_bench_push(self):
        """The shell push() run for real, with wget replaced by a recorder of its argv."""
        script = pod_spec(one("kube-bench/cronjob.yml", "CronJob"))["containers"][0]["args"][0]
        func = re.search(r"^( *)push\(\) \{\n.*?^\1\}\n", script, re.S | re.M)
        self.assertIsNotNone(func, "push() not found in the kube-bench script")
        with tempfile.TemporaryDirectory() as tmp:
            wget = Path(tmp, "wget")
            wget.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > "$ARGV_OUT"\n')
            wget.chmod(wget.stat().st_mode | stat.S_IEXEC)
            out = Path(tmp, "argv")
            env = {"PATH": tmp + os.pathsep + os.environ["PATH"], "ARGV_OUT": str(out),
                   ENV: PASSWORD, "PUSHGATEWAY": "http://pg.invalid", "NODE_NAME": "n1", "TMPDIR": tmp}
            body = func.group(0).replace("/tmp/", tmp + "/")
            subprocess.run(["sh", "-c", body + 'push "m 1"'], env=env, check=True)
            argv = out.read_text().splitlines()
        self.assertIn("Authorization: " + EXPECTED, argv)
        self.assertEqual(argv[-1], "http://pg.invalid/metrics/job/kube_bench/node/n1")
        self.assertNotIn(PASSWORD, "\n".join(argv))


class ThePasswordComesFromTheSecretStore(unittest.TestCase):
    def test_each_writer_reads_it_from_an_externalsecret_in_its_namespace(self):
        for bundle, pod_file, kind, name, container, es_file in WRITERS:
            with self.subTest(bundle=bundle):
                workload = one(f"{bundle}/{pod_file}", kind, name)
                (c,) = [c for c in pod_spec(workload)["containers"] if c["name"] == container]
                env = {e["name"]: e for e in c.get("env") or []}
                self.assertEqual(env[ENV]["valueFrom"]["secretKeyRef"], {"name": SECRET, "key": SECRET_KEY})
                for e in env.values():
                    if e["name"].startswith("PUSHGATEWAY"):
                        self.assertEqual(e["value"], FRONT)
                es = one(f"{bundle}/{es_file}", "ExternalSecret", SECRET)
                self.assertEqual(es["metadata"].get("namespace"), workload["metadata"].get("namespace"))
                self.assertEqual(es["spec"]["target"]["name"], SECRET)
                self.assertEqual(es["spec"]["secretStoreRef"], {"kind": "ClusterSecretStore", "name": "cm-akeyless"})
                self.assertEqual([(d["secretKey"], d["remoteRef"]["key"]) for d in es["spec"]["data"]],
                                 [(SECRET_KEY, AKEYLESS)])

    def test_it_is_the_item_the_front_checks(self):
        es = one("monitoring-vm/vmauth-pushgateway.yml", "ExternalSecret", "vmauth-pg-writer")
        self.assertEqual([d["remoteRef"]["key"] for d in es["spec"]["data"]], [AKEYLESS])


class KubeBenchMayReachTheFront(unittest.TestCase):
    def test_the_egress_fence_opens_8427_and_no_longer_9091(self):
        np = one("kube-bench/netpol.yml", "NetworkPolicy", "allow-egress-pushgateway")
        (rule,) = np["spec"]["egress"]
        self.assertEqual(rule["to"], [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "monitoring"}}}])
        self.assertEqual(rule["ports"], [{"protocol": "TCP", "port": 8427}])


class OffClusterPushesReachTheFront(unittest.TestCase):
    """pushgateway.mdapi.ch writes go to vmauth too (infra-o4i3m.5.2.1).

    The bpi-r4 router is the one off-cluster writer (~420 pushes/h, every curl with
    -K /etc/pushgw-writer.curl since openwrt !51). A second Ingress on the same host
    sends only /metrics/job/... to the front; the chart's Ingress keeps `/` for the LAN
    reads (GET /api/v1/metrics) and keeps owning the certificate, so the two never
    fight over the TLS Secret and nginx's longest-prefix match does the split.
    """

    HOST = "pushgateway.mdapi.ch"

    def chart_ingress(self) -> dict:
        return yaml.safe_load((ROOT / "monitoring-pushgateway/values.yml").read_text())["ingress"]

    def front_ingress(self) -> dict:
        return one("monitoring-vm/vmauth-pushgateway.yml", "Ingress", "pushgateway-write")

    def test_writes_on_the_lan_host_are_routed_to_the_front(self):
        ing = self.front_ingress()
        (rule,) = ing["spec"]["rules"]
        self.assertEqual(rule["host"], self.HOST)
        (path,) = rule["http"]["paths"]
        self.assertEqual((path["path"], path["pathType"]), ("/metrics/job", "Prefix"))
        self.assertEqual(path["backend"]["service"], {"name": "vmauth-pushgateway", "port": {"number": 8427}})
        self.assertEqual(ing["spec"]["ingressClassName"], "nginx")

    def test_it_keeps_the_lan_allowlist_and_the_body_limit(self):
        mine = self.front_ingress()["metadata"]["annotations"]
        chart = self.chart_ingress()["annotations"]
        for key in ("nginx.ingress.kubernetes.io/whitelist-source-range",
                    "nginx.ingress.kubernetes.io/proxy-body-size"):
            self.assertEqual(mine[key], chart[key], key)

    def test_it_reuses_the_chart_certificate_without_claiming_it(self):
        ing = self.front_ingress()
        self.assertNotIn("cert-manager.io/cluster-issuer", ing["metadata"]["annotations"])
        self.assertEqual(ing["spec"]["tls"],
                         [{"hosts": [self.HOST], "secretName": self.chart_ingress()["tls"][0]["secretName"]}])
        self.assertEqual(ing["metadata"]["namespace"], "monitoring")


if __name__ == "__main__":
    unittest.main()
