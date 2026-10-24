#!/usr/bin/env python3
"""The automerge ladder must be a decision you can make, and a decision you can undo.

WHY THIS FILE EXISTS (2026-10-23, measured against the live config and the engine)

1. `automergeSchedule` never gated anything. With `platformAutomerge: true` GitLab arms
   merge-when-pipeline-succeeds when Renovate CREATES the MR (getPlatformPrOptions() in
   workers/repository/update/pr/index.js does not consult the schedule), so the written
   window `* 0-6 * * *` was fiction: bot merges on 2026-10-23 landed at 16:11Z and 16:16Z
   on MRs created at 16:08Z and 16:12Z. A config line that cannot fail, again.

2. The ladder had a hole at the BOTTOM. GRADUATED matched only ["minor","patch"], so a
   digest update fell through to the package's MR-only opt-out: 19 of the 19 graduated
   packages still held their digest MRs. The class that waited for a human was the
   same-version rebuild — the base-image security path, and the lowest-risk update here.

3. The ladder had no DOWN direction, and one rung that never existed: `caddy` sat on the
   GRADUATED list while the manifest writes `library/caddy`, so that graduation was a
   no-op for months and nothing said so.

WHAT IS ASSERTED
  1. no `automergeSchedule` at the top level or in any packageRule (it is inert here);
  2. GRADUATED·digest and DEMOTED sit ABOVE the digest group, GRADUATED·major stays last
     (packageRules are last-match-wins per field, so position IS the policy);
  3. a graduated package really resolves `automerge: true` for its own update types —
     a rule that a later MR-only rule overrides is a description, not a policy;
  4. a DEMOTED package resolves `automerge: false` for EVERY update type, digest
     included, or the group rule would admit it and never merge the group again;
  5. no package is on both lists, and every name on either list exists in a manifest
     (the `caddy` vs `library/caddy` failure);
  6. the graduation ticks are defined ONCE, in the top-level `prBodyNotes`, so every
     Renovate MR offers them — including an automerging one, which is the only place
     `automerge:never` can realistically be reached.
"""

from __future__ import annotations

import re
import unittest

import fleetlib
from test_every_pinned_image_is_renovate_reachable import (
    IMAGE_RE,
    OUT_OF_SCOPE_FILES,
    renovate_config,
    spellings,
)
from test_gitlab_chart_is_renovate_reachable import effective_config

GROUP_MARKER = "DIGEST CHURN IS ONE MR"
BLANKET_MARKER = "LANE POLICY · BLANKET MINOR+PATCH"
GRADUATED = {
    "digest": "GRADUATED — digest",
    "minor": "GRADUATED — minor+patch",
    "major": "GRADUATED — major",
}
DEMOTED_MARKER = "DEMOTED — MR-only"

# A name no registry can serve. Renovate 44 happens to treat an empty
# `matchPackageNames` as "matches nothing" (probed with applyPackageRules on
# 2026-10-23), but this rule carries `automerge: true`, where being wrong means the
# whole estate automerges, so the no-op state is made version-proof instead.
SENTINEL = "__none__"

TICK = re.compile(r"<!--grad:\{\{\{depName\}\}\}:(?P<level>[a-z]+)-->")
CHART = re.compile(r"(?m)^\s*chart:\s*[\"']?([A-Za-z0-9][^\s\"'#]*)")


def index_of(cfg: dict, marker: str) -> int:
    """Locate a rule by the START of its description.

    ⚠️ Substring, not startswith, is a trap this repo has already paid for twice: the
    GRADUATED·digest and DEMOTED descriptions both NAME the group rule ("must sit above
    'DIGEST CHURN IS ONE MR'"), so a substring search for the group returns the rule
    that merely mentions it.
    """
    for i, rule in enumerate(cfg["packageRules"]):
        if rule.get("description", "").lstrip("⛔⭐ ⚠️") .startswith(marker):
            return i
    raise AssertionError("no packageRule whose description starts with %r" % marker)


def rule(cfg: dict, marker: str) -> dict:
    return cfg["packageRules"][index_of(cfg, marker)]


def names(cfg: dict, marker: str) -> list[str]:
    return [n for n in rule(cfg, marker).get("matchPackageNames", []) if n != SENTINEL]


