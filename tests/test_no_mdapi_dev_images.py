"""Nothing deploys from zot's scratch area `mdapi-dev/**` (infra-8f4.54 stage 5).

Branch, MR and tag-pipeline builds publish to zot.mdapi.ch/mdapi-dev/<project>:<sha>
with a 14-day retention (zot/zot-s3-config-es.yml). A manifest pinned there would
ImagePullBackOff once retention removes the tag, and it would run an unreviewed build:
the dev-push credential is readable by every branch pipeline. Renovate must not
follow it either, so renovate.json carries a rule that disables it.

The only files allowed to name the prefix are the ones that DEFINE the area.
"""
from __future__ import annotations

import json
import unittest

import fleetlib

# An IMAGE reference always carries the host, so prose that merely names the area
# (comments explaining where CI publishes) does not trip the guard.
PREFIX = "zot.mdapi.ch/mdapi-dev/"
ALLOWED = {"zot/zot-s3-config-es.yml", "renovate.json", "tests/test_no_mdapi_dev_images.py"}
DEV_GLOB = "zot.mdapi.ch/mdapi-dev/**"


def offenders(files: dict[str, str]) -> list[str]:
    """Paths (other than the defining files) whose text names the mdapi-dev area."""
    return sorted(p for p, text in files.items() if PREFIX in text and p not in ALLOWED)


def _text(rel: str) -> str:
    try:
        return (fleetlib.repo_root() / rel).read_text(errors="replace")
    except (IsADirectoryError, FileNotFoundError):
        return ""


class NothingDeploysFromMdapiDev(unittest.TestCase):
    def test_offenders_flags_a_manifest_pinned_to_the_dev_area(self):
        files = {"app/deploy.yml": "image: zot.mdapi.ch/mdapi-dev/app:abc123",
                 "other/deploy.yml": "image: zot.mdapi.ch/mdapi/app:1.0-mdapi.4",
                 "doc/note.yml": "# branch builds land in mdapi-dev/<project>",
                 "zot/zot-s3-config-es.yml": '"mdapi-dev/**"'}
        self.assertEqual(offenders(files), ["app/deploy.yml"])

    def test_no_tracked_file_references_the_dev_area(self):
        files = {f: _text(f) for f in fleetlib.tracked_files()}
        self.assertEqual(offenders(files), [])

    def test_renovate_disables_the_dev_area_and_nothing_below_re_enables_it(self):
        rules = json.loads(_text("renovate.json"))["packageRules"]
        idx = [i for i, r in enumerate(rules) if DEV_GLOB in r.get("matchPackageNames", [])]
        self.assertTrue(idx, "renovate.json has no rule for " + DEV_GLOB)
        self.assertIs(rules[idx[-1]].get("enabled"), False)
        # last-match-wins per field: a later enabled=true would switch it back on
        later = [r.get("description", "")[:60] for r in rules[idx[-1] + 1:] if r.get("enabled") is True]
        self.assertEqual(later, [])

if __name__ == "__main__":
    unittest.main()
