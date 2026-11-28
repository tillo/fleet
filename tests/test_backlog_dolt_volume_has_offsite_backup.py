"""The beads tracker's own volume has an offsite restic copy (infra-qfam, user 2026-11-12).

backlog/data-dolt-0 is the Dolt store behind bd. Until 2026-11-12 its only copy off
the cluster was the daily `bd export` JSONL (RPO ~24 h); the volume itself had no
ReplicationSource. The decision was the estate's standard VolSync/restic channel.

Two traps the estate has hit before are asserted here, because both produce a
ReplicationSource that reports Successful while backing up the wrong thing:
  * data-dolt-0 is a LONGHORN volume, so a ceph-block snapshot class never snapshots
    it (memory volsync_replicationsource_on_a_longhorn_pvc_2026_09_22);
  * every restic source needs its OWN repository -- a reused RESTIC_REPOSITORY mixes
    two volumes' untagged snapshots and lets one's retain prune the other's
    (memory restic_restore_proven_and_repo_collision_2026_09_03).
"""

import unittest

import fleetlib


def docs(kind):
    for rel in fleetlib.tracked_yaml():
        for doc in fleetlib.load_docs(rel):
            if doc.get("kind") == kind:
                yield rel, doc


def restic_repositories() -> dict[tuple[str, str], str]:
    """(namespace, secret name) -> RESTIC_REPOSITORY, from every ExternalSecret template."""
    out = {}
    for _, es in docs("ExternalSecret"):
        tpl = ((es.get("spec") or {}).get("target") or {}).get("template") or {}
        repo = (tpl.get("data") or {}).get("RESTIC_REPOSITORY")
        if repo:
            meta = es.get("metadata") or {}
            out[(meta.get("namespace"), (es["spec"]["target"].get("name") or meta.get("name")))] = repo
    return out


def restic_sources():
    for rel, rs in docs("ReplicationSource"):
        spec = rs.get("spec") or {}
        if isinstance(spec.get("restic"), dict):
            yield rel, rs["metadata"].get("namespace"), spec


class BacklogDoltOffsiteBackupTest(unittest.TestCase):
    def test_data_dolt_0_has_a_longhorn_snapshotting_restic_source(self):
        found = [s for _, ns, s in restic_sources()
                 if ns == "backlog" and s.get("sourcePVC") == "data-dolt-0"]
        self.assertEqual(len(found), 1, "backlog/data-dolt-0 needs exactly one restic ReplicationSource")
        restic = found[0]["restic"]
        self.assertEqual(restic.get("volumeSnapshotClassName"), "longhorn-snapshot",
                         "data-dolt-0 is harvester-longhorn-2replicas: only a Longhorn class snapshots it")
        repo = restic_repositories().get(("backlog", restic.get("repository")))
        self.assertTrue(repo and repo.endswith("/data-dolt-0"),
                        f"its repository secret must point at a data-dolt-0 repo, got {repo!r}")

    def test_no_two_restic_sources_share_a_repository(self):
        repos = restic_repositories()
        seen: dict[str, str] = {}
        clashes = []
        for rel, ns, spec in restic_sources():
            repo = repos.get((ns, spec["restic"].get("repository")))
            if repo is None:
                continue  # secret not templated in git (e.g. kustomize-generated); not judged here
            where = f"{rel}: {ns}/{spec.get('sourcePVC')}"
            if repo in seen:
                clashes.append(f"{where} and {seen[repo]} -> {repo}")
            seen[repo] = where
        self.assertGreater(len(seen), 5, "found almost no restic sources -- the reader is broken")
        self.assertEqual(clashes, [], "restic sources sharing one repository")


if __name__ == "__main__":
    unittest.main()
