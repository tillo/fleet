#!/usr/bin/env python3
"""The curl-downloaded binaries in .gitlab-ci.yml are Renovate-tracked (bead infra-i8ja).

WHY THIS FILE EXISTS (2026-10-21)
`gitlabci` reads job IMAGES (infra-iq68); it has nothing to say about the tarballs a job
curls. fleet-validate downloads helm, kubectl, kubeconform and the fleet CLI, verifies each
with a pinned sha256, and public-mirror does the same for the gitleaks scanner -- nine
hand-maintained literals that no manager looked at, so a bump meant noticing a release
by hand AND computing its digest by hand.

WHAT IS ASSERTED
  1. every one of those five binaries is extracted, with the version AND (where possible)
     the sha256 in the same dependency, so one MR moves both;
  2. a NEW `*_VERSION`/`*_SHA256` pin in this file is a tracked dependency or an explicit,
     reasoned exemption -- never silently invisible again;
  3. the three GitHub-attached tarballs resolve their digest (github-release-attachments),
     and their rewrite template writes BOTH fields;
  4. helm and kubectl are version-only BY NECESSITY (no datasource can digest get.helm.sh
     or dl.k8s.io), so they must stay MR-only + dashboard-approved: `sha256sum -c` in the
     job is the fail-closed gate for the human step;
  5. kubectl's `allowedVersions` keeps it within one minor of the cluster (KUBE_VERSION);
  6. GITLEAKS_VERSION holds the TAG: the datasource resolves releases/tags/<currentValue>
     literally, so a bare "8.30.1" would 404 and leave the OLD sha on a bumped branch.
"""

from __future__ import annotations

import re
import unittest

import yaml

import fleetlib
from test_every_pinned_image_is_renovate_reachable import renovate_config, to_regex
from test_gitlab_chart_is_renovate_reachable import effective_config, extracted_deps

CI_FILE = ".gitlab-ci.yml"

# binary -> (repo, VERSION var, SHA var or None, datasource)
BINARIES = {
    "kubeconform": ("yannh/kubeconform", "KUBECONFORM_VERSION", "KUBECONFORM_SHA256",
                    "github-release-attachments"),
    "fleet-cli": ("rancher/fleet", "FLEET_VERSION", "FLEET_SHA256",
                  "github-release-attachments"),
    "gitleaks": ("gitleaks/gitleaks", "GITLEAKS_VERSION", "GITLEAKS_SHA256",
                 "github-release-attachments"),
    "helm": ("helm/helm", "HELM_VERSION", "HELM_SHA256", "github-releases"),
    "kubectl": ("kubernetes/kubernetes", "KUBECTL_VERSION", "KUBECTL_SHA256",
                "github-releases"),
}

# Pins that must stay OUT of Renovate's hands, each for a stated reason. A new pin that
# is neither in BINARIES nor here fails test_no_pin_is_invisible.
EXEMPT = {
    "KUBE_VERSION": "the CLUSTER's version (currently v1.35.2+rke2r1). Upstream's newest "
                    "release is not the right answer for it; it moves when the cluster does.",
    "HELM_SHA256": "no datasource can derive it: helm attaches no artifact to its GitHub "
                   "releases and get.helm.sh publishes no digest-carrying listing.",
    "KUBECTL_SHA256": "no datasource can derive it: kubernetes/kubernetes releases carry "
                      "no assets at all and dl.k8s.io publishes no digest listing.",
}

VARIABLE_LINE = re.compile(r'(?m)^\s+([A-Z][A-Z0-9_]*): "([^"]*)"$')


def ci_text() -> str:
    return (fleetlib.repo_root() / CI_FILE).read_text(encoding="utf-8")


def ci_variables(text: str | None = None) -> dict[str, str]:
    """Every `UPPER: "value"` line in the file -- job variables blocks, flattened."""
    out: dict[str, str] = {}
    for key, value in VARIABLE_LINE.findall(ci_text() if text is None else text):
        out[key] = value
    return out


def ci_managers(cfg: dict) -> list[dict]:
    """customManagers whose file pattern selects .gitlab-ci.yml."""
    return [cm for cm in cfg.get("customManagers", [])
            if any(to_regex(raw).search(CI_FILE)
                   for raw in cm.get("managerFilePatterns", []))]