def package_name(cfg: dict, pkg: str) -> str:
    """`packageName` as the kubernetes manager reports it, i.e. AFTER registryAliases.

    ⛔ The alias is why `matchPackageNames` and `matchDepNames` are not interchangeable:
    `zot.mdapi.ch/berriai` is aliased to `ghcr.io/berriai`, so a rule naming the zot
    spelling in matchPackageNames matches nothing at all. A replay that sets
    packageName = depName turns that silent miss into a green test.
    """
    for prefix, replacement in (cfg.get("kubernetes", {}).get("registryAliases") or {}).items():
        if pkg == prefix or pkg.startswith(prefix + "/"):
            return replacement + pkg[len(prefix):]
    return pkg


def dep(cfg: dict, pkg: str, update_type: str) -> dict:
    return {
        "manager": "kubernetes",
        "depName": pkg,
        "packageName": package_name(cfg, pkg),
        "updateType": update_type,
        "datasource": "docker",
        "packageFile": "example/deploy.yaml",
    }


def resolved(cfg: dict, pkg: str, update_type: str) -> dict:
    """Automerge as the engine would resolve it, plus a guard on the replay itself.

    A rule the replay cannot evaluate is returned as ambiguous rather than applied; if
    such a rule sets `automerge` the verdict is worthless, so say so instead of passing.
    """
    config, ambiguous = effective_config(cfg, dep(cfg, pkg, update_type))
    for ambiguous_rule in ambiguous:
        if "automerge" in ambiguous_rule:
            raise AssertionError(
                "cannot replay a rule that sets automerge and carries a matcher this test "
                "does not implement: %r" % ambiguous_rule.get("description", "?")[:120])
    return config


def manifest_names() -> set[str]:
    """Every package name this repo actually references: image refs and Helm charts.

    Image refs are taken WITHOUT the pinned-only filter, because a graduated package
    may sit on a rolling tag (`latest`, `2-alpine`) that is never "pinned" yet is still
    exactly what a rule names.
    """
    root = fleetlib.repo_root()
    found: set[str] = set()
    for rel in fleetlib.tracked_yaml():
        if rel.rsplit("/", 1)[-1] in OUT_OF_SCOPE_FILES:
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            m = IMAGE_RE.match(line)
            if not m:
                continue
            ref = m.group(1)
            if "{{" in ref or "${" in ref:
                continue
            repo = ref.partition("@")[0]
            if ":" in repo.rsplit("/", 1)[-1]:
                repo = repo.rsplit(":", 1)[0]
            found.add(repo)
        if rel.rsplit("/", 1)[-1] in ("fleet.yaml", "fleet.yml"):
            for chart in CHART.findall(text):
                found.add(chart)
                found.add(chart.rsplit("/", 1)[-1])
    return found


class GraduationLadderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = renovate_config()

    def test_automerge_schedule_is_not_used(self):
        """It is inert under platformAutomerge, so its presence is a lie that gets cited.

        merge-when-pipeline-succeeds is armed at MR CREATION, before any schedule check,
        so a window here gates nothing. (The nested `vulnerabilityAlerts` block keeps one
        for the day a language package is enabled; that whole block is provably inert.)
        """
        self.assertNotIn(
            "automergeSchedule", self.cfg,
            "top-level automergeSchedule does not gate merges under platformAutomerge: "
            "GitLab arms MWPS when the MR is created — measured 2026-10-23, bot merges at "
            "16:11Z on a 16:08Z MR. A real window is an external merge gate.")
        offending = [r.get("description", "?")[:80] for r in self.cfg["packageRules"]
                     if "automergeSchedule" in r]
        self.assertEqual(
            offending, [],
            "a packageRule automergeSchedule gates nothing either, for the same reason")

    def test_ladder_is_ordered_for_last_match_wins(self):
        """Position is the mechanism: the group rule reads `automerge` resolved above it."""
        group = index_of(self.cfg, GROUP_MARKER)
        digest = index_of(self.cfg, GRADUATED["digest"])
        demoted = index_of(self.cfg, DEMOTED_MARKER)
        major = index_of(self.cfg, GRADUATED["major"])
        blanket = index_of(self.cfg, BLANKET_MARKER)
        last = len(self.cfg["packageRules"]) - 1

        self.assertLess(blanket, digest, "the ladder must be able to override the blanket rule")
        self.assertLess(digest, group,
                        "GRADUATED — digest must sit ABOVE the digest group: matchJsonata "
                        "there reads automerge as resolved above it, so below the group a "
                        "graduated package would open one MR per rebuild per run (infra-x1ju)")
        self.assertLess(demoted, group,
                        "DEMOTED must sit ABOVE the digest group, or a demoted digest is "
                        "admitted to the shared branch and then blocks it for every member")
        self.assertLess(digest, demoted,
                        "when a package is on both lists by mistake, DEMOTED must win")
        self.assertEqual(major, last,
                         "GRADUATED — major must stay last: un-graduation is expressed by "
                         "removal from these lists, and anything below could re-enable one")

    def test_graduation_actually_graduates(self):
        """A graduation a later rule overrides is a comment, not a policy."""
        for level, marker in GRADUATED.items():
            update_types = rule(self.cfg, marker)["matchUpdateTypes"]
            for pkg in names(self.cfg, marker):
                for update_type in update_types:
                    self.assertIs(
                        resolved(self.cfg, pkg, update_type).get("automerge"), True,
                        "%s is on %r but resolves automerge != true for a %s update — a "
                        "rule below it is winning (last-match-wins per field)"
                        % (pkg, marker, update_type))

    def test_demoted_is_mr_only_for_every_update_type(self):
        for pkg in names(self.cfg, DEMOTED_MARKER):
            for update_type in ("digest", "patch", "minor", "major"):
                self.assertIsNot(
                    resolved(self.cfg, pkg, update_type).get("automerge"), True,
                    "%s is DEMOTED but still automerges a %s update" % (pkg, update_type))

    def test_no_package_is_both_graduated_and_demoted(self):
        graduated = {n for m in GRADUATED.values() for n in names(self.cfg, m)}
        self.assertEqual(graduated & set(names(self.cfg, DEMOTED_MARKER)), set(),
                         "the graduate job writes both lists; an overlap means one of its "
                         "two edits was skipped and the outcome depends on rule order")

    def test_lists_name_packages_that_exist(self):
        """`caddy` sat on GRADUATED while the manifest writes `library/caddy`: no-op, silent."""
        available = manifest_names()
        for marker in list(GRADUATED.values()) + [DEMOTED_MARKER]:
            for pkg in names(self.cfg, marker):
                self.assertIn(
                    pkg, available,
                    "%r (%r) names no image or chart in this repo. Renovate matches the "
                    "name AS WRITTEN in the manifest, so a near-miss spelling evaluates to "
                    "nothing and no rule says so (`library/caddy`, not `caddy`)" % (pkg, marker))

    def test_no_automerge_rule_matches_everything(self):
        """An automerge rule with no matcher at all would automerge the whole estate."""
        for i, rule_ in enumerate(self.cfg["packageRules"]):
            if "automerge" not in rule_:
                continue
            matchers = [k for k in rule_ if k.startswith("match")]
            self.assertNotEqual(
                matchers, [], "packageRules[%d] sets automerge and has no matcher at all"
                             % i)
            if "matchPackageNames" in rule_:
                self.assertNotEqual(
                    rule_["matchPackageNames"], [],
                    "packageRules[%d] has an empty matchPackageNames; use the %r sentinel so "
                    "the no-op state does not depend on engine behaviour" % (i, SENTINEL))

    def test_ticks_are_defined_once_for_every_mr(self):
        """One tick block, top level: a graduated (automerging) MR must offer `never` too."""
        text = (fleetlib.repo_root() / "renovate.json").read_text(encoding="utf-8")
        levels = TICK.findall(text)
        self.assertEqual(
            sorted(levels), ["digest", "major", "minor", "never"],
            "the graduation ticks must appear exactly once each, in the top-level "
            "prBodyNotes — per-rule copies make them drift, and a rule-level copy is "
            "invisible on an automerging MR, which is where `never` is needed")
        for level in ("digest", "minor", "major", "never"):
            self.assertTrue(
                any("<!--grad:{{{depName}}}:" + level + "-->" in line
                    for line in self.cfg["prBodyNotes"]),
                "the %r tick is missing from the top-level block" % level)


if __name__ == "__main__":
    unittest.main()
