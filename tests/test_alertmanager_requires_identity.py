"""vmalertmanager-mdapi must require a credential, and every caller must send one.

infra-o4i3m.5.1 (2026-11-16): any pod could POST /api/v2/silences on
vmalertmanager-mdapi:9093 with no credential, i.e. mute every page before acting.
The fix is Alertmanager's own web config (basic auth, one user per client, hashes
from Akeyless via ESO), and it has three traps that each fail SILENTLY or LOUDLY:

* The operator keeps the liveness/readiness probes on plain HTTP GET /-/healthy.
  Under basic auth those answer 401 and the container is killed in a loop (measured
  on a scratch VMAlertmanager, operator v0.75.2). Probes must be tcpSocket.
* The config-reloader POSTs /-/reload with no credential: every config change would
  401 and AM would keep paging with the OLD routes, with nothing failing visibly.
  The reload URL carries the reloader user, expanded from a Secret-backed env var.
* The operator's self-scrape would 401 and the meta-monitoring.alertmanager rules
  would go blind.

And a caller that is not given a credential is a lost page path, so every tracked
file that names the in-cluster AM URL must be one this test knows sends auth.
"""

from __future__ import annotations

import ast
import base64
import os
import unittest
from unittest import mock

import yaml

from tests.fleetlib import repo_root, tracked_files

AM_HOST = "vmalertmanager-mdapi.monitoring"
WEB_CONFIG_SECRET = "vmalertmanager-web"

## Files that name the in-cluster AM URL and are known to authenticate (or are not
## callers at all). A new caller must be added here only once it sends a credential.
KNOWN_CALLERS = {
    "monitoring-vm/vmalert.yml": "VMAlert notifier basicAuth",
    "monitoring-cribl-am-relay/relay.yml": "am_request() Basic header",
    "monitoring-statuspage-adapter/adapter-cm.yml": "am_request() Basic header",
    "monitoring-vm/vmalertmanager.yml": "the server itself",
}


def load_docs(rel: str) -> list[dict]:
    return [d for d in yaml.safe_load_all((repo_root() / rel).read_text()) if d]


def doc_of_kind(rel: str, kind: str, name: str | None = None) -> dict:
    for d in load_docs(rel):
        if d.get("kind") == kind and (name is None or d["metadata"]["name"] == name):
            return d
    raise AssertionError(f"no {kind} {name or ''} in {rel}")


def am_spec() -> dict:
    return doc_of_kind("monitoring-vm/vmalertmanager.yml", "VMAlertmanager", "mdapi")["spec"]


def embedded_python(rel: str, cm_name: str) -> str:
    cm = doc_of_kind(rel, "ConfigMap", cm_name)
    (src,) = [v for k, v in cm["data"].items() if k.endswith(".py")]
    return src


def am_request_fn(src: str):
    """Compile ONLY the am_request helper out of an embedded script (the scripts
    start servers at import time)."""
    tree = ast.parse(src)
    (fn,) = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "am_request"]
    ns: dict = {}
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    exec(compile(ast.Module(body=[*imports, fn], type_ignores=[]), "<am_request>", "exec"), ns)
    return ns["am_request"]


class AlertmanagerServerRequiresIdentity(unittest.TestCase):
    def test_web_config_file_is_the_mounted_secret(self):
        spec = am_spec()
        self.assertIn(WEB_CONFIG_SECRET, spec.get("secrets", []))
        self.assertEqual(
            spec.get("extraArgs", {}).get("web.config.file"),
            f"/etc/vm/secrets/{WEB_CONFIG_SECRET}/web.yml",
        )

    def test_probes_do_not_need_a_credential(self):
        spec = am_spec()
        for probe in ("livenessProbe", "readinessProbe"):
            self.assertIn("tcpSocket", spec.get(probe, {}), probe)
            self.assertNotIn("httpGet", spec.get(probe, {}), probe)

    def test_config_reloader_authenticates(self):
        spec = am_spec()
        url = spec.get("configReloaderExtraArgs", {}).get("reload-url", "")
        self.assertRegex(url, r"^http://reloader:\$\(AM_RELOAD_PASSWORD\)@127\.0\.0\.1:9093/-/reload$")
        (reloader,) = [c for c in spec.get("containers", []) if c["name"] == "config-reloader"]
        env = {e["name"]: e for e in reloader.get("env", [])}
        self.assertEqual(env["AM_RELOAD_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"], WEB_CONFIG_SECRET)

    def test_self_scrape_authenticates(self):
        eps = am_spec().get("serviceScrapeSpec", {}).get("endpoints", [])
        web = [e for e in eps if e.get("port") == "http"]
        self.assertTrue(web and all("basicAuth" in e for e in web))

    def test_ui_ingress_passes_the_browsers_basic_header(self):
        ## auth-response-headers: Authorization replaces the browser's header with
        ## oauth2-proxy-admin's (empty), so the UI would loop on 401 forever.
        ing = doc_of_kind("monitoring-vm/vmalertmanager-ing.yml", "Ingress", "vmalertmanager")
        self.assertNotIn("nginx.ingress.kubernetes.io/auth-response-headers", ing["metadata"]["annotations"])


class EveryCallerSendsACredential(unittest.TestCase):
    def test_vmalert_notifier_has_basic_auth(self):
        notifier = doc_of_kind("monitoring-vm/vmalert.yml", "VMAlert")["spec"]["notifier"]
        self.assertIn("basicAuth", notifier)
        self.assertEqual(notifier["basicAuth"]["password"]["name"], "vmalertmanager-client-vmalert")

    def assert_helper_sends_basic(self, rel: str, cm: str):
        am_request = am_request_fn(embedded_python(rel, cm))
        with mock.patch.dict(os.environ, {"AM_USERNAME": "u", "AM_PASSWORD": "p"}):
            req = am_request("http://x/api/v2/alerts", data=b"[]", method="POST")
        self.assertEqual(req.get_header("Authorization"), "Basic " + base64.b64encode(b"u:p").decode())
        self.assertEqual(req.get_method(), "POST")

    def test_cribl_relay_sends_basic(self):
        self.assert_helper_sends_basic("monitoring-cribl-am-relay/relay.yml", "cribl-am-relay-code")

    def test_statuspage_adapter_sends_basic(self):
        self.assert_helper_sends_basic("monitoring-statuspage-adapter/adapter-cm.yml", "statuspage-adapter")

    def test_no_unknown_caller(self):
        callers = {f for f in tracked_files() if f.endswith((".yml", ".yaml", ".py")) and not f.startswith("tests/")
                   and AM_HOST in (repo_root() / f).read_text(errors="ignore")}
        self.assertEqual(callers - set(KNOWN_CALLERS), set(), "new AM caller: give it a credential, then list it")


if __name__ == "__main__":
    unittest.main()
