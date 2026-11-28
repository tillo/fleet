"""A CNPG instance PVC is re-created on every instance rebuild, and arrives undeclared.

The incident this encodes (O2 incident LonghornBackupClassUndeclared, infra-r9mzd,
2026-10-28..11-04): `backup_label_enforcer` alerts on any Longhorn PVC created after the
2026-09-16 policy epoch without `mdapi.ch/backup-class`. CNPG creates its instance PVCs
itself, so no manifest can label them -- and it creates a NEW one every time it rebuilds
an instance. grafana-pg-1/-2 and guac-pg-1 were rebuilt 10-26/27 after node events and
paged for a week; the 09-29 fix for openfga-pg was a live label, which the next rebuild
throws away.

The one place that survives a rebuild is the Cluster: CNPG copies
`spec.inheritedMetadata.labels` onto every PVC it creates and reconciles them onto the
existing ones. So every Cluster declares its class there.

LEGACY is the pre-epoch backlog (infra-30h.2): those clusters resolve to their
enforcer default today and are migrated by that bead, with the class the enforcer
already gives them (bootstrap and home-assistant are WEEKLY_NS). Shrink it; never grow it.
"""

import unittest

import fleetlib

CLASSES = {"none", "default", "weekly"}
LABEL = "mdapi.ch/backup-class"
LEGACY = {
    ("bootstrap", "gitlab-pg"),
    ("home-assistant", "ha-recorder-pg"),
    ("bussola", "bussola-pg"),
    ("litellm", "litellm-pg"),
    ("paperless", "paperless-pg"),
}


def cnpg_clusters():
    for rel in fleetlib.tracked_yaml():
        for doc in fleetlib.load_docs(rel):
            if (isinstance(doc, dict) and doc.get("kind") == "Cluster"
                    and str(doc.get("apiVersion", "")).startswith("postgresql.cnpg.io/")):
                meta = doc.get("metadata") or {}
                yield rel, (meta.get("namespace"), meta.get("name")), doc


class CnpgClustersDeclareBackupClassTest(unittest.TestCase):
    def test_every_cnpg_cluster_declares_a_backup_class(self):
        missing = []
        seen = set()
        for rel, key, doc in cnpg_clusters():
            seen.add(key)
            if key in LEGACY:
                continue
            labels = (((doc.get("spec") or {}).get("inheritedMetadata") or {})
                      .get("labels") or {})
            if labels.get(LABEL) not in CLASSES:
                missing.append(f"{rel}: {key[0]}/{key[1]}")
        self.assertGreater(len(seen), 5, "found no CNPG Clusters -- the reader is broken")
        self.assertEqual(missing, [], "CNPG Cluster without spec.inheritedMetadata.labels"
                         f"['{LABEL}'] in {sorted(CLASSES)}")

    def test_legacy_list_names_only_existing_undeclared_clusters(self):
        declared_or_absent = []
        clusters = {key: doc for _rel, key, doc in cnpg_clusters()}
        for key in LEGACY:
            doc = clusters.get(key)
            labels = (((doc or {}).get("spec") or {}).get("inheritedMetadata") or {}).get("labels") or {}
            if doc is None or LABEL in labels:
                declared_or_absent.append(key)
        self.assertEqual(declared_or_absent, [], "remove these from LEGACY")


if __name__ == "__main__":
    unittest.main()
