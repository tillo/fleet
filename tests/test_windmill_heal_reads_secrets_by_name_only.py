"""windmill-worker-heal reads Secrets BY NAME, in the namespaces that hold them, never cluster-wide.

SA token audit 2026-11-10 (infra-p6q07.2): `windmill-worker-heal-secrets` was a ClusterRole
`secrets: get` behind a ClusterRoleBinding, so any `heal`-tagged job (an author picks the
tag) could fetch jump/jump-seat-direct-token or kube-system/bpi-r4-seat-token, two
long-lived cluster-admin tokens. Every Secret a heal script actually reads is known:

  restic_snapshot_freshness   each VolSync ReplicationSource's spec.restic.repository
  rgw_backup_bucket_grant     each CNPG bucket's OBC owner Secret (named after the OBC)
  k8s_orphan_audit            each VM's cloud-init secretRef / networkDataSecretRef
  ha_upgrade_rollback, horizon_lic001_heal, openbao_failover_check    fixed names (FIXED)

So the role now lists resourceNames, and one RoleBinding per namespace grants it. The three
families are derived HERE from the manifests that create them: a new ReplicationSource,
OBC or VM that is not granted fails this test at MR time instead of 403'ing at 03:00.
The check runs both ways, so a grant nothing needs fails as well.
"""

import os
import unittest

import fleetlib

ROLE = "windmill-worker-heal-secrets"
HEAL_SA = ("windmill", "windmill-worker-heal")

# Read by name, not derivable from a manifest kind. Each entry names its reader.
FIXED = {
    ("home-assistant", "home-assistant-env-secret"),  # ha_upgrade_rollback
    ("evertrust-mcp", "horizon-mcp-secrets"),         # horizon_lic001_heal
    ("openbao", "openbao-drill-check"),               # openbao_failover_check
    # k8s_orphan_audit: the rancher-mgmt VMs run on prod but are not Fleet-managed.
    ("rancher-mgmt", "mgmt-1-cloudinit"),
    ("rancher-mgmt", "mgmt-2-cloudinit"),
    ("rancher-mgmt", "mgmt-3-cloudinit"),
}
# Never grantable to a job, whatever a manifest says.
FORBIDDEN_NAMES = {"jump-seat-direct-token", "bpi-r4-seat-token"}
FORBIDDEN_NAMESPACES = {"kube-system", "cattle-system", "fleet-system", "cattle-fleet-system",
                        "external-secrets", "windmill"}


def _bundle_namespace(rel):
    """namespace a manifest lands in when it does not say: its bundle's (default)Namespace."""
    best = None
    for d, marker in fleetlib.bundles():
        if (d == "" or rel.startswith(d + "/")) and (best is None or len(d) > len(best[0])):
            best = (d, marker)
    spec = fleetlib.bundle_spec(best[1]) if best else {}
    return spec.get("namespace") or spec.get("defaultNamespace") or "default"


def _cloudinit_refs(vm):
    vols = (((vm.get("spec") or {}).get("template") or {}).get("spec") or {}).get("volumes") or []
    for vol in vols:
        for key in ("cloudInitNoCloud", "cloudInitConfigDrive"):
            for ref in ("secretRef", "networkDataSecretRef"):
                name = ((vol.get(key) or {}).get(ref) or {}).get("name")
                if name:
                    yield name


def required_from(docs):
    """{(namespace, secret)} the heal scripts read, from (rel, doc, namespace) triples."""
    out = set()
    for _rel, doc, ns in docs:
        kind, meta = doc.get("kind"), doc.get("metadata") or {}
        if kind == "ReplicationSource":
            repo = ((doc.get("spec") or {}).get("restic") or {}).get("repository")
            if repo:
                out.add((ns, repo))
        elif kind == "ObjectBucketClaim" and meta.get("name", "").startswith("cnpg-"):
            out.add((ns, meta["name"]))
        elif kind == "VirtualMachine":
            out.update((ns, s) for s in _cloudinit_refs(doc))
    return out


def _heal_subject(binding):
    return any((s.get("namespace"), s.get("name")) == HEAL_SA for s in binding.get("subjects") or [])


def grants_from(docs):
    """(resourceNames of ROLE, namespaces it is bound in, ClusterRoleBindings of it)."""
    names, namespaces, cluster_wide = None, set(), []
    for rel, doc, ns in docs:
        kind, meta = doc.get("kind"), doc.get("metadata") or {}
        if kind == "ClusterRole" and meta.get("name") == ROLE:
            names = set()
            for rule in doc.get("rules") or []:
                if "secrets" in rule.get("resources", []):
                    names.update(rule.get("resourceNames") or ["*"])
        ref = doc.get("roleRef") or {}
        if ref.get("name") == ROLE and _heal_subject(doc):
            if kind == "ClusterRoleBinding":
                cluster_wide.append(rel)
            elif kind == "RoleBinding":
                namespaces.add(ns)
    return names, namespaces, cluster_wide


def _all_docs():
    for rel in fleetlib.tracked_yaml():
        default_ns = None
        for doc in fleetlib.load_docs(rel):
            ns = (doc.get("metadata") or {}).get("namespace")
            if not ns:
                default_ns = default_ns or _bundle_namespace(rel)
                ns = default_ns
            yield rel, doc, ns


class HealSecretsByName(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        docs = list(_all_docs())
        cls.required = required_from(docs) | FIXED
        cls.names, cls.namespaces, cls.cluster_wide = grants_from(docs)

    def test_role_exists_and_names_every_secret(self):
        self.assertIsNotNone(self.names, f"ClusterRole {ROLE} not found")
        self.assertNotIn("*", self.names, "secrets rule without resourceNames = every Secret")

    def test_never_bound_cluster_wide(self):
        self.assertEqual([], self.cluster_wide)

    def test_every_read_is_granted(self):
        granted = {(ns, n) for ns in self.namespaces for n in self.names}
        self.assertEqual(set(), self.required - granted)

    def test_nothing_unneeded_is_granted(self):
        self.assertEqual(set(), self.names - {n for _, n in self.required})
        self.assertEqual(set(), self.namespaces - {ns for ns, _ in self.required})

    def test_admin_tokens_and_platform_namespaces_stay_out(self):
        self.assertEqual(set(), self.names & FORBIDDEN_NAMES)
        self.assertEqual(set(), self.namespaces & FORBIDDEN_NAMESPACES)

    def test_derivation_sees_the_families(self):
        # Empty discovery would make every check above pass vacuously.
        derived = self.required - FIXED
        for family in ("volsync-", "cnpg-"):
            self.assertTrue(any(n.startswith(family) for _, n in derived), family)

    def test_detects_a_cluster_wide_grant(self):
        docs = [("x.yml", {"kind": "ClusterRole", "metadata": {"name": ROLE},
                           "rules": [{"resources": ["secrets"], "verbs": ["get"]}]}, "windmill"),
                ("x.yml", {"kind": "ClusterRoleBinding", "metadata": {"name": ROLE},
                           "roleRef": {"name": ROLE},
                           "subjects": [{"namespace": HEAL_SA[0], "name": HEAL_SA[1]}]}, "windmill")]
        names, _, cluster_wide = grants_from(docs)
        self.assertEqual({"*"}, names)
        self.assertEqual(["x.yml"], cluster_wide)


if __name__ == "__main__":
    os.chdir(fleetlib.repo_root())
    unittest.main()
