"""findmy-collector publishes WHEN its Find My keys were last exported (infra-e7g.2).

The keys come off the macOS VM over SSH, and that VM is off by design (replicas 0,
booted for an export session every few weeks). While it is off every cycle logs
"key extraction failed, using keys from the last cycle" and still completes, so the
cycle timestamp stays fresh. What is cached is the KEYS, not the positions: each
cycle still fetches current reports from Apple and drops any older than 6 h. Cached
keys only cost NEW tags -- a tag enrolled after the last export never appears.

So the age of the export is the thing to make visible, not a cycle failure: every
cycle pushes findmy_collector_keys_exported_timestamp_seconds, the newest export on
the state volume (0 when there is none), on both of its exits.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_mqtt_clients_authenticate import load_collector

METRIC = "findmy_collector_keys_exported_timestamp_seconds"


class KeysExportedAt(unittest.TestCase):
    def setUp(self):
        self.c = load_collector()
        self.dir = Path(tempfile.mkdtemp())

    def test_no_directory_is_never(self):
        self.assertEqual(self.c.keys_exported_at(self.dir / "missing"), 0)

    def test_an_empty_directory_is_never(self):
        self.assertEqual(self.c.keys_exported_at(self.dir), 0)

    def test_the_newest_key_file_is_the_export_time(self):
        for name, mtime in (("a.json", 1_000), ("b.json", 3_000), ("c.json", 2_000)):
            (self.dir / name).write_text("{}")
            os.utime(self.dir / name, (mtime, mtime))
        self.assertEqual(self.c.keys_exported_at(self.dir), 3_000)

    def test_files_that_are_not_keys_do_not_count(self):
        (self.dir / "a.json").write_text("{}")
        os.utime(self.dir / "a.json", (1_000, 1_000))
        (self.dir / "notes.txt").write_text("x")
        os.utime(self.dir / "notes.txt", (9_000, 9_000))
        self.assertEqual(self.c.keys_exported_at(self.dir), 1_000)


class CycleMetrics(unittest.TestCase):
    def setUp(self):
        self.c = load_collector()

    def test_names_every_gauge_once(self):
        m = self.c.cycle_metrics(extract_ok=0, accessories=15, tags=9, located=4,
                                 exported_at=1_000, now=2_000)
        self.assertEqual(m, {
            "findmy_collector_extract_ok": 0,
            "findmy_collector_accessories_total": 15,
            "findmy_collector_tags_total": 9,
            "findmy_collector_tags_located": 4,
            METRIC: 1_000,
            "findmy_collector_last_success_timestamp_seconds": 2_000,
        })


class MainPushesIt(unittest.TestCase):
    """Through main(): the Mac is unreachable, as it is whenever the VM is off."""

    def setUp(self):
        self.c = load_collector()
        state = Path(tempfile.mkdtemp())
        self.c.DEVICES, self.c.IMPORTED = state / "devices", state / "imported"
        self.pushed = []
        self.c.push_metrics = lambda **kw: self.pushed.append(kw)
        self.c.seed_imported = lambda: None

        def unreachable():
            raise OSError("Unable to connect to port 10022")
        self.c.extract_keys = unreachable

    def run_main(self):
        with mock.patch.dict("sys.modules", {"paho": mock.MagicMock(),
                                             "paho.mqtt": mock.MagicMock(),
                                             "paho.mqtt.client": mock.MagicMock()}):
            self.assertEqual(self.c.main(), 0)
        (pushed,) = self.pushed
        return pushed

    def test_no_keys_at_all_pushes_never_exported(self):
        pushed = self.run_main()
        self.assertEqual(pushed[METRIC], 0)
        self.assertEqual(pushed["findmy_collector_extract_ok"], 0)

    def test_cached_keys_push_the_time_of_their_export(self):
        self.c.DEVICES.mkdir()
        (self.c.DEVICES / "tag.json").write_text('{"identifier": "tag", "model": "x"}')
        os.utime(self.c.DEVICES / "tag.json", (1_700_000_000, 1_700_000_000))
        account = mock.MagicMock()
        account.fetch_location.return_value = None
        self.c.load_account = lambda: account
        self.c.mqtt_connect = lambda mqtt: mock.MagicMock()
        self.c.publish_session = lambda: None
        self.c.FindMyAccessory = mock.MagicMock()
        pushed = self.run_main()
        self.assertEqual(pushed[METRIC], 1_700_000_000)
        self.assertEqual(pushed["findmy_collector_extract_ok"], 0)
        self.assertEqual(pushed["findmy_collector_tags_total"], 1)


if __name__ == "__main__":
    unittest.main()
