"""A bundle pinned to a namespace SILENTLY DROPS cluster-scoped resources.

The incident this encodes (tv/flaresolverr-netpol.yml, 2026-09-02): a cluster-scoped
GlobalNetworkPolicy was committed to the `tv` bundle, whose fleet.yaml declares
`namespace: tv`. Fleet applied the commit, reported the bundle **1/1 READY**, and the
policy simply never appeared in the cluster. Nothing errored. A green bundle is not proof
a resource landed.

`namespace:` (unlike `defaultNamespace:`) FORCES every resource in the bundle into that
namespace, which a cluster-scoped kind cannot be. The fix is to move the resource to a
bundle without `namespace:` pinned, or use the namespaced equivalent -- Calico's
namespaced NetworkPolicy takes the identical rule syntax, which is what tv/ ended up
doing.
"""

import unittest

import fleetlib

# Cluster-scoped kinds we actually deploy or plausibly would. Add to this list rather
# than weakening the test.
CLUSTER_SCOPED = {
    "Namespace", "ClusterRole", "ClusterRoleBinding", "CustomResourceDefinition",
    "PriorityClass", "StorageClass", "ClusterIssuer", "GlobalNetworkPolicy",
    "GlobalNetworkSet", "ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding",
    "ValidatingWebhookConfiguration", "MutatingWebhookConfiguration", "APIService",
    "PersistentVolume", "IngressClass", "RuntimeClass", "ClusterSecretStore",
    "VolumeSnapshotClass", "CSIDriver", "ClusterRoleTemplateBinding", "ClusterPolicy",
    "NodeFeatureRule", "ClusterIssuerConfig",
}


class NamespacedBundleClusterScopedTest(unittest.TestCase):
    def test_no_cluster_scoped_resource_in_a_namespace_pinned_bundle(self):
        pinned = []
        violations = []

        for bundle_dir, marker in fleetlib.bundles():
            spec = fleetlib.bundle_spec(marker)
            ns = spec.get("namespace")
            if not ns:
                continue
            pinned.append(bundle_dir)
            for rel in fleetlib.files_under(bundle_dir):
                for doc in fleetlib.load_docs(rel):
                    kind = doc.get("kind")
                    if kind in CLUSTER_SCOPED:
                        name = (doc.get("metadata") or {}).get("name", "<unnamed>")
                        violations.append(
                            f"{rel}: {kind}/{name} sits in a bundle pinned to "
                            f"namespace={ns!r} ({marker}) -- Fleet will drop it silently "
                            f"and still report the bundle Ready"
                        )

        # ⛔ An empty selection is NOT a pass. If this ever stops finding pinned bundles,
        # the discovery is broken (e.g. only looking at fleet.yaml, missing fleet.yml)
        # and the test would be certifying nothing.
        self.assertGreater(
            len(pinned), 0,
            "no namespace-pinned bundles found at all -- bundle discovery is broken, "
            "this test is not actually checking anything",
        )
        self.assertEqual([], violations, "\n" + "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
