#!/usr/bin/env python3
"""The vendored GitLab chart's pin must be READ by Renovate, and must NEVER automerge.

WHY THIS FILE EXISTS (infra-h0xb)
The Core GitLab release (ns bootstrap) was the last hand-installed thing on this
cluster: `helm upgrade` from ~/bootstrap/gitlab-values.yaml, outside Fleet and outside
Renovate, so a GitLab CVE had no automated patch path. It is now a Fleet bundle, and the
chart is VENDORED under gitlab/charts/ because Fleet embeds a chart into the Bundle it
writes to etcd (remote = 2,079,280 B, over the FAIL mark; vendored+trimmed = ~495 KB).
See gitlab/fleet.yaml and tools/vendor-gitlab-chart.py.

That vendoring created this test's subject: `chart: ./charts/gitlab` is a LOCAL PATH, and
Renovate's `fleet` manager returns skipReason `local-chart` for those -- it checks the
path BEFORE it looks for a repo (verified in renovate 42.99.0,
modules/manager/fleet/extract.js), so a `chart: ./...` pin is invisible to the manager
that exists to track chart versions. A `repo:` cannot be added to make it visible without
undoing the vendoring. So the pin is read by a `custom.regex` manager off the
`# renovate:` line gitlab/fleet.yaml carries above `version:`.

⛔ THE FAILURE MODE THIS GUARDS IS SILENT, and it has two halves.
  1. A regex that stopped matching returns exactly the same green as one that found
     nothing. Renovate parses NO `# renovate:` comment syntax -- the fields come from the
     manager's *Template keys, and the comment is otherwise decorative -- so the ONLY
     thing tying that comment to this config is that matchStrings requires it verbatim.
     Edit the comment, or split the two lines, and the dependency quietly ceases to
     exist: no MR, no dependency-dashboard row, no log line. Hence the test runs the
     REAL regex from renovate.json against the REAL file, rather than a copy of either.
  2. An MR-only rule placed ABOVE the blanket minor+patch rule is overridden by it
     (last-match-wins per field) and the pin automerges -- a GitLab version jump with
     nobody reading the upgrade path. So the effective automerge is COMPUTED here by
     replaying the rules in order, not asserted from the presence of a rule.

⚠️ SCOPE, stated rather than hidden. This replays the packageRules that are IN
renovate.json. Renovate also APPENDS synthesized rules at runtime for OSV vulnerability
alerts (workers/repository/process/vulnerabilities.js, appendVulnerabilityPackageRules),
which can carry `force: {vulnerabilityAlerts}` and would win over everything here. That
is inert for this dependency and only for a reason worth knowing: `helm` is absent from
that map (crate/go/hackage/hex/maven/npm/nuget/packagist/pypi/rubygems), so no OSV alert
can ever be raised for a chart. Cite the version when re-checking -- the map MOVED in
44.x, and 44.139.0 is what CI runs: `util/vulnerability/ecosystem.js`
`datasourceToOsvEcosystem`, where 42.99.0 (local dry-runs only) has the same list inline
as `Vulnerabilities.datasourceEcosystemMap`. The CVE oracle for GitLab is Windmill
f/probe/gitlab_version_health, not Renovate. If `helm` is ever mapped there, this dep
gains a path that bypasses the rule below -- re-read this paragraph then;
tests/test_renovate_has_no_osv_eligible_dependency.py is written to fail first.
"""

from __future__ import annotations

import fnmatch
import json
import re
import unittest

import fleetlib

# The sibling test owns the `/regex/`-vs-bare-string reading of managerFilePatterns and
# the config loader; two copies of that encoding could drift, and a drifted copy here
# would silently test the wrong file.
from test_every_pinned_image_is_renovate_reachable import renovate_config, to_regex

FLEET_YAML = "gitlab/fleet.yaml"
CHART_YAML = "gitlab/charts/gitlab/Chart.yaml"
DEP_NAME = "gitlab"
REGISTRY_URL = "https://charts.gitlab.io/"
REVENDOR_CMD = "tools/vendor-gitlab-chart.py"

