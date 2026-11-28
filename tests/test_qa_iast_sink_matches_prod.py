"""The QA iast-sink runs the SAME sink as prod, differing only where QA must.

infra-7rog.41.2 (2026-11-23): a Fleet bundle carries only files inside its own
directory -- `fleet apply` refuses a kustomize `../` resource and a symlink out of the
bundle -- so qa/iast-sink holds a copy of iast-sink's Deployment and Service. A copy
drifts silently: Renovate bumps one pin, someone fixes a probe in one file, and QA then
measures a sink prod does not run. This pins the copy to the original, field by field,
with the deliberate differences named:

  * QA has no priorityClassName (mdapi-low exists only on prod);
  * QA does not forward spans (no OpenObserve there): IAST_SINK_OTLP_ENDPOINT absent.
"""
from __future__ import annotations

import copy
import unittest

import yaml

from tests.fleetlib import repo_root

PROD, QA = repo_root() / "iast-sink", repo_root() / "qa" / "iast-sink"
QA_ONLY_DROPPED_ENV = {"IAST_SINK_OTLP_ENDPOINT"}


def doc(path) -> dict:
    return yaml.safe_load(path.read_text())


def qa_shape(prod_deploy: dict) -> dict:
    """What the QA Deployment must be, derived from prod's."""
    want = copy.deepcopy(prod_deploy)
    pod = want["spec"]["template"]["spec"]
    pod.pop("priorityClassName", None)
    for c in pod["containers"]:
        c["env"] = [e for e in c.get("env", []) if e["name"] not in QA_ONLY_DROPPED_ENV]
    return want


class QaIastSinkMatchesProd(unittest.TestCase):
    def test_deployment_is_prods_minus_the_named_differences(self):
        self.assertEqual(doc(QA / "iast-sink-deploy.yml"),
                         qa_shape(doc(PROD / "iast-sink-deploy.yml")))

    def test_service_is_identical(self):
        self.assertEqual(doc(QA / "iast-sink-svc.yml"), doc(PROD / "iast-sink-svc.yml"))

    def test_qa_sink_forwards_nowhere(self):
        env = doc(QA / "iast-sink-deploy.yml")["spec"]["template"]["spec"]["containers"][0]["env"]
        self.assertFalse({e["name"] for e in env} & QA_ONLY_DROPPED_ENV)


if __name__ == "__main__":
    unittest.main()
