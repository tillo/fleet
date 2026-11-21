"""A VolSync restic cache must live on a class with Longhorn dataLocality DISABLED.

The incident this encodes (O2 incident LonghornVolumeSnapshotsNearMax, mirror
infra-cokig, recurring from 2026-11-05; hand bead infra-30h.8): the cache PVCs
used harvester-longhorn-1replica or longhorn-volsync-cache-uid*, which set no
dataLocality, so they inherited Longhorn's default `best-effort`. A cache is
attached only while its mover runs, and the mover pod lands on any node. Whenever
it landed on a node other than the one holding the single replica, best-effort
locality started a local replica and rebuilt it, and every rebuild leaves a
system snapshot (auto-cleanup-system-generated-snapshot=false). The hourly
snapshotcleanup job never reaches a detached volume, so the snapshots piled up to
snapshot-max-count 6: hand-cleared on 2026-11-06 (infra-30h.7), back at 4-6 of 6
on six caches by 2026-11-11. A volume at the cap can neither rebuild nor take a
VolSync snapshot.

With dataLocality disabled the engine simply reaches the replica over the network:
no rebuild, so no snapshot. A cache is disposable, so locality buys nothing there.

StorageClass parameters are immutable, so the fix is a class whose parameters say
so, in this repo. The Harvester-made harvester-longhorn-1replica is not in git,
so it cannot pass.
"""

import unittest

import fleetlib


def docs_of(kinds):
    for rel in fleetlib.tracked_yaml():
        for doc in fleetlib.load_docs(rel):
            if isinstance(doc, dict) and doc.get("kind") in kinds:
                yield rel, doc


def classes_in_repo() -> dict[str, dict]:
    return {
        (doc.get("metadata") or {}).get("name"): doc.get("parameters") or {}
        for _, doc in docs_of({"StorageClass"})
    }


def restic_caches():
    for rel, doc in docs_of({"ReplicationSource", "ReplicationDestination"}):
        restic = (doc.get("spec") or {}).get("restic")
        if isinstance(restic, dict):
            meta = doc.get("metadata") or {}
            where = f"{rel}: {doc['kind']} {meta.get('namespace')}/{meta.get('name')}"
            yield where, restic.get("cacheStorageClassName")


class VolsyncCacheClassDisablesDataLocalityTest(unittest.TestCase):
    def test_every_restic_cache_class_disables_data_locality(self):
        classes = classes_in_repo()
        seen, wrong = 0, []
        for where, cls in restic_caches():
            seen += 1
            locality = (classes.get(cls) or {}).get("dataLocality")
            if locality != "disabled":
                wrong.append(f"{where}: cacheStorageClassName={cls!r} "
                             f"(dataLocality={locality!r})")
        self.assertGreater(seen, 5, "found no restic ReplicationSources -- the reader is broken")
        self.assertEqual(wrong, [], "restic cache on a class without dataLocality: disabled "
                         "-- best-effort locality rebuilds the detached cache whenever the "
                         "mover lands on another node, and each rebuild leaves a system "
                         "snapshot nothing purges (infra-cokig). Use a volsync-cache-* class.")


if __name__ == "__main__":
    unittest.main()
