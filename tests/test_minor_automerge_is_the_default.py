#!/usr/bin/env python3
"""A minor update automerges, for practically every dependency (owner, 2026-11-13).

Owner decision on fleet !2189: "I prefer to get minor upgrades automatically. This,
and I insist, should be the default for practically everything" -- if a minor breaks
something, it is fixed after the fact. Caution alone ("read the notes", "migrations",
"config contract") is no longer a reason to hold a minor; majors stay MR-only.

So every package an MR-only rule names is replayed for a MINOR, with that rule's own
manager (a `fleet` or `custom.regex` rule never matches a dep replayed as
`kubernetes`, and would silently pass), and must resolve to automerge=true -- unless
it is in MECHANICAL below, where an automerge flag could not help: the MR cannot be
green on its own, or merging it kills the session that would fix it.
"""

from __future__ import annotations

import unittest

from test_gitlab_chart_is_renovate_reachable import effective_config
from test_graduation_ladder import dep_name, package_name, renovate_config

MECHANICAL = {
    # fleet-validate's helm/kubectl: no published digest, so *_SHA256 is a hand step
    # and the MR stays red on `sha256sum -c` until a human sets it (infra-i8ja).
    "helm/helm": "sha256 not derivable; MR is red until set by hand",
    "kubernetes/kubernetes": "sha256 not derivable + kubectl/API skew cap",
    # tests/test_cis_baseline_matches_kube_bench_pin.py keeps a version bump red until
    # the CIS baseline is re-derived by hand.
    "docker.io/aquasec/kube-bench": "red until the CIS baseline is re-derived",
    "aquasec/kube-bench": "red until the CIS baseline is re-derived",
    # The image of this very seat: merging rolls deploy/jump-bastion (Recreate) and
    # kills every session on it; it rolls at an attended or idle moment instead.
    "zot.mdapi.ch/mdapi/jump-bastion": "merging rolls the operator seat",
}
PLACEHOLDERS = {"__none__"}


def mr_only_packages(cfg: dict) -> list[tuple[str, str]]:
    """(name, manager) for every package an `automerge: false` rule names."""
    out = []
    for rule in cfg.get("packageRules", []):
        if rule.get("automerge") is not False:
            continue
        managers = rule.get("matchManagers") or ["kubernetes"]
        for name in rule.get("matchPackageNames", []) + rule.get("matchDepNames", []):
            if name not in PLACEHOLDERS:
                out.extend((name, m) for m in managers)
    return out


def minor_dep(cfg: dict, name: str, manager: str) -> dict:
    depname = dep_name(cfg, name)
    return {
        "manager": manager,
        "depName": depname,
        "packageName": package_name(cfg, depname),
        "updateType": "minor",
        "datasource": {"fleet": "helm", "custom.regex": "github-releases"}.get(manager, "docker"),
        "packageFile": "example/fleet.yaml" if manager == "fleet" else "example/deploy.yaml",
    }


class MinorAutomergeIsTheDefault(unittest.TestCase):
    def test_every_minor_automerges_but_the_mechanical_exceptions(self):
        cfg = renovate_config()
        held = []
        for name, manager in mr_only_packages(cfg):
            resolved, ambiguous = effective_config(cfg, minor_dep(cfg, name, manager))
            self.assertEqual([], [r.get("description", "?")[:100] for r in ambiguous
                                  if "automerge" in r], name)
            if resolved.get("automerge") is not True and name not in MECHANICAL:
                held.append(f"{name} ({manager})")
        self.assertEqual([], sorted(set(held)),
                         "a minor of these is MR-only for no mechanical reason; graduate "
                         "them (GRADUATED -- minor+patch) or add the reason to MECHANICAL")

    def test_a_major_still_waits_for_a_human(self):
        cfg = renovate_config()
        for name, manager in mr_only_packages(cfg):
            d = {**minor_dep(cfg, name, manager), "updateType": "major"}
            resolved, _ = effective_config(cfg, d)
            self.assertIsNot(True, resolved.get("automerge"), name)

    def test_the_exceptions_are_still_mr_only(self):
        """An exception that has since graduated is a stale line, not a policy."""
        names = {n for n, _ in mr_only_packages(renovate_config())}
        self.assertEqual(set(), set(MECHANICAL) - names)


if __name__ == "__main__":
    unittest.main()
