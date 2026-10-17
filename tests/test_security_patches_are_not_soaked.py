#!/usr/bin/env python3
"""A SECURITY patch must never wait on the Renovate soak (owner policy 2026-06-29).

WHY THIS FILE EXISTS (bead infra-6gg9, measured 2026-10-13/14)
renovate.json has carried the line "Security fixes bypass the window" since 2026-06-29,
and until 2026-10-14 nothing implemented it -- because in this repo it CANNOT be
implemented the usual way. Renovate's vulnerability alerts are provably inert here: the
OSV datasource->ecosystem map has no `docker` and no `helm` entry, and GitLab offers no
Dependabot path, so every dependency this repo has (all datasource docker or helm) sits
outside the only mechanism that bypasses a soak. See
tests/test_renovate_has_no_osv_eligible_dependency.py, which asserts those premises.

The consequences were measured, not theorised. On 2026-10-12 GitLab published a security
release (chart 10.4.2 / v19.4.2, shipped alongside 19.3.5 and 19.2.8).
version.gitlab.com reported the version this estate ran (19.4.1) as severity=danger with
critical_vulnerability=true, and Renovate had opened NO MR for the fix at all, because
the chart was ~1.6 days old against a 3-day `minimumReleaseAge`. The soak delayed the
NOTIFICATION, not merely the merge. Nothing anywhere said so, because a `description`
string cannot fail.

So the exemption is expressed the only way this repo can express it: by UPDATE TYPE.
Security fixes ship as PATCH releases, and a DIGEST change is the rebuild path by which a
floating-tag image picks up base-image security updates at an unchanged version.

WHAT IS ASSERTED, and why it is not vacuous. Two things, and the second is the one that
rots:
  1. a PATCH update of the measured dependency resolves to no soak, and so does a DIGEST
     update of a Lane-A image;
  2. the rule achieving that sits BELOW every rule declaring a non-zero soak -- because
     packageRules are last-match-wins PER FIELD, so moving it up, or appending a new
     soaking rule after it, silently restores the very delay this file exists to prevent.
It also asserts the exemption changes ONLY timing: it may not carry `automerge`, because
the claim in its description is that it alters when a release is adopted, never whether
it is reviewed.
"""

from __future__ import annotations

import unittest

from test_every_pinned_image_is_renovate_reachable import renovate_config
from test_gitlab_chart_is_renovate_reachable import effective_config, gitlab_dep

# Anchored on a phrase in the rule's own description rather than on its index, so the
# test still points at the right rule if the list above it grows.
EXEMPTION_MARKER = "SECURITY FIXES MUST NOT BE DELAYED"

NO_SOAK = (None, "0 days")


class SecurityPatchesAreNotSoaked(unittest.TestCase):

    def test_a_patch_update_of_the_gitlab_chart_resolves_to_no_soak(self):
        """The measured case: this is the dependency that was left unpatched."""
        cfg, dep = gitlab_dep()
        dep = dict(dep, updateType="patch")
        resolved, ambiguous = effective_config(cfg, dep)

        offenders = [r.get("description", "?")[:120] for r in ambiguous
                     if "minimumReleaseAge" in r]
        self.assertEqual([], offenders,
                         "a packageRule matched on every matcher this test implements and also "
                         "carries one it does not, while setting `minimumReleaseAge` -- extend "
                         "IMPLEMENTED_MATCHERS before trusting the result below: %r" % offenders)

        self.assertIn(resolved.get("minimumReleaseAge"), NO_SOAK,
                      "a PATCH update of `%s` still resolves to a soak of %r. Security fixes "
                      "ship as patch releases, and Renovate can see a vulnerability for NO "
                      "dependency in this repo, so a soak here is a delay applied to the fix "
                      "itself -- see %s" % (dep["depName"], resolved.get("minimumReleaseAge"),
                                            EXEMPTION_MARKER))

    def test_a_digest_update_resolves_to_no_soak(self):
        """The second half of the policy, and the one rule 2 (LANE-A) would otherwise hold.

        A digest change re-points a floating tag at a rebuilt image at the SAME version --
        which is how a base-image security update reaches this estate at all. The LANE-A
        rule soaks those by 1 day, and unlike the ghcr pins (which publish no
        releaseTimestamp, so `timestamp-optional` already let them through) the Docker Hub
        and registry.mdapi.ch tags this matches DO publish one, so the soak really bit.
        """
        dep = {
            "manager": "kubernetes",
            "depName": "registry.mdapi.ch/mdapi/example",
            "packageName": "registry.mdapi.ch/mdapi/example",
            "packageFile": "example/deploy.yaml",
            "datasource": "docker",
            "updateType": "digest",
        }
        resolved, ambiguous = effective_config(renovate_config(), dep)

        offenders = [r.get("description", "?")[:120] for r in ambiguous
                     if "minimumReleaseAge" in r]
        self.assertEqual([], offenders,
                         "a packageRule matched on every matcher this test implements and also "
                         "carries one it does not, while setting `minimumReleaseAge` -- extend "
                         "IMPLEMENTED_MATCHERS before trusting the result below: %r" % offenders)

        self.assertIn(resolved.get("minimumReleaseAge"), NO_SOAK,
                      "a DIGEST update still resolves to a soak of %r. A digest change is the "
                      "rebuild path by which a floating-tag image picks up a base-image security "
                      "update at an unchanged version, so it must not wait either -- see %s"
                      % (resolved.get("minimumReleaseAge"), EXEMPTION_MARKER))

    def test_the_exemption_outranks_every_declared_soak(self):
        """Placement IS the mechanism: last-match-wins per field, so order is the control."""
        rules = renovate_config()["packageRules"]

        exempt = [i for i, r in enumerate(rules)
                  if EXEMPTION_MARKER in (r.get("description") or "")]
        self.assertEqual(1, len(exempt),
                         "expected exactly one packageRule carrying %r, found %d -- without it "
                         "every soak below applies to security patches again"
                         % (EXEMPTION_MARKER, len(exempt)))
        rule = rules[exempt[0]]

        self.assertEqual(["patch", "digest"], rule.get("matchUpdateTypes"),
                         "the exemption must be scoped to patch AND digest updates: a security "
                         "fix is a patch release, a digest change is the rebuild that carries a "
                         "base-image fix at an unchanged version, and widening past those two "
                         "would bypass the required-stop review the estate depends on")
        self.assertEqual("0 days", rule.get("minimumReleaseAge"))

        self.assertNotIn("automerge", rule,
                         "the exemption carries `automerge`, so it no longer only changes WHEN "
                         "a release is adopted -- it would also decide WHETHER it is reviewed")

        soaking = [i for i, r in enumerate(rules)
                   if r.get("minimumReleaseAge") not in NO_SOAK]
        self.assertNotEqual([], soaking,
                            "no rule declares a soak, so this test would prove nothing")

        below = [i for i in soaking if i > exempt[0]]
        self.assertEqual([], below,
                         "these packageRules declare a minimumReleaseAge BELOW the patch "
                         "exemption, and packageRules are last-match-wins per field -- so they "
                         "re-soak patches and the exemption silently stops working: %r"
                         % [rules[i].get("description", "?")[:90] for i in below])


if __name__ == "__main__":
    unittest.main()
