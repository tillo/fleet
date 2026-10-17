#!/usr/bin/env python3
"""The security-automerge flags in renovate.json must stay HONEST: nothing here is OSV-eligible.

WHY THIS FILE EXISTS (infra-jypn, found 2026-10-06 while vendoring the GitLab chart)
From 2026-06-29 until 2026-10-07 renovate.json's own top-level description read
"Security fixes auto-merge at any time via vulnerabilityAlerts", and the commit that put
it there said the flags were chosen because they "cover Docker/Helm advisories". Neither
was true; no advisory has ever auto-merged. Renovate's OSV alerts are LANGUAGE-ONLY: the
datasource->ecosystem map covers clojure/crate/go/golang-version/hackage/hex/maven/npm/
nuget/packagist/pypi/rubygems, and `fetchDependencyVulnerability()` returns null
('Cannot map datasource ... to OSV ecosystem') for anything outside it. Every dependency
this repo has is datasource `docker` or `helm`, so `osvVulnerabilityAlerts` can raise
ZERO alerts and there is no unattended CVE merge door for an image or a chart.

The incident is not the flag -- the flag is the correct posture in general. The incident
is that the config file claimed a security control the runtime could not deliver, and the
claim survived every audit because a `description` string cannot fail. So the premise is
asserted here instead, next to the description that depends on it.

WHAT IS ASSERTED, and why it is not vacuous. Two independent things make the block inert,
and BOTH are premises that can silently stop holding:
  1. no manager enabled in `enabledManagers` resolves dependencies against an OSV-mapped
     datasource;
  2. no language package manifest is tracked in this repo at all.
Either one changing is the moment the security story in renovate.json has to be re-read,
so the test fails then -- loudly -- rather than letting the belief go quietly wrong
again. It also fails when `enabledManagers` gains a manager nobody mapped below, so
adding a manager is a decision rather than a default.

⚠️ The datasource map is the part that rots. It records what these managers emit IN THIS
REPO, not what they can emit in general -- and because the map is read from the manager
names, a manager that starts emitting a new datasource would have to come from a manifest
the second half of this test scans for first.
"""

from __future__ import annotations

import fnmatch
import os
import unittest

import fleetlib
from test_every_pinned_image_is_renovate_reachable import renovate_config

# OSV-mappable datasources, exactly as Renovate 44.139.0 lists them in
# `util/vulnerability/ecosystem.js` `datasourceToOsvEcosystem`. Renovate 42.99.0 (the
# local dry-run version) carries the same list inline as `Vulnerabilities
# .datasourceEcosystemMap` in workers/repository/process/vulnerabilities.js. There is no
# `docker` and no `helm` entry in either -- that absence is the whole finding.
OSV_DATASOURCES = frozenset({
    "clojure", "crate", "go", "golang-version", "hackage", "hex",
    "maven", "npm", "nuget", "packagist", "pypi", "rubygems",
})

# What each manager enabled in renovate.json resolves dependencies against, here.
# `custom.regex` is the exception: its datasource is declared per customManager, so it
# is collected from the config rather than hardcoded (see custom_regex_datasources).
MANAGER_DATASOURCES = {
    "kubernetes": frozenset({"docker"}),
    "helm-values": frozenset({"docker"}),
    "fleet": frozenset({"helm"}),
    "custom.regex": frozenset(),
}
CUSTOM_REGEX_MANAGER = "custom.regex"

# A tracked file with one of these names (or a suffix in LANGUAGE_MANIFEST_SUFFIXES) is a
# language package manifest -- something an OSV-eligible manager could read. None exists
# today; one appearing is the signal to re-read the security story, not necessarily a bug.
LANGUAGE_MANIFEST_NAMES = frozenset({
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
    "go.mod", "go.sum", "pom.xml", "build.gradle", "build.gradle.kts",
    "Cargo.toml", "Cargo.lock", "composer.json", "composer.lock",
    "Gemfile", "Gemfile.lock", "Pipfile", "Pipfile.lock",
    "pyproject.toml", "setup.py", "setup.cfg", "mix.exs",
})
LANGUAGE_MANIFEST_PATTERNS = ("requirements*.txt", "*.gemspec", "*.nuspec",
                              "*.csproj", "*.fsproj", "*.cabal")

