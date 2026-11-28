"""windmill-worker-heal-storage gets pods/exec PER NAMESPACE, never cluster-wide.

SA token audit 2026-11-10 (infra-p6q07.2): the heal-storage ClusterRole held pods/exec behind
a ClusterRoleBinding, so its token could exec into the privileged hostPID pods of kube-system,
rook-ceph, longhorn-system and harvester-system, i.e. root on every node. Its only exec caller
is longhorn_eio_autoheal.fs_probe, which only ever probes a pod that mounts a Longhorn volume.
Measured 2026-11-19: 44 namespaces run such a pod, 17 run privileged/hostPID pods, and only 4
(frigate, home-assistant, jump, tv) are on both lists.

So exec lives in its own ClusterRole, bound by one RoleBinding per Longhorn namespace
(windmill/rbac/heal-storage-exec.yml), and the admission policy heal-storage-exec-pin still pins
the command. Longhorn is the DEFAULT StorageClass, so a new tenant usually needs a binding. That
coverage cannot be read from manifests (helm charts make PVCs), so it is measured LIVE by
f/probe/rbac_hygiene (gauge rbac_heal_storage_exec_uncovered_namespaces, alert
RbacHealStorageExecUncovered). This test guards the direction that matters for security: no
cluster-wide exec, and never a binding into a platform namespace.
"""

import unittest

import fleetlib

STORAGE_ROLE = "windmill-worker-heal-storage"
EXEC_ROLE = "windmill-worker-heal-storage-exec"
SA = ("windmill", "windmill-worker-heal-storage")

# Namespaces whose pods are privileged or hostPID and mount no Longhorn volume (2026-11-19).
# An exec there is node root, and fs_probe never needs one.
PLATFORM = {
    "kube-system", "rook-ceph", "longhorn-system", "harvester-system", "cattle-system",
    "cattle-monitoring-system", "cattle-fleet-system", "cattle-fleet-local-system",
    "cephfs-kmod", "etcd-priority", "kube-bench", "akeyless-pg-bridge", "mirror",
    "ntppool", "opennic", "ups", "external-secrets",
}


def _docs():
    for rel in fleetlib.tracked_yaml():
        for doc in fleetlib.load_docs(rel):
            if isinstance(doc, dict):
                yield rel, doc


def _rules(docs, name):
    for _rel, doc in docs:
        if doc.get("kind") == "ClusterRole" and (doc.get("metadata") or {}).get("name") == name:
            return doc.get("rules") or []
    return None


def _grants_exec(rules):
    return any(("pods/exec" in r.get("resources", []) or "*" in r.get("resources", []))
               and r.get("apiGroups", [""]) in ([""], ["*"]) for r in rules or [])


def _bindings(docs, role):
    """(ClusterRoleBinding files, RoleBinding namespaces) of `role` naming the heal-storage SA."""
    crbs, namespaces = [], []
    for rel, doc in docs:
        if (doc.get("roleRef") or {}).get("name") != role:
            continue
        if not any((s.get("namespace"), s.get("name")) == SA for s in doc.get("subjects") or []):
            continue
        if doc.get("kind") == "ClusterRoleBinding":
            crbs.append(rel)
        elif doc.get("kind") == "RoleBinding":
            namespaces.append((doc.get("metadata") or {}).get("namespace"))
    return crbs, namespaces


class HealStorageExecPerNamespace(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = list(_docs())

    def test_cluster_wide_role_has_no_exec(self):
        rules = _rules(self.docs, STORAGE_ROLE)
        self.assertIsNotNone(rules, f"ClusterRole {STORAGE_ROLE} not found")
        self.assertFalse(_grants_exec(rules), f"{STORAGE_ROLE} is bound cluster-wide: no pods/exec")

    def test_exec_role_is_exactly_pods_exec(self):
        rules = _rules(self.docs, EXEC_ROLE)
        self.assertIsNotNone(rules, f"ClusterRole {EXEC_ROLE} not found")
        self.assertEqual([{"apiGroups": [""], "resources": ["pods/exec"],
                           "verbs": ["create", "get"]}], rules)

    def test_exec_role_never_bound_cluster_wide(self):
        crbs, _ = _bindings(self.docs, EXEC_ROLE)
        self.assertEqual([], crbs)

    def test_no_binding_into_a_platform_namespace(self):
        _, namespaces = _bindings(self.docs, EXEC_ROLE)
        self.assertEqual(set(), set(namespaces) & PLATFORM)
        self.assertEqual([], [n for n in namespaces if not n or n.startswith(("cattle-", "fleet-"))])

    def test_one_binding_per_namespace(self):
        _, namespaces = _bindings(self.docs, EXEC_ROLE)
        self.assertEqual(sorted(set(namespaces)), sorted(namespaces))

    def test_bindings_are_found(self):
        # An empty discovery would make the security checks above pass vacuously.
        _, namespaces = _bindings(self.docs, EXEC_ROLE)
        self.assertGreaterEqual(len(namespaces), 40)

    def test_detector_catches_cluster_wide_exec(self):
        self.assertTrue(_grants_exec([{"apiGroups": [""], "resources": ["pods/exec"]}]))
        self.assertTrue(_grants_exec([{"apiGroups": ["*"], "resources": ["*"]}]))
        self.assertFalse(_grants_exec([{"apiGroups": [""], "resources": ["pods"]}]))


if __name__ == "__main__":
    unittest.main()