def ci_deps(cfg: dict) -> dict[str, dict]:
    """depName -> extracted dependency, with the manager/file context rules match on."""
    text = ci_text()
    out: dict[str, dict] = {}
    for manager in ci_managers(cfg):
        for dep in extracted_deps(manager, text):
            dep = dict(dep, manager="custom.regex", packageFile=CI_FILE)
            out[dep["depName"]] = dep
    return out


def apply_auto_replace(manager: dict, dep: dict, new_value: str, new_digest: str) -> dict:
    """The file Renovate would write: autoReplace template spliced over the match.

    Applied to the REAL text, so indentation, quoting and the YAML block it lives in are
    all under test -- a template that loses the 4-space indent parses as broken YAML here
    rather than in a Renovate branch nobody reads.
    """
    text = ci_text()
    match = next(re.compile(re.sub(r"\(\?<([A-Za-z_][A-Za-z0-9_]*)>", r"(?P<\1>",
                                   manager["matchStrings"][0])).finditer(text))
    assert match.group("currentValue") == dep["currentValue"]
    rendered = (manager["autoReplaceStringTemplate"]
                .replace("{{{newValue}}}", new_value)
                .replace("{{{newDigest}}}", new_digest))
    return yaml.safe_load(text[:match.start()] + rendered + text[match.end():])


