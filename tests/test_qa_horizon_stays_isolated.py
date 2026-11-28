"""qa/horizon: the QA Horizon is the estate's image with nothing else from prod but the licence.

infra-7rog.41.12, user decision 2026-11-25: the prod Horizon LICENCE is the one prod value
allowed into the QA cluster (a named exception, qa/README.md). The vault master key is a
throwaway local Tink keyset, so the estate's ciphertrust:// hook stays unexercised and
nothing in QA may reach the prod CipherTrust Manager; the data is its own MongoDB with
synthetic data, never prod Horizon or prod Keycloak.

Each of these drifts silently (Horizon boots fine with any of them):
* a ciphertrust:// (or any) master-key URI would point QA at prod's KMS;
* a prod hostname (cm/horizon/idp.mdapi.ch) or a prod Service FQDN in an env value;
* an ExternalSecret or a Secret in git: QA has no ESO, and the licence must arrive from
  the seat only (~/src/spot-qa/horizon-qa-secrets.py);
* the tracer-off control drifting from the instrumented copy, so the overhead is a lie.
"""

from __future__ import annotations

import re
import unittest

import yaml

from fleetlib import repo_root

BUNDLE = repo_root() / "qa" / "horizon"
PROD_HOSTS = ("cm.mdapi.ch", "horizon.mdapi.ch", "idp.mdapi.ch", "keycloak.mdapi.ch", "stream.mdapi.ch")


def docs() -> list[dict]:
    out = []
    for f in sorted(BUNDLE.glob("*.yml")):
        out += [d for d in yaml.safe_load_all(f.read_text()) if d]
    return out


def deployment(name: str) -> dict:
    (d,) = [d for d in docs() if d["kind"] == "Deployment" and d["metadata"]["name"] == name]
    return d


def container(name: str) -> dict:
    return deployment(name)["spec"]["template"]["spec"]["containers"][0]


def env(name: str) -> dict:
    return {e["name"]: e for e in container(name).get("env", [])}


class QaHorizonStaysIsolated(unittest.TestCase):
    def test_no_secret_or_externalsecret_is_shipped_from_git(self):
        kinds = {d["kind"] for d in docs()}
        self.assertNotIn("Secret", kinds)
        self.assertNotIn("ExternalSecret", kinds)

    def test_no_master_key_uri_and_no_kms_credentials(self):
        for app in ("horizon", "horizon-off"):
            e = env(app)
            self.assertNotIn("VAULT_TINK_MASTER_KEY_URI", e, app)
            self.assertNotIn("VAULT_TINK_CREDENTIALS_PATH", e, app)
            self.assertEqual(e["VAULT_TYPE"]["value"], "tink", app)

    def test_licence_is_the_only_value_read_from_the_exception_secret(self):
        for app in ("horizon", "horizon-off"):
            refs = [(n, e["valueFrom"]["secretKeyRef"]["name"]) for n, e in env(app).items()
                    if "secretKeyRef" in e.get("valueFrom", {})]
            self.assertIn(("LICENSE", "horizon-license"), refs, app)
            others = {s for n, s in refs if n != "LICENSE"}
            self.assertEqual(others, {"horizon-qa"}, app)

    def test_no_prod_hostname_and_no_other_namespace_in_any_value(self):
        for f in BUNDLE.glob("*.yml"):
            for line in f.read_text().splitlines():
                if line.lstrip().startswith("#"):
                    continue
                for host in PROD_HOSTS:
                    self.assertNotIn(host, line, f.name)
                for ns in re.findall(r"\.([a-z0-9-]+)\.svc(?:\.cluster\.local)?\b", line):
                    self.assertIn(ns, ("horizon", "iast"), line)

    def test_runs_the_estates_image_with_the_switch_on_only_in_the_instrumented_copy(self):
        on, off = container("horizon"), container("horizon-off")
        self.assertTrue(on["image"].startswith("zot.mdapi.ch/mdapi/horizon-ciphertrust:"))
        self.assertIn("@sha256:", on["image"])
        self.assertEqual(on["image"], off["image"])
        self.assertEqual(env("horizon")["DD_IAST_ENABLED"]["value"], "true")
        self.assertFalse(any(n.startswith("DD_") for n in env("horizon-off")))

    def test_the_off_copy_differs_only_by_the_tracer_and_its_own_identity(self):
        on, off = env("horizon"), env("horizon-off")
        own = {"MONGODB_URI", "AKKA_ACTOR_SYSTEM"}
        tracer = {n for n in on if n.startswith("DD_")}
        self.assertEqual(set(on) - tracer, set(off))
        for n in set(off) - own:
            self.assertEqual(on[n], off[n], n)
        self.assertNotEqual(on["MONGODB_URI"]["value"], off["MONGODB_URI"]["value"])
        self.assertEqual(container("horizon")["resources"]["requests"], container("horizon-off")["resources"]["requests"])

    def test_no_service_account_token_reaches_horizon(self):
        for app in ("horizon", "horizon-off", "horizon-mongodb"):
            self.assertIs(deployment(app)["spec"]["template"]["spec"]["automountServiceAccountToken"], False, app)


if __name__ == "__main__":
    unittest.main()
