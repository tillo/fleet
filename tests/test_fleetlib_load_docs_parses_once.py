"""fleetlib.load_docs parses each tracked file once per process, and callers cannot share state.

Until 2026-11-24 load_docs re-parsed the file on every call. 38 modules sweep all ~1250
tracked YAML files through it, several from setUp (once per TEST), so the suite spent
708 s of fleet-validate's 29 min re-parsing the same bytes at ~10 s a pass. That made the
job the largest consumer of the shared runner (389 of 746 runner-hours a week).

The cache is only safe if a caller that mutates what it got back cannot change what the
next caller sees -- tests build fixtures by editing real documents.
"""

from __future__ import annotations

import unittest
from unittest import mock

import fleetlib

SAMPLE = next(f for f in fleetlib.tracked_yaml() if fleetlib.load_docs(f))


class LoadDocs(unittest.TestCase):
    def test_a_second_call_does_not_parse_again(self):
        fleetlib.load_docs(SAMPLE)
        with mock.patch.object(fleetlib.yaml, "safe_load_all", side_effect=AssertionError("re-parsed")):
            self.assertTrue(fleetlib.load_docs(SAMPLE))

    def test_a_caller_mutating_its_docs_does_not_reach_the_next_caller(self):
        first = fleetlib.load_docs(SAMPLE)
        first[0]["__mutated__"] = True
        first.append({"extra": 1})
        again = fleetlib.load_docs(SAMPLE)
        self.assertNotIn("__mutated__", again[0])
        self.assertNotIn({"extra": 1}, again)

    def test_unparseable_and_missing_files_still_yield_nothing(self):
        self.assertEqual(fleetlib.load_docs("does/not/exist.yml"), [])


if __name__ == "__main__":
    unittest.main()
