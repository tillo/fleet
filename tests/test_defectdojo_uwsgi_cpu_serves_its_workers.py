"""DefectDojo's uwsgi CPU limit must fit its worker count.

Incident (2026-11-26, o2 row infra-hy8gb, ContainerCpuThrottling on uwsgi, 14 incidents
in 7 d): the chart runs uwsgi with `processes: 2`, but the container's CPU limit was 1
core. The hourly `f/security/defectdojo_metrics` export reads all 49,771 open findings
(~51 pages of 1000, ~180 MB of JSON, ~7-8 min) and that ONE request holds the whole
quota: for those minutes the cgroup was throttled in ~100% of CFS periods (7-day peak
ratio 0.918), and the second worker got the leftovers - the blackbox probe's own
requests went from p95 114 ms to p95 611 ms, max 9.9 s.

The export cannot be made cheaper: DefectDojo 3.5.300's /api/v2/findings/ has no field
projection, and it is GIL-bound, so it can never use more than one core. What the
container needs is headroom for the OTHER worker, i.e. a limit of at least one core per
declared process.

A limit below the process count is the defect; no limit at all (cpu: null) is the
estate's other accepted shape and is left alone.
"""

from __future__ import annotations

import unittest

import yaml

from tests.fleetlib import repo_root

BUNDLE = "defectdojo"


def uwsgi_values() -> dict:
    values = yaml.safe_load((repo_root() / BUNDLE / "fleet.yaml").read_text())["helm"]["values"]
    return values["django"]["uwsgi"]


def cores(cpu) -> float:
    """A Kubernetes CPU quantity in cores: '2' -> 2.0, '1500m' -> 1.5."""
    text = str(cpu)
    return float(text[:-1]) / 1000 if text.endswith("m") else float(text)


def cores_for(processes: int, cpu_limit) -> bool:
    """True when the limit can host every worker at full speed; None limit means uncapped."""
    if cpu_limit is None:
        return True
    return cores(cpu_limit) >= processes


class DefectDojoUwsgiCpuServesItsWorkers(unittest.TestCase):
    def test_cpu_limit_is_at_least_one_core_per_worker(self):
        uwsgi = uwsgi_values()
        processes = uwsgi["appSettings"]["processes"]
        limit = uwsgi["resources"]["limits"]["cpu"]
        self.assertTrue(
            cores_for(processes, limit),
            f"uwsgi runs {processes} processes but its cpu limit is {limit}: one batch "
            f"request then pins the whole quota and starves the other worker "
            f"(o2 infra-hy8gb, 2026-11-26)")

    def test_cores_for_rejects_a_limit_below_the_process_count(self):
        self.assertFalse(cores_for(2, "1"))
        self.assertTrue(cores_for(2, "2"))
        self.assertFalse(cores_for(2, "1500m"), "1.5 cores still starves the second of 2 workers")
        self.assertTrue(cores_for(1, "1500m"))
        self.assertTrue(cores_for(4, None), "no limit at all is uncapped, not a defect")


if __name__ == "__main__":
    unittest.main()
