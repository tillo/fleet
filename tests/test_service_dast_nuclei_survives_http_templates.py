"""The active scan's nuclei must not be killed while it loads the HTTP template tree.

Measured 2026-11-23 (infra-7rog.31.17, first live run of the new service-dast): the
nuclei container was OOMKilled (exit 137) after 15 min at its 1Gi limit, before
emitting a single report, while the edge scan's `-t ssl/` (16 templates) stays well
inside it. `-t http/` is the estate's whole HTTP template tree; the old memory note
measured 809 MiB just to PARSE the whole tree, so 1Gi leaves no room for the scan.

Capped on purpose (see monitoring-rules/18-cpu-throttling.yml): keep the CPU cap,
raise only the memory backstop to 2Gi -- a quarter of the edge zap's 3Gi.
"""

import pathlib
import unittest

import yaml

from tests.fleetlib import repo_root

CJ = pathlib.Path(repo_root()) / "dast" / "service-cronjob.yml"


def nuclei():
    pod = yaml.safe_load(CJ.read_text())["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    return next(c for c in pod["containers"] if c["name"] == "nuclei")


def gib(text: str) -> float:
    return float(str(text).removesuffix("Gi").removesuffix("Mi")) * (1 if str(text).endswith("Gi") else 1 / 1024)


class NucleiSurvivesTheHttpTemplateTree(unittest.TestCase):
    def test_memory_limit_leaves_room_for_the_http_templates(self):
        self.assertGreaterEqual(gib(nuclei()["resources"]["limits"]["memory"]), 2)

    def test_still_cpu_capped(self):
        self.assertEqual("500m", nuclei()["resources"]["limits"]["cpu"])


if __name__ == "__main__":
    unittest.main()
