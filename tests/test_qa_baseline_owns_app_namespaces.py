"""qa/qa-baseline is the ONE owner of every QA app namespace; app bundles declare no labels.

Measured 2026-11-25 (infra-7rog.41.12/.11), both directions:
* qa/horizon WITHOUT namespaceLabels: its first install reset namespace `horizon` (made and
  labelled by qa-baseline one MR earlier) to {kubernetes.io/metadata.name, name}; PSA and
  mdapi.ch/iast-tracer were gone and the sink refused the tracer. A one-off at first
  install, healed by a GitRepo force-sync plus a restart of the traced pods.
* qa/horizon WITH namespaceLabels equal to qa-baseline's (fleet !2898): Fleet set exactly
  that list on every deploy, stripping the helm labels (app.kubernetes.io/managed-by,
  objectset hash) qa-baseline's release owns -> qa-baseline "Modified", and every bundle that
  dependsOn it waited forever ("waiting for dependent bundle(s)").
So one owner: a qa/ app bundle may not declare namespaceLabels for a namespace qa-baseline
declares.
"""

from __future__ import annotations

import unittest

import yaml

from fleetlib import repo_root

QA = repo_root() / "qa"


def baseline_namespaces() -> set[str]:
    docs = yaml.safe_load_all((QA / "qa-baseline" / "namespaces.yml").read_text())
    return {d["metadata"]["name"] for d in docs if d and d["kind"] == "Namespace"}


class QaBaselineOwnsAppNamespaces(unittest.TestCase):
    def test_no_app_bundle_labels_a_namespace_qa_baseline_owns(self):
        owned = baseline_namespaces()
        self.assertIn("horizon", owned)  # the discovery itself works
        for f in sorted(QA.glob("*/fleet.yaml")):
            if f.parent.name == "qa-baseline":
                continue
            doc = yaml.safe_load(f.read_text()) or {}
            ns = doc.get("namespace") or doc.get("defaultNamespace")
            with self.subTest(bundle=f.parent.name):
                self.assertFalse(ns in owned and "namespaceLabels" in doc,
                                 f"{f.parent.name} labels namespace {ns}, which qa-baseline owns")


if __name__ == "__main__":
    unittest.main()
