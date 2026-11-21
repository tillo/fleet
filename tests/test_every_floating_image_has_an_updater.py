"""A floating image with no updater is a workload that moves when a pod happens to die.

This is the *other half* of test_every_pinned_image_is_renovate_reachable.py, and it
catches a failure no other gate notices.

A manifest that says `:latest` with nothing in git pinning it floats, but nothing polls
it and nothing pins it. The image then moves only when the node happens to recreate the
pod, with no Deployment revision, no commit and no MR. That is the silent-freeze class
this repo has already paid for four times (alpine/k8s, the 12 values.yml files,
litellm, OpenObserve — see the sibling test's docstring).

The one thing that gives a floating reference an updater is a digest in the ref
(`repo:tag@sha256:…`): Renovate resolves the tag's digest and raises an ordinary MR
whenever it moves. No annotation can exempt a workload.

Lacking a digest is the failure, and it is invisible in every other check: lint.py scores
a floating ref as Lane A and says nothing, because a floating tag with `Always` is a
legitimate Lane-A shape — it simply assumes something is driving it.

⚠️ Scope, stated rather than hidden. This asserts LONG-RUNNING workloads only
(Deployment, StatefulSet, DaemonSet). A CronJob is deliberately exempt: every run
creates a fresh pod, so a floating tag with the default `Always` pull policy genuinely
does track upstream on its own, with no digest. Two such CronJobs exist
today (horizon-discovery's netscan, joplin's postgres cleanup) and they are not
failures — they are a second update mechanism, self-refreshing by construction. They
are also invisible to git, so if that ever stops being acceptable they should be
digest-pinned like everything else.

The floating-tag definition is not restated here: it is READ OUT of renovate.json's
LANE-A PREDICATE rule, so adding a shape to that regex moves this test with it. If the
predicate is deleted the test fails loudly rather than silently agreeing with whatever
is left — a scan that cannot match must not return the same green as a scan that found
nothing.
"""

from __future__ import annotations

import json
import os
import re
import unittest

import fleetlib
import yaml

KINDS = {"Deployment", "StatefulSet", "DaemonSet"}

def renovate_config() -> dict:
    return json.loads((fleetlib.repo_root() / "renovate.json").read_text(encoding="utf-8"))


def lane_a_predicate() -> re.Pattern:
    """The LANE-A PREDICATE regex, read from renovate.json rather than restated.

    Renovate writes it as `/…/`; the wrapping slashes are config syntax, not regex.
    """
    for rule in renovate_config().get("packageRules", []):
        if "LANE A PREDICATE" in rule.get("description", ""):
            pattern = rule["matchCurrentValue"]
            return re.compile(pattern[1:-1] if pattern.startswith("/") else pattern)
    raise AssertionError(
        "renovate.json has no LANE-A PREDICATE rule. That rule is what defines a "
        "floating tag, so this test cannot know which references must have an updater "
        "— fix or replace the rule rather than deleting this assertion."
    )


def pod_template(doc: dict) -> dict:
    """The pod template, whichever rung of the spec ladder this kind keeps it on."""
    spec = doc.get("spec") or {}
    return ((spec.get("template") or {}).get("metadata") or {}), ((spec.get("template") or {}).get("spec") or {})


def containers(doc: dict) -> list[tuple[str, str]]:
    _, spec = pod_template(doc)
    out = []
    for c in (spec.get("containers") or []) + (spec.get("initContainers") or []):
        if isinstance(c, dict) and c.get("image"):
            out.append((c.get("name", "?"), str(c["image"])))
    return out


def is_floating(ref: str, predicate: re.Pattern) -> bool:
    """No digest, and the tag is one the Lane-A predicate claims.

    A ref with no tag at all is `:latest` by definition, so it is Lane A too.
    """
    if "@sha256:" in ref:
        return False
    last = ref.rsplit("/", 1)[-1]
    if ":" not in last:
        return True
    return bool(predicate.search(ref.rsplit(":", 1)[-1]))


def workloads() -> list[tuple[str, str, dict]]:
    """(file, workload name, doc) for every long-running workload in tracked YAML."""
    out = []
    for rel in fleetlib.tracked_yaml():
        if os.path.basename(rel) == ".gitlab-ci.yml":
            continue
        try:
            text = (fleetlib.repo_root() / rel).read_text(encoding="utf-8")
            docs = list(yaml.safe_load_all(text))
        except (OSError, yaml.YAMLError):
            continue
        for doc in docs:
            if isinstance(doc, dict) and doc.get("kind") in KINDS:
                name = (doc.get("metadata") or {}).get("name", "?")
                out.append((rel, name, doc))
    return out


def unowned_floating_refs() -> list[tuple[str, str, str, str]]:
    """(file, workload, container, image) for floating refs that NOTHING updates."""
    predicate = lane_a_predicate()
    out = []
    for rel, name, doc in workloads():
        for cname, image in containers(doc):
            if "{{" in image or "${" in image:
                continue                  # a template, not a resolvable reference
            if is_floating(image, predicate):
                out.append((rel, name, cname, image))
    return sorted(set(out))


class FloatingImageOwnershipTest(unittest.TestCase):
    def test_no_floating_ref_is_left_without_an_updater(self):
        wl = workloads()

        # ⛔ Empty selection is not a pass: if the scan stops seeing workloads, every
        # assertion below would pass vacuously.
        self.assertGreater(len(wl), 20, "found almost no workloads -- the scan is broken")

        bad = unowned_floating_refs()
        msgs = [
            f"{rel}: {name}/{cname}: {image}\n"
            f"    This tag rolls on its own and NOTHING updates it: it carries no "
            f"@sha256 digest for Renovate to bump.\n"
            f"    Fix: pin the digest. Renovate Lane A will do it on its next run now "
            f"that the kubernetes manager is on by default — for a Helm values file the "
            f"manager cannot read, declare the ref in a kustomize post-render patch "
            f"instead (see mail/docker-mailserver/kustomize/)."
            for rel, name, cname, image in bad
        ]
        self.assertEqual(
            [], msgs,
            "\nFloating image reference(s) with NO updater -- these move only when a "
            "pod happens to be recreated:\n" + "\n".join(msgs),
        )


if __name__ == "__main__":
    unittest.main()