class CiBinaryPinsAreRenovateTracked(unittest.TestCase):

    def setUp(self):
        self.cfg = renovate_config()
        self.vars = ci_variables()
        self.deps = ci_deps(self.cfg)
        # manager lookup by depName, for the rewrite-template assertions
        self.managers = {d["depName"]: m for m in ci_managers(self.cfg)
                         for d in extracted_deps(m, ci_text())}

    def test_every_binary_is_one_dependency(self):
        for label, (repo, version_var, sha_var, datasource) in BINARIES.items():
            with self.subTest(binary=label):
                self.assertIn(repo, self.deps,
                              "%s (%s) is not extracted by any customManager selecting %s "
                              "-- a bump of it is invisible again" % (label, repo, CI_FILE))
                dep = self.deps[repo]
                self.assertEqual(self.vars[version_var], dep.get("currentValue"),
                                 "%s: the manager does not capture %s" % (label, version_var))
                self.assertEqual(datasource, dep.get("datasource"))
                self.assertEqual(repo, dep.get("packageName"),
                                 "%s: packageName must be set, or a matchPackageNames rule "
                                 "silently never matches this dependency (the litellm trap)"
                                 % label)
                if datasource == "github-release-attachments":
                    self.assertEqual(self.vars[sha_var], dep.get("currentDigest"),
                                     "%s: %s must be captured as currentDigest, or the "
                                     "bump cannot carry the sha" % (label, sha_var))

    def test_no_pin_is_invisible(self):
        """A new *_VERSION / *_SHA256 with no manager and no stated exemption is a failure."""
        tracked = {dep.get("currentValue") for dep in self.deps.values()} | {
            dep.get("currentDigest") for dep in self.deps.values()}
        orphans = []
        for key, value in self.vars.items():
            if not (key.endswith("_VERSION") or key.endswith("_SHA256")):
                continue
            if key in EXEMPT:
                self.assertTrue(EXEMPT[key].strip(), "%s is exempt with no reason" % key)
                continue
            if value not in tracked:
                orphans.append("%s: %r" % (key, value))
        self.assertEqual([], orphans,
                         "these binary pins have no Renovate manager and no exemption: %r. "
                         "Add a customManager (version, and the sha too if a datasource can "
                         "derive it) or an EXEMPT entry saying why nothing can track it."
                         % orphans)

    def test_the_tracked_digests_write_both_fields(self):
        for label, (repo, version_var, sha_var, datasource) in BINARIES.items():
            if datasource != "github-release-attachments":
                continue
            with self.subTest(binary=label):
                manager = self.managers[repo]
                rewritten = apply_auto_replace(
                    manager, self.deps[repo], "v9.9.9", "a" * 64)
                written = {k: v for job in rewritten.values()
                           if isinstance(job, dict) for k, v in (job.get("variables") or {}).items()}
                self.assertEqual({"v": "v9.9.9", "d": "a" * 64},
                                 {"v": written.get(version_var), "d": written.get(sha_var)},
                                 "%s: the rewrite template must set BOTH variables, at the "
                                 "variables-block indentation" % label)
                self.assertIn("{{{newDigest}}}", manager["autoReplaceStringTemplate"],
                              "%s: without newDigest the version would move and the sha "
                              "would stay -- a red or, worse, a stale pin" % label)

    def test_the_digestless_pins_are_mr_only_and_dashboard_approved(self):
        for label, (repo, _, sha_var, datasource) in BINARIES.items():
            if datasource != "github-releases":
                continue
            with self.subTest(binary=label):
                for update_type in ("major", "minor", "patch", "digest"):
                    dep = dict(self.deps[repo], updateType=update_type)
                    resolved, ambiguous = effective_config(self.cfg, dep)
                    self.assertEqual([], [r.get("description", "?")[:90] for r in ambiguous
                                          if {"automerge", "dependencyDashboardApproval"} & set(r)],
                                     "a rule with a matcher effective_config does not implement "
                                     "decides automerge for %s -- extend IMPLEMENTED_MATCHERS" % label)
                    self.assertIsNot(True, resolved.get("automerge"),
                                     "%s cannot carry its own sha256 (%s is not derivable), so a "
                                     "merged bump would red the pipeline or, if CI were ever "
                                     "relaxed, ship an unverified binary; it must not automerge"
                                     % (label, sha_var))
                    self.assertIs(True, resolved.get("dependencyDashboardApproval"),
                                  "%s must wait as a dashboard checkbox: an MR nobody asked for "
                                  "sits red until a human computes the sha" % label)

    def test_the_gitleaks_pair_moves_together(self):
        """The image (gitlabci) and the tarball (custom.regex) are ONE scanner, ONE MR.

        Half a bump means the image that judges the push and the binary that re-scans it
        disagree -- so neither may automerge a version, and both must share the group.
        """
        tarball = dict(self.deps["gitleaks/gitleaks"], updateType="minor")
        image = {"manager": "gitlabci", "depName": "zricethezav/gitleaks",
                 "packageName": "zricethezav/gitleaks", "packageFile": CI_FILE,
                 "datasource": "docker", "updateType": "minor"}
        t_resolved, _ = effective_config(self.cfg, tarball)
        i_resolved, _ = effective_config(self.cfg, image)
        for who, resolved in (("tarball", t_resolved), ("image", i_resolved)):
            self.assertIsNot(True, resolved.get("automerge"),
                             "a gitleaks version bump (%s) still automerges, so it can land "
                             "without the other half of the scanner" % who)
        self.assertIsNotNone(i_resolved.get("groupName"),
                             "the scanners are not grouped, so they arrive as separate MRs and "
                             "nothing keeps them level")
        self.assertEqual(i_resolved.get("groupName"), t_resolved.get("groupName"),
                         "image and tarball resolve to different groups")

    def test_kubectl_stays_within_skew_of_the_cluster(self):
        """Skew is +/-1 minor, so the cap is cluster+1 (inclusive) = cluster+2 (exclusive).

        The pin is already one minor AHEAD of the cluster (1.36 client, 1.35 server) --
        supported, but it is the edge, so the next minor must stay out.
        """
        cluster = self.vars["KUBE_VERSION"]
        major, minor = cluster.split(".")[:2]
        expected = "<%s.%d.0" % (major, int(minor) + 2)
        dep = dict(self.deps["kubernetes/kubernetes"], updateType="minor")
        resolved, _ = effective_config(self.cfg, dep)
        self.assertEqual(expected, resolved.get("allowedVersions"),
                         "kubectl is supported within ONE minor of the API server, so with the "
                         "cluster at %s the cap must be %r (1.%s.x is the last allowed line; "
                         "KUBE_VERSION is NOT tracked, so raise this in the commit that moves "
                         "the cluster)" % (cluster, expected, int(minor) + 1))

    def test_gitleaks_version_holds_the_tag(self):
        value = self.vars["GITLEAKS_VERSION"]
        self.assertRegex(value, r"^v\d+\.\d+\.\d+$",
                         "GITLEAKS_VERSION must be the release TAG (v-prefixed): the datasource "
                         "resolves releases/tags/<currentValue> literally, and on a bare version "
                         "the digest lookup 404s and the branch keeps the OLD sha")
        self.assertIn("${GITLEAKS_VERSION#v}", ci_text(),
                      "the tarball name drops the v prefix; without that expansion the download "
                      "URL gains a vv and the sha check never reaches the file")


if __name__ == "__main__":
    unittest.main()