# Renovate's matchStrings are JavaScript regexes; Python spells a named group (?P<x>).
# Only `(?<name>` -- `(?<=` and `(?<!` are lookbehinds in both languages.
JS_NAMED_GROUP = re.compile(r"\(\?<([A-Za-z_][A-Za-z0-9_]*)>")

# Keep in step with renovate 42.99.0 modules/manager/custom/utils.js `validMatchFields`.
VALID_MATCH_FIELDS = ("depName", "packageName", "currentValue", "currentDigest",
                      "datasource", "versioning", "extractVersion", "registryUrl",
                      "depType", "indentation")

# What this test knows how to evaluate. A rule carrying any OTHER matcher is reported
# rather than ignored: see `effective_config`.
IMPLEMENTED_MATCHERS = ("matchManagers", "matchDepNames", "matchPackageNames",
                        "matchUpdateTypes", "matchDatasources", "matchFileNames")


def gitlab_manager(cfg: dict) -> dict:
    """The customManager that selects gitlab/fleet.yaml (must be exactly one)."""
    hits = []
    for cm in cfg.get("customManagers", []):
        for raw in cm.get("managerFilePatterns", []):
            if to_regex(raw).search(FLEET_YAML):
                hits.append(cm)
                break
    if len(hits) != 1:
        raise AssertionError(
            "expected exactly one customManager whose managerFilePatterns select %s, "
            "found %d -- a pin no manager selects is the failure infra-h0xb exists to "
            "close" % (FLEET_YAML, len(hits)))
    return hits[0]


def js_to_python(pattern: str) -> str:
    return JS_NAMED_GROUP.sub(r"(?P<\1>", pattern)


def extracted_deps(manager: dict, content: str) -> list[dict]:
    """Replay the manager's handleAny(): every matchString, 'g' flag, templates applied.

    Mirrors custom/regex/{strategies,utils}.js: the whole file is the haystack, a
    *Template key wins over a same-named capture group, and a dependency is DISCARDED
    unless isValidDependency() -- which needs (depName or packageName) AND (currentValue
    or currentDigest) AND datasource. That filter is why a capture group that quietly
    stopped matching produces an empty list and not a broken dep.
    """
    deps: list[dict] = []
    for match_string in manager.get("matchStrings", []):
        rx = re.compile(js_to_python(match_string))  # reno regEx(matchString, "g")
        for match in rx.finditer(content):
            groups = match.groupdict()
            dep: dict[str, str] = {}
            for field in VALID_MATCH_FIELDS:
                template = manager.get(field + "Template")
                if template:
                    dep[field] = template
                elif groups.get(field):
                    dep[field] = groups[field]
            if ((dep.get("depName") or dep.get("packageName"))
                    and (dep.get("currentValue") or dep.get("currentDigest"))
                    and dep.get("datasource")):
                deps.append(dep)
    return deps


def string_match(value: str, patterns: list[str]) -> bool:
    """Renovate's matchRegexOrGlobList: `/re/` is a regex, anything else is a glob."""
    for pattern in patterns:
        if pattern.startswith("/") and pattern.endswith("/") and len(pattern) > 1:
            if re.search(pattern[1:-1], value):
                return True
        elif fnmatch.fnmatch(value, pattern):
            return True
    return False


