"""A VM that may live-migrate must name a CPU model every node can run.

Incident (infra-zbsi, 2026-10-27): the honeypot VMs ran with KubeVirt's default
`host-model`. Booted on quarto (DL360 Gen10, host model Skylake-Server-IBRS), their
migration target needed Skylake-Server-IBRS plus ~110 cpu-feature labels that only
quarto has, so the quarto drain looped VMIM Scheduling -> Failed every ~5 min and
timed out; the VMIs had to be deleted by hand, one at a time. The three Gen8 nodes
(qui/quo/qua) report host model IvyBridge-IBRS, so that is the newest model all four
can run. A VM pinned to it migrates both ways.

RATCHET: KNOWN_HOST_MODEL lists the live-migratable VMs that still use host-model.
It may only shrink.
"""
import unittest

import yaml

from tests import fleetlib

COMMON_MODEL = "IvyBridge-IBRS"
LIVE_MIGRATE = {"LiveMigrate", "LiveMigrateIfPossible"}

# Not yet pinned; each needs its own restart decision (follow-up bead infra-zbsi.1).
KNOWN_HOST_MODEL = {
    ("ciphertrust", "cm"): "CipherTrust appliance: check whether its licence binds to the CPU identity first",
    ("ctf", "kali"): "lab VM",
    ("atlas", "atlas-probe"): "RIPE Atlas software probe",
}


def live_migratable_vms():
    root = fleetlib.repo_root()
    for path in sorted(root.glob("**/*.y*ml")):
        if ".git" in path.parts:
            continue
        try:
            docs = list(yaml.safe_load_all(path.read_text()))
        except yaml.YAMLError:
            continue
        for doc in docs:
            if not isinstance(doc, dict) or doc.get("kind") != "VirtualMachine":
                continue
            spec = doc["spec"]["template"]["spec"]
            if spec.get("evictionStrategy") in LIVE_MIGRATE:
                ns = doc["metadata"].get("namespace") or path.parent.name
                yield ns, doc["metadata"]["name"], spec["domain"].get("cpu", {}).get("model")


class LiveMigratableVmsPinACpuModel(unittest.TestCase):
    def test_found_the_vms(self):
        self.assertGreaterEqual(len(list(live_migratable_vms())), 5,
                                "scan found almost no VMs - it is broken, not the repo")

    def test_every_live_migratable_vm_names_the_common_model(self):
        bad = [(ns, n, m) for ns, n, m in live_migratable_vms()
               if m != COMMON_MODEL and (ns, n) not in KNOWN_HOST_MODEL]
        self.assertEqual([], bad, f"set spec.template.spec.domain.cpu.model: {COMMON_MODEL}")

    def test_the_ratchet_only_shrinks(self):
        seen = {(ns, n): m for ns, n, m in live_migratable_vms()}
        stale = [k for k in KNOWN_HOST_MODEL if k not in seen or seen[k] == COMMON_MODEL]
        self.assertEqual([], stale, "drop these from KNOWN_HOST_MODEL")


if __name__ == "__main__":
    unittest.main()
