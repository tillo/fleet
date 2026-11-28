"""The node hung-task panic must not fire on a cluster-wide Ceph stall (infra-m42.11).

lockup-panic.yml sets kernel.hung_task_panic=1, so a task stuck in D state for longer than
kernel.hung_task_timeout_secs panics the node, and panic=10 reboots it. While Ceph PGs are
inactive, every task writing to a Ceph RBD device blocks in D state. On 2026-11-21 the 300 s
value panicked quarto (pstore: 'Kernel panic - not syncing: hung_task: blocked tasks',
jbd2/rbd*, 327 s) and reset quo in the same second. That turned a two-node outage (qua
planned, qui ASR) into a full-cluster reset, and the reboots slowed Ceph's own recovery.
The timeout has to outlast a realistic Ceph recovery, hence 1800 s.
"""

import re
import unittest

import yaml

import fleetlib

FILE = "harvester-node-config/lockup-panic.yml"
MIN_TIMEOUT_SECS = 1800


def sysctls() -> dict[str, str]:
    (cloudinit,) = fleetlib.load_docs(FILE)
    stages = yaml.safe_load(cloudinit["spec"]["contents"])["stages"]
    conf = stages["boot"][0]["files"][0]["content"]
    return dict(re.findall(r"^\s*([\w.]+)\s*=\s*(\S+)", conf, re.M))


class HungTaskTimeout(unittest.TestCase):
    def test_timeout_outlasts_a_ceph_recovery(self):
        self.assertGreaterEqual(int(sysctls()["kernel.hung_task_timeout_secs"]), MIN_TIMEOUT_SECS)

    def test_panic_stays_armed(self):
        # Raising the timeout must not quietly switch off the escalation it tunes.
        self.assertEqual(sysctls()["kernel.hung_task_panic"], "1")


if __name__ == "__main__":
    unittest.main()
