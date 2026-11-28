"""A Fleet comparePatch excludes fields with `jsonPointers`, never `operations`.

Fleet v0.15 applies a comparePatch's `operations` in sequence and returns at the FIRST
op that fails, voiding every op for that object; the agent then logs two errors per
reconcile (infra-8f4.19, ~760 lines/h chronic until fleet !722). An op fails as soon as
its path is absent from any ONE of desired, live, or the live object's last-applied
annotation -- so an entry that passes today breaks silently on a chart change, a removed
container or an operator dropping a field. `jsonPointers` feed the argo normalizer, which
applies each pointer on its own and skips an absent path (infra-8f4.20).
"""

import unittest

import fleetlib


def operations_entries():
    for _bundle, marker in fleetlib.bundles():
        diff = fleetlib.bundle_spec(marker).get("diff") or {}
        for patch in diff.get("comparePatches") or []:
            if patch.get("operations"):
                yield (marker, patch.get("kind"), patch.get("name"))


class ComparePatchesUseJsonPointersTest(unittest.TestCase):
    def test_no_compare_patch_uses_operations(self):
        self.assertEqual(sorted(operations_entries()), [],
                         "comparePatch `operations` voids every op on the first absent "
                         "path -- write the exclusion as `jsonPointers`")

    def test_reader_sees_json_pointer_patches(self):
        pointers = [p for _b, m in fleetlib.bundles()
                    for p in ((fleetlib.bundle_spec(m).get("diff") or {})
                              .get("comparePatches") or [])
                    if p.get("jsonPointers")]
        self.assertGreater(len(pointers), 10, "found no jsonPointers -- the reader is broken")


if __name__ == "__main__":
    unittest.main()
