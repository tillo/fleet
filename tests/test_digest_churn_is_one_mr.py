#!/usr/bin/env python3
"""Digest churn must take ONE Renovate MR slot per run, and never hold an MR-only digest.

WHY THIS FILE EXISTS (bead infra-x1ju, measured 2026-10-18 and 2026-10-21)
Renovate creates at most prHourlyLimit MRs per run, and before spending them it sorts
branches pin -> digest -> patch -> minor -> major (workers/repository/process/sort.js in
44.139.0), so digest updates are ALWAYS served first. A dozen in-house `:latest` images
are rebuilt every few hours and upstream bases (node, python, postgres) are re-pushed
weekly, so every run re-queued a fresh batch of digests at the head of the line.

At prHourlyLimit 2 the 12:03 run of 2026-10-18 spent both slots on digests (!961, !962)
and 72 updates sat Rate-Limited; ghcr.io/open-webui/open-webui v0.12.3 never got a
branch. Raising the limit to 10 (!963) shortened the queue but did not end it: the 08:08
run of 2026-10-21 created ten MRs (!1171-!1180), all ten digests, and left seven more
digests queued ahead of the windmill chart and the nameserver/unbound/jump-bastion tags.

The fix is a group: every digest update that automerges shares one branch. The group must
EXCLUDE MR-only digests, because Renovate automerges a grouped branch only when EVERY
upgrade in it automerges (workers/repository/updates/generate.js,
`upgrades.every(u => u.automerge)`). caddy, keycloak and alpine/socat have each had an
MR-only digest MR; grouped, any one of them would hold every in-house rebuild open behind
a human -- the same starvation, moved. The rule excludes them with
`matchJsonata: ["automerge = true"]`, which Renovate evaluates against the config as
resolved by the rules ABOVE it (util/package-rules/index.js matches each rule against the
running config), so the rule's position is the mechanism.

WHAT IS ASSERTED
  1. an automerging digest -- in-house or upstream -- lands in the group;
  2. no package named by an `automerge: false` rule lands in it as a digest;
  3. no rule below the group sets `automerge` for a digest (it would demote a member the
     group had already taken, which is 2 by another route);
  4. the rule groups and does nothing else: digest only, no automerge, no soak.
"""

from __future__ import annotations

import re
import unittest

from test_every_pinned_image_is_renovate_reachable import renovate_config
from test_gitlab_chart_is_renovate_reachable import rule_matches

# Anchored on a phrase in the rule's own description rather than on its index.
GROUP_MARKER = "DIGEST CHURN IS ONE MR"

PACKAGE_FILE = {
    "kubernetes": "example/deploy.yaml",
    "helm-values": "example/values.yaml",
    "fleet": "example/fleet.yaml",
    "custom.regex": "example/fleet.yaml",
    "gitlabci": ".gitlab-ci.yml",
}

# The only matchJsonata shape this replay understands: `<field> = true|false`.
JSONATA_FIELD_IS_BOOL = re.compile(r"^\s*(\w+)\s*=\s*(true|false)\s*$")


def jsonata_matches(expressions: list[str], resolved: dict) -> bool:
    for expr in expressions:
        m = JSONATA_FIELD_IS_BOOL.match(expr)
        if not m:
            raise AssertionError(
                "matchJsonata %r is beyond this test's evaluator -- extend it before "
                "trusting the replay" % expr)
        if resolved.get(m.group(1)) is (m.group(2) == "true"):
            return True
    return False


def replay(cfg: dict, dep: dict) -> dict:
    """Last-match-wins per field, with matchJsonata read against the RUNNING config."""
    resolved = {"automerge": cfg.get("automerge", False)}
    for rule in cfg["packageRules"]:
        verdict = rule_matches({k: v for k, v in rule.items() if k != "matchJsonata"}, dep)
        if verdict is None:
            if {"automerge", "groupName"} & set(rule):
                raise AssertionError(
                    "a packageRule matched on every matcher this test implements, carries "
                    "one it does not, and sets automerge/groupName -- extend "
                    "IMPLEMENTED_MATCHERS: %r" % rule.get("description", "?")[:120])
            continue
        if not verdict:
            continue
        if "matchJsonata" in rule and not jsonata_matches(rule["matchJsonata"], resolved):
            continue
        for key, value in rule.items():
            if not key.startswith("match") and key != "description":
                resolved[key] = value
    return resolved


