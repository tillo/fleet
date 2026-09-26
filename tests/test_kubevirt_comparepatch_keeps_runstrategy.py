"""Never exclude `/spec/runStrategy` from a KubeVirt VM's Fleet diff.

A VM's power state lives in `spec.runStrategy`. If it is excluded from the diff, Fleet
stops noticing that the live VM is not in the state git declares, and the bundle sits
NotReady forever with no way to tell why. The field is not owned by any webhook -- the
honeypot and atlas VMs hold their declared value while running -- so a divergence is a
real, correctable signal, not noise.

The correct response to a divergence is to patch the LIVE VM:

    kubectl -n <ns> patch vm <name> --type=merge -p '{"spec":{"runStrategy":"Halted"}}'

⚠️ Harvester's `harvesterhci.io/vmRunStrategy` ANNOTATION is different -- that is
bookkeeping and excluding it is correct. This test must therefore inspect the actual
excluded paths, never grep for the substring "runStrategy": ctf/fleet.yaml legitimately
mentions it in a comment explaining this very rule.
"""

import unittest

import fleetlib

FORBIDDEN_SUFFIX = "/spec/runStrategy"


def excluded_paths(spec: dict) -> list[str]:
    """Every JSON pointer a fleet.yaml excludes from the diff, in either supported form:
    `jsonPointers: [...]` and `operations: [{op: remove, path: ...}]`."""
    out = []
    for patch in ((spec.get("diff") or {}).get("comparePatches") or []):
        for p in patch.get("jsonPointers") or []:
            out.append(str(p))
        for op in patch.get("operations") or []:
            if isinstance(op, dict) and op.get("path"):
                out.append(str(op["path"]))
    return out


class KubeVirtRunStrategyTest(unittest.TestCase):
    def test_run_strategy_is_never_excluded(self):
        vm_bundles = []
        violations = []

        for bundle_dir, marker in fleetlib.bundles():
            has_vm = any(
                doc.get("kind") == "VirtualMachine"
                for rel in fleetlib.files_under(bundle_dir)
                for doc in fleetlib.load_docs(rel)
            )
            if not has_vm:
                continue
            vm_bundles.append(bundle_dir)
            for path in excluded_paths(fleetlib.bundle_spec(marker)):
                # exact field or a child of it; the annotation path must NOT match
                if path == FORBIDDEN_SUFFIX or path.startswith(FORBIDDEN_SUFFIX + "/"):
                    violations.append(
                        f"{marker}: excludes {path!r} from the diff. A VM's power state "
                        f"must stay visible to Fleet -- patch the live VM instead."
                    )

        # ⛔ Empty selection is not a pass.
        self.assertGreater(
            len(vm_bundles), 0,
            "no KubeVirt VirtualMachine bundles found at all -- discovery is broken",
        )
        self.assertEqual([], violations, "\n" + "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
