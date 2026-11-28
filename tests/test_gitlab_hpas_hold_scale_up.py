"""GitLab's autoscaled Deployments must hold a scale-up before acting on it.

WHY THIS FILE EXISTS
The GitLab chart's HPA default is scaleUp.stabilizationWindowSeconds 0 against a
300 s scale-down window, so any short CPU spike adds a pod that is removed five
minutes later. Each added pod is a not-yet-ready replica, and a not-ready replica
holds the whole mdapi-prod-gitlab Fleet bundle NotReady. The hourly
daily_infra_health push then catches it, and FleetBundlesNotReadySustained fires.

It happened twice:
  * 2026-11-04 (infra-r9mzd.8, fleet !1833): registry and sidekiq thrashed
    (1185/637 replica changes in 7 d). The fix was a sidekiq window of 300 s.
  * 2026-11-24 (infra-dg51s): webservice was dropped to minReplicas 1 on 11-07
    (infra-8f4.43) and never got the window. It went from ~2 to 50-80 replica
    changes a day and was unready 8.8% of the time. 29 of the 30 gitlab pushes
    in 7 d fell inside a webservice unready window. An HPA replay over 7 d of pod
    CPU gave 357 changes at 0 s, 6 at 180 s and 2 at 300 s.

Both components that have thrashed must carry a window. gitlab-shell and
gitlab-pages also autoscale but changed replicas 3-5 times in the same 7 d; add
them here if that changes.
"""

import unittest
from pathlib import Path

import fleetlib

VALUES = "gitlab/values.yaml"
SUBCHARTS = Path("gitlab/charts/gitlab/charts/gitlab/charts")
MIN_WINDOW_S = 180

AUTOSCALED = ("webservice", "sidekiq")


class GitlabHpasHoldScaleUpTest(unittest.TestCase):
    def setUp(self):
        self.gitlab = fleetlib.load_docs(VALUES)[0]["gitlab"]

    def test_each_autoscaled_component_holds_scale_up(self):
        for component in AUTOSCALED:
            with self.subTest(component=component):
                hpa = self.gitlab[component].get("hpa") or {}
                window = ((hpa.get("behavior") or {}).get("scaleUp") or {}).get(
                    "stabilizationWindowSeconds"
                )
                self.assertIsNotNone(
                    window,
                    f"gitlab.{component}.hpa.behavior.scaleUp.stabilizationWindowSeconds "
                    "is unset: the chart default is 0 s, so every CPU spike adds a "
                    "not-ready pod and holds mdapi-prod-gitlab NotReady",
                )
                self.assertGreaterEqual(window, MIN_WINDOW_S)

    def test_chart_renders_the_behavior_block(self):
        # A window in values does nothing if the subchart's template ignores it.
        for component in AUTOSCALED:
            with self.subTest(component=component):
                tpl = (fleetlib.repo_root() / SUBCHARTS / component / "templates" / "hpa.yaml").read_text()
                self.assertIn('include "gitlab.hpa.behavior"', tpl)


if __name__ == "__main__":
    unittest.main()
