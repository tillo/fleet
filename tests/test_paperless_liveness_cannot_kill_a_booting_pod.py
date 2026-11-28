"""Paperless's liveness probe must not kill a container that is still booting.

The incident this encodes (O2 SyntheticProbeFailing on https://documents.mdapi.ch,
infra-tjscj, 2026-10-29..11-06): the paperless container had no startupProbe, only a
liveness probe with initialDelaySeconds 60, periodSeconds 30 and failureThreshold 3. That
probe kills the container ~150 s after it starts if it has not answered `/` by then. A
fresh pod's first boot always took longer than that. kube_pod_container_state_started shows
the fresh container restarted ~2m35s after it started in 7 of 7 rollouts
(10-29 23:15, 10-31 07:16, 11-02 11:15, 11-03 22:19, 11-05 11:52, 15:23, 11-06 00:05).
The second boot then needed another 73-115 s, so every Recreate rollout blacked out
the DMS for 4-5 min instead of ~2.

The same probe made node blips worse. On 11-05 quo stalled 21:36:30-21:39. Paperless
was unready from 21:39, was killed at 21:42 and only answered again at 21:46: 7 min of
503s and a page. On 11-06 the pod was rescheduled and was killed twice during startup
(00:04:54, 00:07:54), which turned it into a 19-min outage.

So: a startupProbe owns the boot. Liveness starts only once the app has answered, and it
must stay failed for longer than a node blip before it kills.
"""

import unittest

import fleetlib

MANIFEST = "paperless/paperless-deploy.yml"
# Measured: first boot > 150 s (always killed at the first possible deadline), second
# boot 73-115 s. Twice the worst observed restart, plus the first-boot surplus.
MIN_STARTUP_BUDGET_S = 300
# quo's stall on 11-05 lasted ~150 s of scrape failures. The liveness probe must stay
# failed for longer than that before it restarts a container that would recover by itself.
MIN_LIVENESS_TOLERANCE_S = 180
# `/` answers in ~5 ms when healthy. A 1 s timeout turns one GC pause or I/O hiccup into
# a failure.
MIN_TIMEOUT_S = 3


def budget(probe: dict) -> int:
    return (probe.get("initialDelaySeconds", 0)
            + probe.get("periodSeconds", 10) * probe.get("failureThreshold", 3))


def paperless_container() -> dict:
    found = [c
             for doc in fleetlib.load_docs(MANIFEST)
             if isinstance(doc, dict) and doc.get("kind") == "Deployment"
             and doc["metadata"]["name"] == "paperless"
             for c in doc["spec"]["template"]["spec"]["containers"]
             if c["name"] == "paperless"]
    assert len(found) == 1, f"expected exactly one paperless container, found {len(found)}"
    return found[0]


class PaperlessProbes(unittest.TestCase):
    def setUp(self):
        self.c = paperless_container()

    def test_a_startup_probe_owns_the_boot(self):
        sp = self.c.get("startupProbe")
        self.assertIsNotNone(sp, "no startupProbe: liveness kills the first boot")
        self.assertGreaterEqual(budget(sp), MIN_STARTUP_BUDGET_S)

    def test_liveness_outlasts_a_node_blip(self):
        lp = self.c["livenessProbe"]
        tolerance = lp.get("periodSeconds", 10) * lp.get("failureThreshold", 3)
        self.assertGreaterEqual(tolerance, MIN_LIVENESS_TOLERANCE_S)

    def test_probes_do_not_fail_on_a_one_second_hiccup(self):
        for kind in ("startupProbe", "readinessProbe", "livenessProbe"):
            with self.subTest(kind=kind):
                self.assertGreaterEqual(self.c[kind].get("timeoutSeconds", 1), MIN_TIMEOUT_S)


if __name__ == "__main__":
    unittest.main()
