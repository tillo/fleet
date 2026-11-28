"""The mail image is pinned by a VERSIONED tag, and Renovate understands that version.

2026-11-24: a Renovate "Update image digests" MR moved the fleet pin of
`:latest@digest` BACK from the DMS 17 build (Dovecot 2.4) to the frozen Dovecot 2.3
build, because both digests had carried `:latest` within hours and a digest carries
no version. The image repo now mints `<DMS release>-mdapi.<pipeline>` (mdapi/
docker-mailserver `.version-tag`). For that to be safe, three things must hold
together, and this pins all three:

  * the fleet pin names such a tag, with a digest;
  * renovate.json reads that scheme for THIS package (without a versioning rule
    Renovate cannot order `17.0.0-mdapi.1566`, so it would offer nothing at all);
  * a rebuild (patch) or a new digest automerges with no soak, as before, while a
    new DMS release (minor/major) stays an MR.
"""

from __future__ import annotations

import json
import re
import unittest

from tests.fleetlib import repo_root

PIN = repo_root() / "mail" / "docker-mailserver" / "kustomize" / "deployment-image-pin.yaml"
RENOVATE = json.loads((repo_root() / "renovate.json").read_text())
PACKAGE = "zot.mdapi.ch/mdapi/docker-mailserver"
VERSION_TAG = re.compile(r"mailserver:(?P<tag>\d+\.\d+\.\d+-mdapi\.\d+)@sha256:[0-9a-f]{64}")


def rules_for(package: str) -> list[dict]:
    return [r for r in RENOVATE["packageRules"]
            if package in (r.get("matchPackageNames") or []) or package in (r.get("matchDepNames") or [])]


class MailImagePinIsVersioned(unittest.TestCase):
    def test_the_pin_is_a_version_tag_with_a_digest(self) -> None:
        m = VERSION_TAG.search(PIN.read_text())
        self.assertIsNotNone(m, "the pin is not <version>-mdapi.<n>@sha256:...")
        self.assertTrue(m.group("tag").startswith("17."), "mail is on DMS 17 (Dovecot 2.4)")

    def test_renovate_reads_that_version_scheme(self) -> None:
        versioning = [r["versioning"] for r in rules_for(PACKAGE) if "versioning" in r]
        self.assertIn(r"regex:^(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)-mdapi\.(?<build>\d+)$",
                      versioning)

    def test_rebuilds_automerge_and_releases_do_not(self) -> None:
        major_or_minor_automerge = [r for r in rules_for(PACKAGE)
                                    if r.get("automerge")
                                    and set(r.get("matchUpdateTypes") or []) & {"major", "minor"}]
        self.assertEqual(major_or_minor_automerge, [], "a new DMS release must stay an MR")
        patch = [r for r in rules_for(PACKAGE) if r.get("automerge")
                 and {"patch", "digest"} <= set(r.get("matchUpdateTypes") or [])]
        self.assertTrue(patch, "a rebuild of the same release must automerge")


if __name__ == "__main__":
    unittest.main()
