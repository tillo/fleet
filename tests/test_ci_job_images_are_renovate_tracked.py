#!/usr/bin/env python3
"""Every CI job image is digest-pinned and read by a Renovate manager (bead infra-iq68).

WHY THIS FILE EXISTS (2026-10-21)
`gitlabci` was missing from renovate.json's enabledManagers, so NOTHING in .gitlab-ci.yml
was tracked, and the sibling image tests skip the file on purpose (it is not a Kubernetes
workload). Four refs sat outside every updater: `python:3.13-alpine` and `alpine:3` on
bare line tags with no digest, the gitleaks scanner that gates the public mirror bumped
by hand, and the Renovate runner itself on `ghcr.io/renovatebot/renovate:latest`. The
runner is the sharpest case: this config's own descriptions cite 44.139.0 as the version
CI runs, and by 2026-10-21 job 141478 was running 44.174.0 -- 35 releases of the tool
that decides every automerge here, with no commit to diff or revert.

WHAT IS ASSERTED
  1. `gitlabci` is enabled and nothing in ignorePaths hides the file from it;
  2. every job image is a ref Renovate can resolve -- no CI variable other than the
     dependency-proxy prefix, which the manager strips -- and carries a digest, so a
     moved tag is a commit;
  3. the Renovate runner names a full version, not an alias or a line;
  4. a gitleaks VERSION bump never automerges: it is grouped with the
     GITLEAKS_VERSION/GITLEAKS_SHA256 pair in public-mirror (infra-i8ja tracks the pair,
     so one MR carries both scanners).
"""

from __future__ import annotations

import fnmatch
import re
import unittest

import yaml

import fleetlib
from test_every_pinned_image_is_renovate_reachable import is_pinned, renovate_config, split_ref
from test_gitlab_chart_is_renovate_reachable import effective_config

CI_FILE = ".gitlab-ci.yml"
RUNNER = "ghcr.io/renovatebot/renovate"
GITLEAKS = "zricethezav/gitleaks"

# The prefix Renovate's gitlabci manager strips before lookup (modules/manager/gitlabci/
# utils.ts `depProxyRe`). Any OTHER variable in a ref is a lookup Renovate skips.
DEP_PROXY_RE = re.compile(r"^\$\{?CI_DEPENDENCY_PROXY_(?:DIRECT_)?GROUP_IMAGE_PREFIX\}?/")


def image_name(value) -> str | None:
    """`image:` is a string or `{name: ...}`; services are lists of either."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("name"), str):
        return value["name"]
    return None


def ci_images() -> list[tuple[str, str]]:
    """(job, ref) for every image and service in .gitlab-ci.yml, dep-proxy prefix stripped."""
    doc = yaml.safe_load((fleetlib.repo_root() / CI_FILE).read_text(encoding="utf-8"))
    out = []
    for job, body in doc.items():
        if not isinstance(body, dict):
            continue
        refs = [image_name(body.get("image"))]
        refs += [image_name(s) for s in body.get("services") or []]
        out += [(job, DEP_PROXY_RE.sub("", r)) for r in refs if r]
    return out


def resolve_current_value_rules(cfg: dict, current_value: str) -> dict:
    """Settle `matchCurrentValue` (which effective_config cannot) against one tag."""
    rules = []
    for rule in cfg["packageRules"]:
        pattern = rule.get("matchCurrentValue")
        if pattern is None:
            rules.append(rule)
        elif re.search(pattern.strip("/"), current_value):
            rules.append({k: v for k, v in rule.items() if k != "matchCurrentValue"})
    return dict(cfg, packageRules=rules)


class CiJobImagesAreRenovateTracked(unittest.TestCase):

    def test_the_gitlabci_manager_reads_this_file(self):
        cfg = renovate_config()
        self.assertIn("gitlabci", cfg.get("enabledManagers", []),
                      "gitlabci is not an enabled manager, so no image in %s has an updater "
                      "-- the state this file exists to prevent" % CI_FILE)
        hidden = [g for g in cfg.get("ignorePaths", []) if fnmatch.fnmatch(CI_FILE, g)]
        self.assertEqual([], hidden, "ignorePaths hides %s from every manager" % CI_FILE)

    def test_every_job_image_is_resolvable_and_digest_pinned(self):
        images = ci_images()
        self.assertGreaterEqual(len(images), 4,
                                "found only %r -- the job/image parse is broken, not clean"
                                % images)
        self.assertTrue(any(split_ref(r)[0] == RUNNER for _, r in images),
                        "no `%s` job image found -- the runner moved or was renamed" % RUNNER)

        unresolvable = [(j, r) for j, r in images if "$" in r]
        self.assertEqual([], unresolvable,
                         "these refs carry a CI variable Renovate cannot expand, so it skips "
                         "them with no MR and no dashboard row: %r" % unresolvable)

        undigested = [(j, r) for j, r in images if not split_ref(r)[2]]
        self.assertEqual([], undigested,
                         "these job images carry no @sha256, so the build a job runs can change "
                         "with no commit. Pin tag@digest; Renovate keeps the digest current: %r"
                         % undigested)

    def test_the_renovate_runner_names_a_version(self):
        refs = [r for _, r in ci_images() if split_ref(r)[0] == RUNNER]
        self.assertNotEqual([], refs, "no `%s` job image found" % RUNNER)
        for ref in refs:
            tag = split_ref(ref)[1]
            self.assertTrue(is_pinned(tag),
                            "the Renovate runner is `%s` -- an alias or a line moves the tool "
                            "that decides every automerge here without a commit; pin X.Y.Z"
                            % ref)

    def test_a_gitleaks_version_bump_is_never_automerged(self):
        refs = [r for _, r in ci_images() if split_ref(r)[0] == GITLEAKS]
        self.assertNotEqual([], refs, "no `%s` job image found" % GITLEAKS)
        for ref in refs:
            _, tag, _ = split_ref(ref)
            cfg = resolve_current_value_rules(renovate_config(), tag)
            for update_type in ("major", "minor", "patch"):
                dep = {"manager": "gitlabci", "depName": GITLEAKS, "packageName": GITLEAKS,
                       "packageFile": CI_FILE, "datasource": "docker",
                       "updateType": update_type}
                resolved, ambiguous = effective_config(cfg, dep)
                offenders = [r.get("description", "?")[:120] for r in ambiguous
                             if "automerge" in r]
                self.assertEqual([], offenders,
                                 "a rule carrying a matcher effective_config does not implement "
                                 "sets automerge -- extend IMPLEMENTED_MATCHERS first: %r"
                                 % offenders)
                self.assertIsNot(True, resolved.get("automerge"),
                                 "a %s bump of %s resolves to automerge, but public-mirror's "
                                 "the scanner image and the GITLEAKS_VERSION/GITLEAKS_SHA256 "
                                 "pair in public-mirror move as one MR-only change"
                                 % (update_type, GITLEAKS))


if __name__ == "__main__":
    unittest.main()