def digest(manager: str, name: str, update_type: str = "digest") -> dict:
    return {
        "manager": manager,
        "depName": name,
        "packageName": name,
        "packageFile": PACKAGE_FILE[manager],
        "datasource": "docker",
        "updateType": update_type,
    }


def group_rule(rules: list[dict]) -> tuple[int, dict]:
    # Anchored on the START of the description, not a substring: rules that must sit
    # above this one (GRADUATED — digest, DEMOTED) name it in their own prose, and a
    # substring search counts a mere mention as a second group rule (2026-10-23).
    hits = [i for i, r in enumerate(rules)
            if (r.get("description") or "").lstrip("⛔⭐ ⚠️").startswith(GROUP_MARKER)]
    if len(hits) != 1:
        raise AssertionError(
            "expected exactly one packageRule carrying %r, found %d -- without it every "
            "rebuilt image takes its own MR slot and version bumps starve behind them"
            % (GROUP_MARKER, len(hits)))
    return hits[0], rules[hits[0]]


class DigestChurnIsOneMr(unittest.TestCase):

    def setUp(self):
        self.cfg = renovate_config()
        self.index, self.rule = group_rule(self.cfg["packageRules"])

    def test_automerging_digests_share_the_group(self):
        samples = [
            digest("kubernetes", "registry.mdapi.ch/mdapi/example"),
            digest("kubernetes", "zot.mdapi.ch/mdapi/example"),
            digest("kubernetes", "python"),
            digest("helm-values", "zot.mdapi.ch/mdapi/example"),
            digest("gitlabci", "python"),
        ]
        for dep in samples:
            resolved = replay(self.cfg, dep)
            self.assertIs(True, resolved.get("automerge"),
                          "a %s digest of %s no longer automerges, so this test's premise is "
                          "gone" % (dep["manager"], dep["packageName"]))
            self.assertEqual(self.rule["groupName"], resolved.get("groupName"),
                             "an automerging %s digest of %s is not grouped, so it takes its "
                             "own prHourlyLimit slot again" % (dep["manager"], dep["packageName"]))

    def test_mr_only_digests_stay_out_of_the_group(self):
        population = [(m, name)
                      for r in self.cfg["packageRules"] if r.get("automerge") is False
                      for name in r.get("matchPackageNames", []) + r.get("matchDepNames", [])
                      for m in r.get("matchManagers", PACKAGE_FILE)]
        self.assertNotEqual([], population, "no automerge:false rule names a package, so "
                            "this test would prove nothing")

        demoted = 0
        for manager, name in population:
            resolved = replay(self.cfg, digest(manager, name))
            if resolved.get("automerge") is False:
                demoted += 1
            self.assertFalse(
                resolved.get("automerge") is False
                and resolved.get("groupName") == self.rule["groupName"],
                "the MR-only %s digest of %s resolves into group %r -- a group automerges "
                "only if every member does, so it would hold every rebuilt image open"
                % (manager, name, resolved.get("groupName")))
        self.assertGreater(demoted, 0, "no MR-only package resolves to automerge:false as a "
                           "digest, so the exclusion was never exercised")

    def test_nothing_below_the_group_decides_digest_automerge(self):
        below = [r.get("description", "?")[:90]
                 for r in self.cfg["packageRules"][self.index + 1:]
                 if "automerge" in r and "digest" in r.get("matchUpdateTypes", ["digest"])]
        self.assertEqual([], below,
                         "these packageRules set automerge for a digest BELOW the group, after "
                         "matchJsonata has already read it -- move them above: %r" % below)

    def test_the_rule_only_groups_digests(self):
        self.assertEqual(["digest"], self.rule.get("matchUpdateTypes"))
        self.assertEqual(["automerge = true"], self.rule.get("matchJsonata"))
        self.assertTrue(self.rule.get("groupName"))
        for field in ("automerge", "minimumReleaseAge"):
            self.assertNotIn(field, self.rule,
                             "the group carries `%s`, so it no longer only decides which "
                             "branch a digest lands on" % field)

        resolved = replay(self.cfg, digest("kubernetes", "registry.mdapi.ch/mdapi/example",
                                           update_type="patch"))
        self.assertIsNone(resolved.get("groupName"),
                          "a PATCH update resolved into a group -- version bumps must keep "
                          "their own MRs")


if __name__ == "__main__":
    unittest.main()
