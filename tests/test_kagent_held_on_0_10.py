"""kagent stays on the 0.10 line until a Substrate migration is designed.

kagent 1.0.0 is a different product, not a version bump: agents run as Substrate
Actors (gVisor/microVM workers, a root atelet DaemonSet with host /dev mounted,
a pod-certificate CA with no renewal), the bundled Postgres is gone (external DB
with owner/readwrite logins), and the API group moved kagent.dev -> api.kagent.dev.
Renovate proposed it as an ordinary major: the CRD chart auto-merged (fleet
eef6965b, 2026-11-09), the controller chart failed fleet-validate (!2133, job
179748). User decision 2026-11-10: hold on 0.10.

Two guards: Renovate never proposes 1.x for either chart, and the two charts sit
on the same minor line, so a CRD chart can never again run ahead of its
controller.
"""

from __future__ import annotations

import json
import re
import unittest

import yaml

from tests.fleetlib import repo_root

CHARTS = (
    "ghcr.io/kagent-dev/kagent/helm/kagent",
    "ghcr.io/kagent-dev/kagent/helm/kagent-crds",
)


def allowed_versions_for(package):
    rules = json.loads((repo_root() / "renovate.json").read_text())["packageRules"]
    return [r["allowedVersions"] for r in rules
            if package in r.get("matchPackageNames", []) and "allowedVersions" in r]


def chart_version(bundle):
    return yaml.safe_load((repo_root() / bundle / "fleet.yaml").read_text())["helm"]["version"]


class KagentHeldOn010(unittest.TestCase):
    def test_renovate_never_proposes_1x(self):
        for package in CHARTS:
            with self.subTest(package=package):
                caps = allowed_versions_for(package)
                self.assertEqual(len(caps), 1, "exactly one allowedVersions rule per chart")
                cap = caps[0]
                self.assertTrue(cap.startswith("/") and cap.endswith("/"),
                                "regex, not a range: a parse error must fail CLOSED")
                pattern = re.compile(cap[1:-1])
                for v in ("1.0.0", "1.2.3", "0.11.0"):
                    self.assertIsNone(pattern.search(v), v)
                for v in ("0.10.6", "0.10.7", "0.10.12"):
                    self.assertIsNotNone(pattern.search(v), v)

    def test_crds_and_controller_on_the_same_minor(self):
        crds, controller = chart_version("kagent-crds"), chart_version("kagent")
        self.assertEqual(crds.split(".")[:2], controller.split(".")[:2])
        self.assertTrue(controller.startswith("0.10."), controller)


if __name__ == "__main__":
    unittest.main()
