#!/usr/bin/env python3
"""A pinned image digest must resolve to a linux/amd64 image before it can merge (infra-r9mzd.23).

WHY THIS FILE EXISTS (2026-11-04)
fleet 80788d98 (Renovate "Update caddy Docker tag to v2.11.8", automerged 19:09Z) pinned
library/caddy:2.11.8-alpine@sha256:eb37f586...; Docker Hub served that digest as an OCI
index with ZERO manifests (a half-published official-image push, still empty on 11-05).
containerd: "no match for platform in manifest: not found", and k8s-mcp had 0 ready
replicas 20:01-23:20Z until a later digest refresh replaced it. Every gate was green: the
manifest is valid YAML, the ref is well-formed, and nothing asked the registry. A soak would
not have helped either: a broken index stays broken under its digest forever.

tools/ci/image-platforms.py now resolves every `image: ...@sha256:` line ADDED by the
commit range against its registry and fails unless it is (or indexes) a linux/amd64 image.
These tests pin its decisions offline; the registry calls run in fleet-validate.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).absolute().parent.parent
_spec = importlib.util.spec_from_file_location("image_platforms", ROOT / "tools" / "ci" / "image-platforms.py")
ip = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ip)

BAD = "eb37f58646a901dc7727cf448cae36daaefaba79de33b5058dab79aa4c04aefb"
GOOD = "e70b164da81ef698b3ad19af799ea6086ce6d9e48ed4079eb9fe26f6fbf14ee5"

# The incident diff, verbatim (git show 80788d98).
INCIDENT_DIFF = f"""\
diff --git a/k8s-mcp/k8s-mcp-deploy.yml b/k8s-mcp/k8s-mcp-deploy.yml
--- a/k8s-mcp/k8s-mcp-deploy.yml
+++ b/k8s-mcp/k8s-mcp-deploy.yml
@@ -93 +93 @@ spec:
-        image: library/caddy:2.11.7-alpine@sha256:{GOOD}
+        image: library/caddy:2.11.8-alpine@sha256:{BAD}
"""

OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"


def index(*platforms):
    return {"mediaType": OCI_INDEX, "manifests": [
        {"digest": f"sha256:{n:064x}", "platform": {"os": o, "architecture": a}}
        for n, (o, a) in enumerate(platforms)]}


def single(config_digest="sha256:" + "c" * 64):
    return {"mediaType": OCI_MANIFEST, "config": {"digest": config_digest}}


class AddedRefs(unittest.TestCase):
    def test_the_incident_diff_yields_only_the_new_digest(self):
        self.assertEqual(ip.added_refs(INCIDENT_DIFF), [f"library/caddy:2.11.8-alpine@sha256:{BAD}"])

    def test_quotes_list_items_and_duplicates(self):
        diff = (f'+  - image: "ghcr.io/a/b:1@sha256:{GOOD}"\n'
                f"+    image: 'ghcr.io/a/b:1@sha256:{GOOD}'\n"
                f"+image: alpine:3@sha256:{BAD}\n")
        self.assertEqual(ip.added_refs(diff), [f"alpine:3@sha256:{BAD}", f"ghcr.io/a/b:1@sha256:{GOOD}"])

    def test_ignores_file_headers_removals_and_non_image_keys(self):
        diff = (f"+++ b/image: x@sha256:{BAD}\n-        image: x@sha256:{BAD}\n"
                f"+    tag: v1@sha256:{BAD}\n+  # image: x@sha256:{BAD}\n"
                f"+    image: ${{CI_DEPENDENCY_PROXY_GROUP_IMAGE_PREFIX}}/x:1@sha256:{BAD}\n")
        self.assertEqual(ip.added_refs(diff), [])


class SplitRef(unittest.TestCase):
    def test_docker_hub_short_names(self):
        self.assertEqual(ip.split_ref(f"library/caddy:2.11.8-alpine@sha256:{BAD}"),
                         ("registry-1.docker.io", "library/caddy", f"sha256:{BAD}"))
        self.assertEqual(ip.split_ref(f"alpine:3@sha256:{BAD}"),
                         ("registry-1.docker.io", "library/alpine", f"sha256:{BAD}"))
        self.assertEqual(ip.split_ref(f"docker.io/rclone/rclone@sha256:{BAD}"),
                         ("registry-1.docker.io", "rclone/rclone", f"sha256:{BAD}"))

    def test_other_registries_keep_host_and_drop_the_tag(self):
        self.assertEqual(ip.split_ref(f"ghcr.io/formbricks/formbricks:6.1.3@sha256:{BAD}"),
                         ("ghcr.io", "formbricks/formbricks", f"sha256:{BAD}"))
        self.assertEqual(ip.split_ref(f"localhost:5000/x@sha256:{BAD}"),
                         ("localhost:5000", "x", f"sha256:{BAD}"))


class Verdict(unittest.TestCase):
    def test_the_incident_index_with_zero_manifests_is_refused(self):
        self.assertIn("no linux/amd64", ip.verdict({"mediaType": OCI_INDEX, "manifests": []}, None))

    def test_an_index_with_linux_amd64_passes_and_attestations_do_not_count(self):
        self.assertIsNone(ip.verdict(index(("linux", "arm64"), ("linux", "amd64")), None))
        self.assertIn("linux/arm64, unknown/unknown",
                      ip.verdict(index(("linux", "arm64"), ("unknown", "unknown")), None))

    def test_a_single_manifest_is_judged_by_its_config(self):
        self.assertIsNone(ip.verdict(single(), {"os": "linux", "architecture": "amd64"}))
        self.assertIn("linux/arm64", ip.verdict(single(), {"os": "linux", "architecture": "arm64"}))

    def test_an_index_without_a_media_type_is_still_an_index(self):
        # mediaType is OPTIONAL in an OCI index; its manifests list is what makes it one.
        self.assertIsNone(ip.verdict({"manifests": index(("linux", "amd64"))["manifests"]}, None))

    def test_docker_v2_list_media_type_counts_as_an_index(self):
        lst = index(("linux", "amd64"))
        lst["mediaType"] = "application/vnd.docker.distribution.manifest.list.v2+json"
        self.assertIsNone(ip.verdict(lst, None))


class Bearer(unittest.TestCase):
    def test_parses_the_challenge(self):
        self.assertEqual(
            ip.parse_bearer('Bearer realm="https://ghcr.io/token",service="ghcr.io",'
                            'scope="repository:a/b:pull"'),
            ("https://ghcr.io/token", {"service": "ghcr.io", "scope": "repository:a/b:pull"}))

    def test_basic_or_missing_challenge_is_none(self):
        self.assertIsNone(ip.parse_bearer('Basic realm="Authorization Required"'))
        self.assertIsNone(ip.parse_bearer(""))


class Check(unittest.TestCase):
    """The orchestration, with the registry replaced by a dict."""

    def run_check(self, refs, manifests, configs=None):
        def fetch_manifest(reg, repo, digest):
            got = manifests[(reg, repo, digest)]
            if isinstance(got, Exception):
                raise got
            return got

        def fetch_config(reg, repo, digest):
            return (configs or {})[digest]

        return ip.check(refs, fetch_manifest, fetch_config)

    def test_the_incident_fails_and_its_fix_passes(self):
        reg, repo = "registry-1.docker.io", "library/caddy"
        manifests = {(reg, repo, f"sha256:{BAD}"): {"mediaType": OCI_INDEX, "manifests": []},
                     (reg, repo, f"sha256:{GOOD}"): index(("linux", "amd64"))}
        bad = self.run_check([f"library/caddy:2.11.8-alpine@sha256:{BAD}"], manifests)
        self.assertEqual(len(bad.failures), 1)
        self.assertIn("caddy", bad.failures[0])
        good = self.run_check([f"library/caddy:2.11.7-alpine@sha256:{GOOD}"], manifests)
        self.assertEqual((good.failures, good.passed), ([], 1))

    def test_in_house_registries_are_skipped_not_failed(self):
        out = self.run_check([f"registry.mdapi.ch/mdapi/x@sha256:{BAD}",
                              f"zot.mdapi.ch/mdapi/y:latest@sha256:{BAD}"], {})
        self.assertEqual((out.failures, out.passed, len(out.skipped)), ([], 0, 2))

    def test_an_unreachable_public_registry_fails_closed(self):
        ref = f"ghcr.io/a/b@sha256:{BAD}"
        out = self.run_check([ref], {("ghcr.io", "a/b", f"sha256:{BAD}"): OSError("timed out")})
        self.assertEqual(len(out.failures), 1)
        self.assertIn("timed out", out.failures[0])

    def test_a_single_manifest_without_a_config_fails_unverified(self):
        out = self.run_check([f"ghcr.io/a/b@sha256:{GOOD}"],
                             {("ghcr.io", "a/b", f"sha256:{GOOD}"): {"mediaType": OCI_MANIFEST}})
        self.assertEqual(len(out.failures), 1)
        self.assertIn("could not be verified", out.failures[0])

    def test_a_single_manifest_fetches_its_config(self):
        cfg = "sha256:" + "c" * 64
        out = self.run_check([f"ghcr.io/a/b@sha256:{GOOD}"],
                             {("ghcr.io", "a/b", f"sha256:{GOOD}"): single(cfg)},
                             {cfg: {"os": "linux", "architecture": "amd64"}})
        self.assertEqual((out.failures, out.passed), ([], 1))


class ReportAndBase(unittest.TestCase):
    def test_report_fails_the_job_only_on_failures(self):
        r = ip.Result()
        r.passed, r.skipped = 2, ["zot.mdapi.ch/x@sha256:1: in-house"]
        lines, rc = ip.report(r, n_refs=3, base="0123456789abcdef")
        self.assertEqual(rc, 0)
        self.assertEqual(lines[0], "skip  zot.mdapi.ch/x@sha256:1: in-house")
        self.assertEqual(lines[-1], "image-platforms: 3 added digest(s) since 0123456789ab: "
                                    "2 ok, 1 skipped, 0 failed")
        r.failures = ["library/caddy@sha256:eb37: no linux/amd64 in its index (has: nothing)"]
        lines, rc = ip.report(r, n_refs=3, base="0123456789abcdef")
        self.assertEqual(rc, 1)
        self.assertIn("FAIL  library/caddy@sha256:eb37", lines[1])

    def test_diff_base_prefers_the_mr_base_then_the_pushed_before(self):
        self.assertEqual(ip.diff_base({"CI_MERGE_REQUEST_DIFF_BASE_SHA": "aaa",
                                       "CI_COMMIT_BEFORE_SHA": "bbb"}), "aaa")
        self.assertEqual(ip.diff_base({"CI_COMMIT_BEFORE_SHA": "bbb"}), "bbb")
        # A new branch's first push reports forty zeros: nothing to diff against.
        self.assertEqual(ip.diff_base({"CI_COMMIT_BEFORE_SHA": "0" * 40}), "HEAD~1")
        self.assertEqual(ip.diff_base({}), "HEAD~1")


if __name__ == "__main__":
    unittest.main()
