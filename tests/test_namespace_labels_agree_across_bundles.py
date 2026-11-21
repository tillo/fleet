#!/usr/bin/env python3
"""Bundles that deploy into the same namespace declare the same namespaceLabels.

WHY THIS FILE EXISTS (2026-11-04, infra-r9mzd.14)
Since the Rancher 2.15.2 / Fleet v0.16 upgrade (2026-10-29), every bundle that deploys
into a namespace resets that namespace's labels to ITS OWN `namespaceLabels`. Two bundles
sharing a namespace with different lists therefore flip the labels on every deploy of
either one. It happened twice on the day the PSA labels were declared: a gitlab deploy
stripped bootstrap's Pod Security labels, then a jump-rbac deploy stripped jump's
`enforce: privileged`, the label the seat's own pod depends on. Nothing errored either
time; the namespace just changed under the next pod admission.

WHAT IS ASSERTED
Among the bundles that declare `namespaceLabels` for a namespace, every list is equal.
(Bundles that declare no namespaceLabels are not compared: they leave labels alone.)
"""

from __future__ import annotations

import collections
import unittest

import yaml

import fleetlib


def bundle_namespaces() -> dict[str, list[tuple[str, dict]]]:
    """namespace -> [(fleet.yaml path, namespaceLabels)] for bundles that declare labels."""
    out: dict[str, list[tuple[str, dict]]] = collections.defaultdict(list)
    for path in fleetlib.tracked_files():
        if not path.endswith(("fleet.yaml", "fleet.yml")) or path.startswith("gitlab/charts/"):
            continue
        doc = yaml.safe_load((fleetlib.repo_root() / path).read_text(encoding="utf-8")) or {}
        labels = doc.get("namespaceLabels")
        ns = doc.get("namespace") or doc.get("defaultNamespace")
        if labels and ns:
            out[ns].append((path, labels))
    return out


class NamespaceLabelsAgree(unittest.TestCase):
    def test_bundles_sharing_a_namespace_declare_identical_labels(self):
        for ns, decls in sorted(bundle_namespaces().items()):
            if len(decls) < 2:
                continue
            with self.subTest(namespace=ns):
                first_path, first = decls[0]
                for path, labels in decls[1:]:
                    self.assertEqual(
                        first, labels,
                        "%s and %s both deploy into namespace %r with different "
                        "namespaceLabels; Fleet v0.16 resets the namespace to whichever "
                        "deployed last" % (first_path, path, ns))

    def test_the_jump_namespace_is_seen_twice(self):
        # guards the discovery itself: if this stops finding jump + jump-rbac, the
        # assertion above silently compares nothing for the seat's own namespace
        self.assertGreaterEqual(len(bundle_namespaces().get("jump", [])), 2)


if __name__ == "__main__":
    unittest.main()
