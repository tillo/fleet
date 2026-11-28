"""The active in-cluster scan (service-dast) enrols apps by discovery, not by a hand list.

infra-7rog.31.17 (2026-11-23): the hand list dast/service-targets.yml held 0 apps, so the
weekly run lasted 4 s and scanned nothing. The user's goal is ALL apps. service-discover.py,
the service-dast init container, lists every Ingress backend Service from the API and writes
`<svc>.<ns> <url>` for the scanners, aimed at the SERVICE (behind the WAF: an active scan
through ingress-nginx floods ModSecurity false positives).

What breaks the promise if it regresses:
  * an app left out silently: every Ingress backend counts, and the only ways out are an
    annotation that states why (`dast.mdapi.ch/skip` on the Ingress -- also edge-dast's --
    or `dast.mdapi.ch/active-skip` for the active scan only) and the short default-exclusion
    list (Keycloak and stateful-form apps), each with its reason;
  * the URL must be the in-cluster Service at the port the Ingress uses (a named port is
    resolved through the Service), https only when the Ingress says the backend speaks it;
  * the network fence decides what is attacked: only apps the pod can connect to are
    written, the rest are logged as unreachable, so enrolling an app = one egress rule;
  * nothing reachable must FAIL the pod: a scan of nothing would import as a clean week.
"""

from __future__ import annotations

import types
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import yaml

from tests.fleetlib import repo_root

CONFIGMAP = Path(repo_root()) / "dast" / "service-discover.yml"


def _guard(*_a, **_k):
    raise AssertionError("service-discover.py reached the network in a test")


def load() -> types.ModuleType:
    doc = yaml.safe_load(CONFIGMAP.read_text())
    mod = types.ModuleType("service_discover")
    exec(compile(doc["data"]["service-discover.py"], "service-discover.py", "exec"), mod.__dict__)
    mod.urllib = types.SimpleNamespace(request=types.SimpleNamespace(
        Request=urllib.request.Request, urlopen=_guard))
    return mod


D = load()


def ing(ns, svc, port, notes=None, default_backend=False):
    backend = {"service": {"name": svc, "port": port}}
    spec = ({"defaultBackend": backend} if default_backend else
            {"rules": [{"host": "h", "http": {"paths": [{"path": "/", "backend": backend}]}}]})
    return {"metadata": {"name": "i", "namespace": ns, "annotations": dict(notes or {})}, "spec": spec}


def svc(ns, name, *ports, kind="ClusterIP"):
    return {"metadata": {"name": name, "namespace": ns},
            "spec": {"type": kind, "ports": [{"name": n, "port": p} for n, p in ports]}}


class Targets(unittest.TestCase):
    def test_every_backend_once_at_its_service_url(self):
        got = D.targets([ing("app", "web", {"number": 8080}), ing("app", "web", {"number": 8080})],
                        [svc("app", "web", ("http", 8080))])
        self.assertEqual(got, [D.Target("web.app", "http://web.app.svc.cluster.local:8080")])

    def test_a_named_port_is_resolved_through_the_service(self):
        got = D.targets([ing("app", "web", {"name": "http"})], [svc("app", "web", ("http", 7007))])
        self.assertEqual([t.url for t in got], ["http://web.app.svc.cluster.local:7007"])

    def test_https_backend_keeps_its_scheme(self):
        notes = {"nginx.ingress.kubernetes.io/backend-protocol": "HTTPS"}
        got = D.targets([ing("app", "web", {"number": 443}, notes)], [svc("app", "web", ("https", 443))])
        self.assertEqual([t.url for t in got], ["https://web.app.svc.cluster.local:443"])

    def test_default_backend_counts(self):
        got = D.targets([ing("app", "web", {"number": 80}, default_backend=True)], [svc("app", "web", ("h", 80))])
        self.assertEqual([t.name for t in got], ["web.app"])

    def test_missing_or_external_services_are_dropped(self):
        got = D.targets([ing("app", "gone", {"number": 80}), ing("app", "ext", {"number": 80})],
                        [svc("app", "ext", ("h", 80), kind="ExternalName")])
        self.assertEqual(got, [])

    def test_sorted_by_name(self):
        got = D.targets([ing("b", "x", {"number": 1}), ing("a", "x", {"number": 1})],
                        [svc("a", "x", ("h", 1)), svc("b", "x", ("h", 1))])
        self.assertEqual([t.name for t in got], ["x.a", "x.b"])


class OptOut(unittest.TestCase):
    def test_both_annotations_opt_out(self):
        for key in ("dast.mdapi.ch/skip", "dast.mdapi.ch/active-skip"):
            got = D.targets([ing("app", "web", {"number": 80}, {key: "honeypot"})], [svc("app", "web", ("h", 80))])
            self.assertEqual(got, [], key)

    def test_an_opt_out_without_a_reason_fails(self):
        with self.assertRaises(ValueError):
            D.targets([ing("app", "web", {"number": 80}, {"dast.mdapi.ch/active-skip": " "})],
                      [svc("app", "web", ("h", 80))])

    def test_keycloak_and_stateful_forms_are_excluded_by_default(self):
        self.assertIn("keycloak", D.EXCLUDED_SERVICES)
        for ns in ("keycloak", "bootstrap", "nextcloud", "mail"):
            self.assertIn(ns, D.EXCLUDED_NAMESPACES)
            got = D.targets([ing(ns, "web", {"number": 80})], [svc(ns, "web", ("h", 80))])
            self.assertEqual(got, [], ns)
        got = D.targets([ing("tenant", "keycloak", {"number": 8080})], [svc("tenant", "keycloak", ("h", 8080))])
        self.assertEqual(got, [])
        for reason in list(D.EXCLUDED_SERVICES.values()) + list(D.EXCLUDED_NAMESPACES.values()):
            self.assertTrue(reason.strip())

    def test_first_enrolments_are_not_excluded(self):
        for ns in ("backstage", "bussola"):
            self.assertNotIn(ns, D.EXCLUDED_NAMESPACES)


