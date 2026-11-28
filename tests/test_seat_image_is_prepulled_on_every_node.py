"""The jump seat's zot-hosted image is cached on EVERY node, held by a running container.

THE INCIDENT (2026-11-21, infra-8f4)
A full-cluster outage took Ceph to HEALTH_ERR; RGW crashlooped, so zot.mdapi.ch (the
registry of record, which stores in a Ceph RGW bucket) answered 503. Every pod that
needed a pull went ImagePullBackOff -- including deploy/jump-bastion, the operator seat
used to fix the outage. Measured that day: the seat image
(zot.mdapi.ch/mdapi/jump-bastion) was cached on ONE node of four (the one running it).
The storage layer and zot itself were safe: they are seeded as OCI tarballs in
/var/lib/rancher/rke2/agent/images on every node (memory
rke2_bootstrap_image_tarballs_containerd_discards_layers_2026_09_17), and the rest of
the recovery-critical set comes from upstream registries or Harvester's own bundle, not
from zot. The seat was the one Ceph-dependent image in that set.

THE FIX
DaemonSet jump/seat-image-prepull runs each listed image on every node as a container
that only sleeps. A RUNNING container is what keeps kubelet image GC
(imageGCHighThresholdPercent 85) from removing it; an exited init container is weaker.
maxUnavailable 0 + maxSurge 1 keeps the old pod (and the old image) until the new
image has been pulled, so a bump during a zot outage leaves the old seat image cached.

WHAT IS ASSERTED
  1. the pre-puller exists and holds at least one image (an empty selection is no pass);
  2. every pre-puller image is pinned tag@sha256 and pulled IfNotPresent;
  3. every zot.mdapi.ch image of the seat Deployment is a pre-puller container, with
     the IDENTICAL reference -- Renovate bumps every occurrence of a package in one
     MR, so a seat bump that missed the pre-puller fails here;
  4. it tolerates every taint, so "every node" really is every node;
  5. it lives in its own bundle, so merging a pre-puller change never re-renders the
     Helm release that owns the seat Deployment (Recreate: a roll kills the session).
"""

from __future__ import annotations

import os
import unittest

import fleetlib

SEAT_FILE = "jump/bastion.yml"
SEAT = ("Deployment", "jump-bastion")
PREPULL = ("DaemonSet", "seat-image-prepull")
ZOT = "zot.mdapi.ch/"


def find(kind_name: tuple[str, str]) -> list[tuple[str, dict]]:
    """(file, doc) for every tracked document of this kind and name."""
    kind, name = kind_name
    return [(rel, d) for rel in fleetlib.tracked_yaml() for d in fleetlib.load_docs(rel)
            if d.get("kind") == kind and (d.get("metadata") or {}).get("name") == name]


def pod_spec(doc: dict) -> dict:
    return ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}


def containers(doc: dict, *, init: bool = True) -> list[dict]:
    spec = pod_spec(doc)
    every = list(spec.get("containers") or [])
    if init:
        every += spec.get("initContainers") or []
    return [c for c in every if isinstance(c, dict)]


def is_pinned(image: str) -> bool:
    """repo:tag@sha256:<64 hex> -- a tag for humans and Renovate, a digest for the node."""
    ref, _, digest = image.partition("@sha256:")
    tail = ref.rsplit("/", 1)[-1]
    return ":" in tail and len(digest) == 64 and all(ch in "0123456789abcdef" for ch in digest)


def tolerates_everything(spec: dict) -> bool:
    return any(t.get("operator") == "Exists" and not t.get("key") and not t.get("effect")
               for t in spec.get("tolerations") or [])


def bundle_of(rel: str) -> str:
    """The innermost bundle directory that owns this file."""
    dirs = [d for d, _ in fleetlib.bundles() if rel.startswith(d + "/")]
    return max(dirs, key=len) if dirs else ""


class PredicateTest(unittest.TestCase):
    def test_is_pinned(self):
        good = "zot.mdapi.ch/mdapi/jump-bastion:2.1.4-mdapi.1177@sha256:" + "a" * 64
        self.assertTrue(is_pinned(good))
        self.assertFalse(is_pinned("zot.mdapi.ch/mdapi/jump-bastion:2.1.4-mdapi.1177"))
        self.assertFalse(is_pinned("zot.mdapi.ch/mdapi/jump-bastion@sha256:" + "a" * 64))
        self.assertFalse(is_pinned("zot.mdapi.ch:443/x@sha256:" + "a" * 64))
        self.assertFalse(is_pinned("x:1@sha256:" + "a" * 63))
        self.assertFalse(is_pinned("x:1@sha256:" + "g" * 64))

    def test_tolerates_everything(self):
        self.assertTrue(tolerates_everything({"tolerations": [{"operator": "Exists"}]}))
        self.assertFalse(tolerates_everything({"tolerations": [
            {"operator": "Exists", "effect": "NoSchedule"}]}))
        self.assertFalse(tolerates_everything({"tolerations": [
            {"operator": "Exists", "key": "node-role.kubernetes.io/control-plane"}]}))
        self.assertFalse(tolerates_everything({}))


class SeatImagePrepullTest(unittest.TestCase):
    def setUp(self):
        prepull = find(PREPULL)
        self.assertEqual(len(prepull), 1, f"exactly one {PREPULL} expected, found {len(prepull)}")
        self.prepull_file, self.prepull = prepull[0]
        seat = [(rel, d) for rel, d in find(SEAT) if rel == SEAT_FILE]
        self.assertEqual(len(seat), 1, f"{SEAT} not found in {SEAT_FILE}")
        self.seat = seat[0][1]

    def test_every_prepulled_image_is_pinned_and_pulled_if_not_present(self):
        held = containers(self.prepull)
        self.assertTrue(held, "the pre-puller holds no image")
        for c in held:
            with self.subTest(container=c.get("name")):
                self.assertTrue(is_pinned(str(c.get("image", ""))), c.get("image"))
                self.assertEqual(c.get("imagePullPolicy"), "IfNotPresent")

    def test_every_zot_image_of_the_seat_is_held_by_a_running_container(self):
        wanted = {c["image"] for c in containers(self.seat) if str(c.get("image", "")).startswith(ZOT)}
        self.assertTrue(wanted, f"no {ZOT} image found in the seat -- discovery is broken")
        held = {c.get("image") for c in containers(self.prepull, init=False)}
        self.assertEqual(wanted - held, set(),
                         "seat image(s) not held by seat-image-prepull: bump both in one MR")

    def test_seat_pulls_if_not_present(self):
        for c in containers(self.seat):
            with self.subTest(container=c.get("name")):
                self.assertEqual(c.get("imagePullPolicy"), "IfNotPresent")

    def test_runs_on_every_node_and_keeps_the_old_image_until_the_new_one_is_pulled(self):
        self.assertTrue(tolerates_everything(pod_spec(self.prepull)),
                        "needs a bare `operator: Exists` toleration")
        self.assertFalse(pod_spec(self.prepull).get("nodeSelector"))
        rolling = ((self.prepull.get("spec") or {}).get("updateStrategy") or {}).get("rollingUpdate") or {}
        self.assertEqual(rolling.get("maxUnavailable"), 0)
        self.assertEqual(rolling.get("maxSurge"), 1)

    def test_lives_outside_the_seat_bundle(self):
        self.assertNotEqual(bundle_of(self.prepull_file), bundle_of(SEAT_FILE))
        self.assertTrue(bundle_of(self.prepull_file), "pre-puller file belongs to no bundle")


if __name__ == "__main__":
    unittest.main()