def rule_matches(rule: dict, dep: dict) -> bool | None:
    """True / False on the matchers this test implements, None if it cannot tell.

    None means: every matcher it DOES understand matched, but the rule also carries one
    it does not. Renovate ANDs matchers, so an unimplemented one could still exclude the
    dep -- the caller decides what to do with that uncertainty rather than guessing.
    """
    matchers = [k for k in rule if k.startswith("match")]
    if not matchers:
        return True
    for key in matchers:
        if key not in IMPLEMENTED_MATCHERS:
            continue
        wanted = rule[key]
        if key == "matchManagers":
            ok = dep.get("manager") in wanted
        elif key == "matchDepNames":
            ok = bool(dep.get("depName")) and string_match(dep["depName"], wanted)
        elif key == "matchPackageNames":
            # PackageNameMatcher returns false when packageName is unset -- it does NOT
            # fall back to depName. The litellm trap; a rule that relies on this without
            # a packageNameTemplate never matches and never says so.
            ok = bool(dep.get("packageName")) and string_match(dep["packageName"], wanted)
        elif key == "matchUpdateTypes":
            ok = dep.get("updateType") in wanted
        elif key == "matchDatasources":
            ok = dep.get("datasource") in wanted
        elif key == "matchFileNames":
            ok = any(fnmatch.fnmatch(dep["packageFile"], p) for p in wanted)
        else:  # pragma: no cover - IMPLEMENTED_MATCHERS is the guard
            raise AssertionError("unhandled matcher %s" % key)
        if not ok:
            return False
    return None if any(m not in IMPLEMENTED_MATCHERS for m in matchers) else True


def effective_config(cfg: dict, dep: dict) -> tuple[dict, list[dict]]:
    """Replay packageRules in order, last-match-wins per field (Renovate's semantics).

    Returns (resolved fields, ambiguous rules). An ambiguous rule matched on every
    matcher this test understands but also carries one it does not -- it is returned
    rather than applied, so the caller can insist that no such rule touches `automerge`.
    """
    resolved: dict = {}
    ambiguous: list[dict] = []
    for rule in cfg.get("packageRules", []):
        verdict = rule_matches(rule, dep)
        if verdict is None:
            ambiguous.append(rule)
            continue
        if not verdict:
            continue
        for key, value in rule.items():
            if key.startswith("match") or key == "description":
                continue
            if key == "addLabels":
                resolved["labels"] = sorted(set(resolved.get("labels", [])) | set(value))
            else:
                resolved[key] = value
    return resolved, ambiguous


def chart_version() -> str:
    """The version the vendored tree actually is -- Chart.yaml, not the marker."""
    text = (fleetlib.repo_root() / CHART_YAML).read_text(encoding="utf-8")
    match = re.search(r"(?m)^version:\s*(\S+)\s*$", text)
    if not match:
        raise AssertionError("%s has no top-level version:" % CHART_YAML)
    return match.group(1)


def gitlab_dep() -> tuple[dict, dict]:
    """The extracted dependency, with the manager/file context the rules match on."""
    cfg = renovate_config()
    manager = gitlab_manager(cfg)
    content = (fleetlib.repo_root() / FLEET_YAML).read_text(encoding="utf-8")
    deps = extracted_deps(manager, content)
    if len(deps) != 1:
        raise AssertionError(
            "the customManager for %s extracted %d dependencies, expected exactly 1 -- "
            "0 means the `# renovate:` marker line it matches on verbatim has been "
            "edited, reworded or split (the pin is then tracked by NOTHING); >1 means "
            "two pins are being read as one" % (FLEET_YAML, len(deps)))
    dep = dict(deps[0])
    dep["manager"] = "custom.regex"
    dep["packageFile"] = FLEET_YAML
    dep["updateType"] = "minor"  # the update type the blanket rule automerges
    return cfg, dep


