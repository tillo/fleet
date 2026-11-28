"""keycloak-config-cli import files may not ungate a client, nor own clients they do not list.

infra-u3cv: the portal sign-out ends at Keycloak end-session with
post_logout_redirect_uri=https://start.mdapi.ch/, which only works while the client
oauth2-proxy-portal lists that URI in `post.logout.redirect.uris`. It was set by hand
(2026-10-21) and lived only in the live realm, so a rebuild would silently 400 the sign-out.

Measured 2026-11-11 against a throwaway Keycloak seeded from a partial export: a kcc file
listing the client with ONLY `attributes` REMOVED its `authenticationFlowBindingOverrides`
(the kc-gate 'browser gated' flow), i.e. it silently ungated the portal. Nothing errored.
Hence: every client a kcc file lists must declare its flow overrides, and kcc must never
delete a client the file does not list (other clients are managed by hand / kc-gate).
"""

from __future__ import annotations

import functools
import unittest

import yaml

from tests.fleetlib import load_docs, repo_root, tracked_yaml

KCC_IMAGE = "adorsys/keycloak-config-cli"
PORTAL = "oauth2-proxy-portal"


@functools.lru_cache(maxsize=1)
def docs_naming_kcc():
    """Every document in a tracked YAML file that mentions the kcc image or a ConfigMap.
    A cheap text prefilter first, so the 2000-file scan stays fast."""
    out = []
    for rel in tracked_yaml():
        raw = (repo_root() / rel).read_text(encoding="utf-8", errors="replace")
        if KCC_IMAGE in raw or "kind: ConfigMap" in raw:
            out.extend((rel, d) for d in load_docs(rel))
    return tuple(out)


def pod_spec(doc):
    """The pod spec of a Job or CronJob, else None."""
    spec = doc.get("spec") or {}
    if doc.get("kind") == "CronJob":
        spec = (spec.get("jobTemplate") or {}).get("spec") or {}
    elif doc.get("kind") != "Job":
        return None
    return (spec.get("template") or {}).get("spec") or {}


def runs_kcc(pod):
    return any(KCC_IMAGE in (c.get("image") or "") for c in pod.get("containers") or [])


def kcc_workloads():
    """(rel, pod spec) for every Job/CronJob whose container runs keycloak-config-cli."""
    pods = ((rel, pod_spec(doc)) for rel, doc in docs_naming_kcc())
    return [(rel, pod) for rel, pod in pods if pod is not None and runs_kcc(pod)]


def configmaps():
    return {(doc.get("metadata") or {}).get("name"): doc
            for _, doc in docs_naming_kcc() if doc.get("kind") == "ConfigMap"}


def kcc_env(pod):
    c = next(c for c in pod["containers"] if KCC_IMAGE in c.get("image", ""))
    return {e["name"]: e.get("value") for e in c.get("env") or []}


def import_realms(pod):
    """Every realm document the workload mounts from a ConfigMap."""
    cms = configmaps()
    realms = []
    for vol in pod.get("volumes") or []:
        name = (vol.get("configMap") or {}).get("name")
        if not name:
            continue
        if name not in cms:
            raise AssertionError(f"kcc mounts ConfigMap {name!r}, which no tracked file defines")
        for key, body in (cms[name].get("data") or {}).items():
            realms.append((f"{name}/{key}", yaml.safe_load(body)))
    return realms


class KeycloakConfigCliImports(unittest.TestCase):
    def setUp(self):
        self.workloads = kcc_workloads()
        self.assertTrue(self.workloads, "measured NOTHING: no keycloak-config-cli workload found")

    def test_every_listed_client_declares_its_flow_overrides(self):
        for rel, pod in self.workloads:
            for src, realm in import_realms(pod):
                for client in realm.get("clients") or []:
                    with self.subTest(file=rel, src=src, client=client.get("clientId")):
                        self.assertIn("authenticationFlowBindingOverrides", client,
                                      "kcc strips an undeclared override: the client loses its "
                                      "kc-gate flow and is open to every realm user")

    def test_kcc_never_deletes_unlisted_clients(self):
        for rel, pod in self.workloads:
            with self.subTest(file=rel):
                env = kcc_env(pod)
                self.assertEqual(env.get("IMPORT_MANAGED_CLIENT"), "no-delete")
                self.assertNotEqual(env.get("IMPORT_REMOTESTATE_ENABLED"), "false")

    def test_portal_logout_uri_and_gate_are_codified(self):
        portal = [c for _, pod in self.workloads for _, realm in import_realms(pod)
                  if realm.get("realm") == "tilloch"
                  for c in realm.get("clients") or [] if c.get("clientId") == PORTAL]
        self.assertEqual(len(portal), 1, f"{PORTAL} must be declared exactly once")
        client = portal[0]
        uris = client["attributes"]["post.logout.redirect.uris"].split("##")
        self.assertIn("https://start.mdapi.ch/", uris)
        self.assertEqual(client["authenticationFlowBindingOverrides"], {"browser": "browser gated"})


if __name__ == "__main__":
    unittest.main()
