"""Every VolSync restic Secret carries ITS OWN repo's password and B2 key, nothing shared.

SA token audit (infra-p6q07.10): all VolSync repos once rendered one restic password
(/mdapi/volsync/ceph-backup/restic-password) and one bucket-wide B2 key
(/mdapi/volsync/b2/*) into 13 namespaces, a hosting tenant's among them. Anyone who could
read the Secret in ONE namespace could decrypt every namespace's backups and hide every
object in the bucket.

Now each repo has /mdapi/volsync/repo/<repo>/{restic-password,b2-key-id,b2-key}: a
restic key of that repo alone, and a B2 key whose namePrefix is ceph-backup/<repo>/.
`~/bin/volsync-repo-keys.py` mints them (passwords, then b2 with the master key) and,
after this switch, `retire` removes the shared restic key from each repo.

A NEW VolSync backup therefore needs its three items minted first:
    python3 ~/bin/volsync-repo-keys.py passwords --only <repo>
    python3 ~/bin/volsync-repo-keys.py b2 --only <repo>        # own terminal, master key
"""

import re
import unittest

import fleetlib

REPO_RE = re.compile(r"^s3:https://s3\.us-west-001\.backblazeb2\.com/saltminio/ceph-backup/([a-z0-9][a-z0-9-]*)$")
SHARED = re.compile(r"^/mdapi/volsync/(ceph-backup|b2)/")
ITEMS = {"restic-password", "b2-key-id", "b2-key"}


def restic_externalsecrets():
    for rel in fleetlib.tracked_yaml():
        for doc in fleetlib.load_docs(rel):
            if not isinstance(doc, dict) or doc.get("kind") != "ExternalSecret":
                continue
            spec = doc.get("spec") or {}
            data = ((spec.get("target") or {}).get("template") or {}).get("data") or {}
            if "RESTIC_REPOSITORY" in data:
                yield rel, doc, data["RESTIC_REPOSITORY"], spec.get("data") or []


class VolsyncResticCredsArePerRepo(unittest.TestCase):
    def test_found_the_volsync_secrets(self):
        self.assertGreaterEqual(len(list(restic_externalsecrets())), 13)  # 14 until tenant-1 closed 2026-11-20

    def test_each_secret_reads_only_its_own_repo_items(self):
        for rel, doc, url, data in restic_externalsecrets():
            name = doc["metadata"]["name"]
            with self.subTest(file=rel, es=name):
                m = REPO_RE.match(url)
                self.assertIsNotNone(m, f"{url} is not a saltminio/ceph-backup repo")
                own = {f"/mdapi/volsync/repo/{m.group(1)}/{i}" for i in ITEMS}
                keys = {d["remoteRef"]["key"] for d in data}
                self.assertEqual(keys, own, "must read exactly its repo's three items")

    def test_no_externalsecret_reads_the_shared_items(self):
        for rel in fleetlib.tracked_yaml():
            for doc in fleetlib.load_docs(rel):
                if not isinstance(doc, dict) or doc.get("kind") != "ExternalSecret":
                    continue
                spec = doc.get("spec") or {}
                refs = [d.get("remoteRef", {}).get("key", "") for d in spec.get("data") or []]
                refs += [(d.get("find") or {}).get("path", "") or (d.get("extract") or {}).get("key", "")
                         for d in spec.get("dataFrom") or []]
                with self.subTest(file=rel, es=doc["metadata"].get("name")):
                    self.assertEqual([r for r in refs if SHARED.match(r or "")], [])

    def test_one_secret_per_repo(self):
        seen = {}
        for rel, doc, url, _ in restic_externalsecrets():
            self.assertNotIn(url, seen, f"{rel} and {seen.get(url)} share a repo")
            seen[url] = rel


if __name__ == "__main__":
    unittest.main()