class GitlabChartRenovateReachableTest(unittest.TestCase):

    def test_the_regex_matches_this_file_and_no_other(self):
        """The file pattern must select this ONE file, and the regex must fire on it."""
        cfg = renovate_config()
        manager = gitlab_manager(cfg)
        selected = [f for f in fleetlib.tracked_files()
                    if any(to_regex(p).search(f)
                           for p in manager.get("managerFilePatterns", []))]
        self.assertEqual([FLEET_YAML], selected,
                         "the gitlab customManager's managerFilePatterns select %r, "
                         "expected exactly [%r] -- a pattern loose enough to catch the "
                         "vendored tree under gitlab/charts/ would hand Renovate 209 "
                         "upstream files this estate never deploys"
                         % (selected, FLEET_YAML))
        self.assertEqual(1, len(extracted_deps(
            manager, (fleetlib.repo_root() / FLEET_YAML).read_text(encoding="utf-8"))))

    def test_the_marker_comment_is_load_bearing(self):
        """Losing the comment must DROP the dependency -- that is why the test exists.

        Renovate does not parse `# renovate:` lines, so this assertion is the only thing
        that makes the comment and the config one fact instead of two.
        """
        cfg = renovate_config()
        manager = gitlab_manager(cfg)
        content = (fleetlib.repo_root() / FLEET_YAML).read_text(encoding="utf-8")
        self.assertIn("# renovate:", content,
                      "%s carries no `# renovate:` marker line -- the regex matches on "
                      "it verbatim, so the pin is now tracked by nothing" % FLEET_YAML)
        self.assertIn("datasource=helm", manager["matchStrings"][0],
                      "the marker line must stay named in matchStrings, not just in the "
                      "*Template keys: it is the only coupling between the comment in "
                      "gitlab/fleet.yaml and this config")
        # A reworded or deleted marker must change the outcome, or the coupling is fake.
        for poisoned in ("", content.replace("# renovate:", "# renovate :"),
                         content.replace("  version: ", "  chartVersion: ")):
            self.assertEqual(
                [], extracted_deps(manager, poisoned),
                "the regex still extracts a dependency from a file whose marker or "
                "`version:` line has been changed -- it is no longer anchored to what "
                "gitlab/fleet.yaml actually contains")

    def test_the_scripts_and_templates_name_a_helm_chart_at_the_gitlab_registry(self):
        cfg = renovate_config()
        manager = gitlab_manager(cfg)
        self.assertEqual("helm", manager.get("datasourceTemplate"))
        self.assertEqual(REGISTRY_URL, manager.get("registryUrlTemplate"))
        self.assertEqual(DEP_NAME, manager.get("depNameTemplate"))
        # Both, deliberately: matchPackageNames returns FALSE for an unset packageName
        # rather than falling back to depName, so a rule relying on it would never match.
        self.assertEqual(DEP_NAME, manager.get("packageNameTemplate"),
                         "packageNameTemplate must be set: without it packageName is "
                         "unset and any matchPackageNames rule for this dep silently "
                         "stops matching (Renovate PackageNameMatcher)")
        _cfg, dep = gitlab_dep()
        self.assertEqual(DEP_NAME, dep["depName"])
        self.assertEqual("helm", dep["datasource"])
        self.assertEqual(REGISTRY_URL, dep["registryUrl"])
        self.assertRegex(dep["currentValue"], r"^\d+\.\d+\.\d+$")

    def test_the_version_read_out_of_fleet_yaml_is_the_vendored_tree(self):
        """regex -> gitlab/fleet.yaml -> gitlab/charts/ must be one fact, end to end."""
        _cfg, dep = gitlab_dep()
        self.assertEqual(
            chart_version(), dep["currentValue"],
            "the pin Renovate reads from %s is %s but the vendored tree is %s -- a bump "
            "Renovate cannot see (or a pin nobody can move) is the state infra-h0xb "
            "exists to end; re-run %s" % (FLEET_YAML, dep["currentValue"],
                                          chart_version(), REVENDOR_CMD))

    def test_the_effective_automerge_for_this_dependency_is_false(self):
        """Replayed, not asserted from a rule's presence: position is what decides."""
        cfg, dep = gitlab_dep()
        resolved, ambiguous = effective_config(cfg, dep)

        offenders = [r.get("description", "?")[:120] for r in ambiguous
                     if "automerge" in r]
        self.assertEqual([], offenders,
                         "a packageRule matched on every matcher this test implements "
                         "and also carries one it does not, while setting `automerge` -- "
                         "extend IMPLEMENTED_MATCHERS before trusting the result below: %r"
                         % offenders)

        self.assertIs(False, resolved.get("automerge"),
                      "a %s update of `%s` resolves to automerge=%r; the blanket "
                      "minor+patch rule only sets automerge=true, so the MR-only rule "
                      "for this dep must sit BELOW it (last-match-wins per field)"
                      % (dep["updateType"], DEP_NAME, resolved.get("automerge")))
        self.assertNotIn("automerge", resolved.get("labels", []),
                         "the dep also carries the automerge LABEL, which the CI "
                         "mergeability recheck and the bot both read")
        self.assertIn("mr-only", resolved.get("labels", []))
        self.assertIsNot(False, resolved.get("enabled", True),
                         "the dep resolves to enabled=false, so it produces no MR at "
                         "all -- safer than automerge, but it means nothing tracks it")
        # A soak is the second half of the policy: the helm datasource publishes a
        # releaseTimestamp per version, so unlike the ghcr pins this one really waits.
        self.assertEqual("3 days", resolved.get("minimumReleaseAge"))

    def test_the_matching_rule_states_what_the_operator_must_run(self):
        """The MR body IS the operator instruction; it has to carry the command."""
        cfg, dep = gitlab_dep()
        matches = [r for r in cfg.get("packageRules", [])
                   if r.get("matchManagers") == ["custom.regex"]
                   and r.get("matchDepNames") == [DEP_NAME]]
        self.assertEqual(1, len(matches),
                         "expected exactly one packageRule for %s under custom.regex, "
                         "found %d" % (DEP_NAME, len(matches)))
        rule = matches[0]
        blurb = " ".join([rule.get("description", "")]
                         + list(rule.get("prBodyNotes", []) or []))
        self.assertIn(REVENDOR_CMD, blurb,
                      "the rule must name the command that moves both halves of the pin "
                      "(%s); Renovate moves gitlab/fleet.yaml only" % REVENDOR_CMD)
        self.assertIn("/gitlab-upgrade", blurb,
                      "the rule must point at the upgrade runbook: the newest chart "
                      "version is NOT the next legal hop (GitLab required stops)")
        self.assertIn("{{{depName}}}", " ".join(rule.get("prBodyNotes", []) or []),
                      "the prBodyNotes must keep their grad markers -- Windmill "
                      "f/desk/renovate_graduate reads them")

    def test_the_fleet_manager_reads_this_file_and_skips_it(self):
        """The reason a customManager is needed, asserted rather than assumed.

        `chart:` being a local path is what makes the fleet manager return skipReason
        `local-chart` (renovate 42.99.0 modules/manager/fleet/extract.js checks the path
        BEFORE the repo). The manager still SELECTS the file -- so the dep shows up in
        the dependency dashboard as skipped rather than being invisible, which is worth
        knowing when reading a dashboard that looks empty.
        """
        cfg = renovate_config()
        fleet_patterns = [to_regex(p)
                          for p in (cfg.get("fleet") or {}).get("managerFilePatterns", [])]
        self.assertTrue(any(rx.search(FLEET_YAML) for rx in fleet_patterns),
                        "the fleet manager no longer selects %s -- if that is intended, "
                        "this whole manager needs rethinking, not just the comment"
                        % FLEET_YAML)
        fleet_yaml = (fleetlib.repo_root() / FLEET_YAML).read_text(encoding="utf-8")
        chart = re.search(r"(?m)^\s*chart:\s*(\S+)\s*$", fleet_yaml).group(1)
        self.assertTrue(chart.startswith(("./", "/")),
                        "gitlab/fleet.yaml's chart is %r; the fleet manager would read "
                        "the remote chart version directly and the pin would need no "
                        "custom regex -- and Fleet would embed the 2,079,280 B upstream "
                        "chart in the Bundle, which is what the vendoring exists to "
                        "avoid" % chart)
        self.assertIn("gitlab/charts/**", cfg.get("ignorePaths", []),
                      "the vendored tree must stay in ignorePaths: the kubernetes "
                      "manager matches every .ya?ml and helm-values every values.ya?ml, "
                      "so without it the 209 upstream files become Renovate's")


if __name__ == "__main__":
    unittest.main()