REREAD = ("Re-read the vulnerabilityAlerts description in renovate.json and the top-level "
          "description sentence that points at it: the estate has no unattended CVE path "
          "for images or charts (infra-jypn). If this change made one real, say so there; "
          "if it did not, adjust this test's premise instead of deleting it.")


def custom_regex_datasources(cfg: dict) -> set[str]:
    """Datasources the custom.regex managers declare, straight from the config."""
    return {cm.get("datasourceTemplate")
            for cm in cfg.get("customManagers", [])
            if cm.get("datasourceTemplate")}


def language_manifests() -> list[str]:
    """Tracked, non-vendored files that look like a language package manifest."""
    out = []
    for rel in fleetlib.tracked_files():
        if fleetlib.is_vendored(rel):
            continue
        base = os.path.basename(rel)
        if base in LANGUAGE_MANIFEST_NAMES or any(
                fnmatch.fnmatch(base, pat) for pat in LANGUAGE_MANIFEST_PATTERNS):
            out.append(rel)
    return sorted(out)


class RenovateHasNoOsvEligibleDependency(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = renovate_config()

    def test_every_enabled_manager_is_mapped(self) -> None:
        enabled = set(self.cfg.get("enabledManagers", []))
        self.assertTrue(enabled, "enabledManagers is empty -- the config's scope is unstated")
        self.assertEqual(
            enabled, set(MANAGER_DATASOURCES),
            "enabledManagers changed. Decide which datasources the new manager emits, add "
            "it to MANAGER_DATASOURCES, then confirm the vulnerabilityAlerts story still "
            "holds -- enabling a language manager is the one change that makes it live.\n" + REREAD)

    def test_no_enabled_manager_uses_an_osv_datasource(self) -> None:
        enabled = set(self.cfg.get("enabledManagers", []))
        regex_datasources = custom_regex_datasources(self.cfg)

        # An empty selection is not a pass: if custom.regex is enabled it must declare a
        # datasource (Renovate discards a regex dep with no datasource, silently).
        if CUSTOM_REGEX_MANAGER in enabled:
            self.assertTrue(
                regex_datasources,
                "custom.regex is enabled but no customManager declares a datasourceTemplate "
                "-- Renovate would silently discard those dependencies, so this test would "
                "be checking nothing.")

        used: set[str] = set()
        for manager in enabled:
            if manager == CUSTOM_REGEX_MANAGER:
                used |= regex_datasources
                continue
            datasources = MANAGER_DATASOURCES.get(manager)
            self.assertIsNotNone(
                datasources,
                f"enabled manager {manager!r} is not mapped here, so its datasources were "
                f"never classified against the OSV map.\n" + REREAD)
            used |= datasources

        self.assertTrue(used, "no datasource found for any enabled manager -- checking nothing")
        # Guard the guard: every datasource we computed must be one we recognise, so a
        # typo or a new template cannot slip past as "not OSV-mapped".
        self.assertFalse(
            used - {"docker", "helm"},
            f"unrecognised datasource(s) {sorted(used - {'docker', 'helm'})}: classify them "
            f"against the OSV map before trusting this test.\n" + REREAD)

        overlap = used & OSV_DATASOURCES
        self.assertFalse(
            overlap,
            f"enabled managers now resolve dependencies via OSV-mapped datasource(s) "
            f"{sorted(overlap)}. vulnerabilityAlerts/osvVulnerabilityAlerts is no longer "
            f"inert -- the security lane is LIVE for those dependencies, and any packageRule "
            f"that assumed nobody auto-merges on an advisory must be re-checked.\n" + REREAD)

    def test_no_language_manifest_is_tracked(self) -> None:
        found = language_manifests()
        self.assertFalse(
            found,
            "language package manifest(s) are now tracked in this repo: "
            f"{found}. A manifest alone does not enable the security lane (no language "
            "manager is in enabledManagers), but it is the first half of the change that "
            "would, and the vulnerabilityAlerts description says none exists.\n" + REREAD)


if __name__ == "__main__":
    unittest.main()
