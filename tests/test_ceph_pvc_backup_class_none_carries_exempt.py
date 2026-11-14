"""On Ceph, `mdapi.ch/backup-class: none` declares nothing the coverage probe can see.

The incident this encodes (O2 incident PvcBackupUncovered, mirror infra-8lp51, from
2026-11-08 07:35Z): the new clamav bundle (infra-8f4.50) gave its ceph-block PVC
clamav/clamav-db the label `mdapi.ch/backup-class: none`, meaning "rebuildable, do not
back up". That label is the LONGHORN recurring-job class: it acts only through the
Longhorn volume's `recurring-job-group` labels, which is also the only place
f/probe/pvc_backup_coverage reads it. A ceph-block PV has no Longhorn volume, so the
declaration was inert and the probe rightly reported the PVC as having no backup story.

The probe's storage-agnostic exemption is the `mdapi.ch/backup-exempt: <reason>`
annotation (2026-10-18). So a Ceph PVC that says `backup-class: none` must also carry
that annotation, as tv/pms-docker-pvc.yml already does.
"""

import unittest

import fleetlib

CLASS = "mdapi.ch/backup-class"
EXEMPT = "mdapi.ch/backup-exempt"


def ceph_pvcs():
    for rel in fleetlib.tracked_yaml():
        for doc in fleetlib.load_docs(rel):
            if not (isinstance(doc, dict) and doc.get("kind") == "PersistentVolumeClaim"):
                continue
            sc = str((doc.get("spec") or {}).get("storageClassName") or "")
            if sc.startswith("ceph-"):
                yield rel, doc


class CephPvcBackupClassNoneCarriesExemptTest(unittest.TestCase):
    def test_ceph_pvc_declaring_none_is_exempt_for_the_probe(self):
        seen, missing = 0, []
        for rel, doc in ceph_pvcs():
            seen += 1
            meta = doc.get("metadata") or {}
            if (meta.get("labels") or {}).get(CLASS) != "none":
                continue
            if not str((meta.get("annotations") or {}).get(EXEMPT) or "").strip():
                missing.append(f"{rel}: {meta.get('namespace')}/{meta.get('name')}")
        self.assertGreater(seen, 5, "found no ceph-* PVCs -- the reader is broken")
        self.assertEqual(missing, [], f"ceph PVC with {CLASS}=none but no {EXEMPT} "
                         "annotation: the probe cannot see the label (Longhorn-only)")


if __name__ == "__main__":
    unittest.main()
