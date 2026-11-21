"""A digest-pinned image is pulled IfNotPresent, and the manifest says so EXPLICITLY.

`repo:tag@sha256:…` is Lane A: Renovate bumps the digest in git, and the changed pod
template is the rollout. Pulling it `Always` buys nothing — the digest cannot move under
a running ref — and costs a registry round-trip on every pod start, which is what makes
a pinned image boot-fragile on a cold cluster (the 2026-06-06 postmortem class).
f/probe/image_policy_lint counts every `digest+Always` container as a WARN, and the
ImagePolicyWarn alert pages on any WARN that persists for 50 h. 69 of them made it
page for weeks (infra-91rbx): the Keel→Renovate switch of 2026-10-02 added the digests
but left the pull policy alone.

Leaving the field out is not good enough, for two separate reasons:

* Kubernetes defaults an unset policy to `Always` when the TAG is `latest`, and the
  digest suffix does not change that, so `foo:latest@sha256:…` with no policy
  defaults to Always even on a brand-new object.
* Fleet never clears a field the manifest does not mention. A container that WAS
  `Always` (written by Keel's `kubectl patch`, or by an older manifest) stays
  `Always` in the cluster forever while git looks clean.

So the only manifest that converges is one that writes `imagePullPolicy: IfNotPresent`.

Scope: raw Deployment/StatefulSet/DaemonSet/CronJob manifests in tracked YAML. Helm
values are rendered by their chart and are linted live by the probe instead.
"""

from __future__ import annotations

import os
import unittest

import fleetlib

KINDS = {"Deployment", "StatefulSet", "DaemonSet", "CronJob"}


def pod_spec(doc: dict) -> dict:
    """The pod spec, whichever rung of the spec ladder this kind keeps it on."""
    spec = doc.get("spec") or {}
    if doc.get("kind") == "CronJob":
        spec = (spec.get("jobTemplate") or {}).get("spec") or {}
    return (spec.get("template") or {}).get("spec") or {}


def digest_containers(doc: dict) -> list[dict]:
    """Every container and init container of a workload whose image is digest-pinned."""
    if doc.get("kind") not in KINDS:
        return []
    spec = pod_spec(doc)
    every = (spec.get("containers") or []) + (spec.get("initContainers") or [])
    return [c for c in every if isinstance(c, dict) and "@sha256:" in str(c.get("image", ""))]


def offending_containers(doc: dict) -> list[tuple[str, str, str]]:
    """(container, image, policy) for each digest-pinned container not pulled IfNotPresent."""
    return [(c.get("name", "?"), str(c["image"]), c.get("imagePullPolicy", "<unset>"))
            for c in digest_containers(doc) if c.get("imagePullPolicy") != "IfNotPresent"]


def tracked_workloads() -> list[tuple[str, dict]]:
    """(file, doc) for every workload document in tracked YAML."""
    out = []
    for rel in fleetlib.tracked_yaml():
        if os.path.basename(rel) != ".gitlab-ci.yml":
            out.extend((rel, d) for d in fleetlib.load_docs(rel) if d.get("kind") in KINDS)
    return out


def scan() -> tuple[int, list[str]]:
    """(digest-pinned containers seen, one message per offender) across tracked YAML."""
    workloads = tracked_workloads()
    seen = sum(len(digest_containers(doc)) for _, doc in workloads)
    bad = [f"{rel}: {doc['kind']} {(doc.get('metadata') or {}).get('name', '?')}/{cname}: {policy}  ({image})"
           for rel, doc in workloads for cname, image, policy in offending_containers(doc)]
    return seen, sorted(bad)


class OffendingContainersTest(unittest.TestCase):
    """The predicate, on the shapes it must tell apart."""

    @staticmethod
    def deploy(**container) -> dict:
        return {"kind": "Deployment",
                "spec": {"template": {"spec": {"containers": [{"name": "c", **container}]}}}}

    def test_digest_with_if_not_present_conforms(self):
        self.assertEqual(offending_containers(self.deploy(image="a:latest@sha256:00", imagePullPolicy="IfNotPresent")), [])

    def test_digest_with_always_is_flagged(self):
        self.assertEqual(offending_containers(self.deploy(image="a:latest@sha256:00", imagePullPolicy="Always")),
                         [("c", "a:latest@sha256:00", "Always")])

    def test_digest_with_unset_policy_is_flagged(self):
        self.assertEqual(offending_containers(self.deploy(image="a:latest@sha256:00")),
                         [("c", "a:latest@sha256:00", "<unset>")])

    def test_tag_without_digest_is_out_of_scope(self):
        self.assertEqual(offending_containers(self.deploy(image="a:1.2.3", imagePullPolicy="Always")), [])

    def test_cronjob_init_container_is_seen(self):
        doc = {"kind": "CronJob", "spec": {"jobTemplate": {"spec": {"template": {"spec": {
            "initContainers": [{"name": "i", "image": "b:3@sha256:11", "imagePullPolicy": "Always"}]}}}}}}
        self.assertEqual(offending_containers(doc), [("i", "b:3@sha256:11", "Always")])

    def test_other_kinds_are_ignored(self):
        doc = self.deploy(image="a@sha256:00", imagePullPolicy="Always") | {"kind": "Pod"}
        self.assertEqual(offending_containers(doc), [])


class DigestPullPolicyTest(unittest.TestCase):
    def test_every_digest_pinned_container_pulls_if_not_present(self):
        seen, bad = scan()
        # ⛔ Empty selection is not a pass: a scan that stops seeing digest refs would
        # agree with anything.
        self.assertGreater(seen, 50, "found almost no digest-pinned containers -- the scan is broken")
        self.assertEqual(bad, [], "\n".join(
            [f"{len(bad)} digest-pinned container(s) not pulled IfNotPresent:"] + bad +
            ["Fix: write `imagePullPolicy: IfNotPresent` explicitly (unset is not enough; see docstring)."]))


if __name__ == "__main__":
    unittest.main()