class Run(unittest.TestCase):
    def run_with(self, ingresses, services, up):
        written = {}
        with mock.patch.object(D, "list_items", side_effect=[ingresses, services]), \
                mock.patch.object(D, "reachable", return_value=up), \
                mock.patch.object(D, "write_lines", side_effect=lambda p, ls: written.__setitem__(p, ls)), \
                mock.patch("builtins.print") as out:
            rc = D.run()
        return rc, written, " ".join(str(c.args[0]) for c in out.call_args_list)

    def test_writes_only_reachable_apps_and_reports_coverage(self):
        ings = [ing("a", "web", {"number": 80}), ing("b", "web", {"number": 80})]
        svcs = [svc("a", "web", ("h", 80)), svc("b", "web", ("h", 80))]
        rc, written, log = self.run_with(ings, svcs, {"web.a.svc.cluster.local:80"})
        self.assertEqual(rc, 0)
        self.assertEqual(written[D.OUT], ["web.a http://web.a.svc.cluster.local:80"])
        self.assertIn("unreachable web.b", log)
        self.assertIn("coverage: 2 apps enumerated, 1 reachable", log)

    def test_nothing_reachable_fails(self):
        rc, written, log = self.run_with([ing("a", "web", {"number": 80})], [svc("a", "web", ("h", 80))], set())
        self.assertEqual(rc, 1)
        self.assertNotIn(D.OUT, written)

    def test_the_hostport_probed_is_the_url_authority(self):
        t = D.Target("web.a", "https://web.a.svc.cluster.local:8443")
        self.assertEqual(D.authority(t), "web.a.svc.cluster.local:8443")


class Wiring(unittest.TestCase):
    def test_cronjob_runs_discovery_before_the_scanners(self):
        cj = yaml.safe_load((Path(repo_root()) / "dast" / "service-cronjob.yml").read_text())
        pod = cj["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        (init,) = pod["initContainers"]
        self.assertEqual(init["command"][-1], "/discover/service-discover.py")
        vols = {v["name"]: v for v in pod["volumes"]}
        self.assertIn("emptyDir", vols["targets"])
        self.assertEqual(vols["discover"]["configMap"]["name"], "dast-service-discover")
        self.assertFalse(pod["automountServiceAccountToken"])
        for c in pod["containers"]:
            self.assertNotIn("api-token", {m["name"] for m in c.get("volumeMounts", [])}, c["name"])

    def test_the_hand_list_is_gone(self):
        self.assertFalse((Path(repo_root()) / "dast" / "service-targets.yml").exists())


class EveryAppIsReported(unittest.TestCase):
    """The scanner loops read /targets/targets; nuclei (and anything else) reading its
    stdin must not swallow the remaining apps (the edge-dast 2026-11-10 incident)."""

    def run_container(self, name):
        import os
        import stat
        import subprocess
        import tempfile
        from tests.test_dast_scanners_scan_every_listed_target import FAKE_NUCLEI, FAKE_ZAP
        cj = yaml.safe_load((Path(repo_root()) / "dast" / "service-cronjob.yml").read_text())
        (script,) = [c["args"][0] for c in cj["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
                     if c["name"] == name]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for sub in ("bin", "wrk", "home", "targets"):
                (root / sub).mkdir()
            for tool, body in (("nuclei", FAKE_NUCLEI), ("zap-full-scan.py", FAKE_ZAP), ("zap-api-scan.py", FAKE_ZAP)):
                p = root / "bin" / tool
                p.write_text(body)
                p.chmod(p.stat().st_mode | stat.S_IEXEC)
            (root / "targets" / "targets").write_text(
                "a.ns http://a.ns.svc.cluster.local:80\nb.ns http://b.ns.svc.cluster.local:80\n"
                "c.ns http://c.ns.svc.cluster.local:80\n")
            script = script.replace("/targets/", f"{root}/targets/").replace("/zap/wrk", str(root / "wrk"))
            env = {**os.environ, "PATH": f"{root}/bin:/usr/bin:/bin", "HOME": str(root / "home"),
                   "WRK": str(root / "wrk")}
            p = subprocess.run(["bash", "-c", script], input="", capture_output=True, text=True, env=env, timeout=60)
            return p.returncode, p.stdout

    def test_each_scanner_reports_every_app(self):
        for name, tool in (("zap", "zap-full"), ("nuclei", "nuclei-http")):
            rc, out = self.run_container(name)
            self.assertEqual(rc, 0, out)
            for app in ("a.ns", "b.ns", "c.ns"):
                self.assertIn(f"-----BEGIN DAST REPORT {tool} {app}-----", out, name)


if __name__ == "__main__":
    unittest.main()
