"""The recovery images that come from the INTERNET are cached on every node too.

WHY (2026-11-21, infra-8f4): the outage that sent the jump seat into ImagePullBackOff
(zot is Ceph RGW-backed, see test_seat_image_is_prepulled_on_every_node.py) also showed
the next ring of the recovery set cached on only some nodes: cert-manager, MetalLB,
external-secrets, the CNPG operator + barman plugin, and the seat's web door
(guacamole, guacd, guac-pg). They come from ghcr.io / quay.io / docker.io, so a node
that never ran them cannot start them while the internet edge is down.

THE SHAPE, and why each assertion exists
  * A SECOND DaemonSet (recovery-image-prepull), never the seat's: a pull failure of
    any of these images must not be able to hold back the seat image's pod.
  * Each image is a MAIN container: main containers start (and pull) in parallel, so
    one failing pull cannot block the others the way sequential init containers do.
    A running container is also what keeps kubelet image GC off the image.
  * Most of these images are distroless (no sleep, no shell), so one init container
    stages a STATIC busybox into an emptyDir and every holder runs it from there.
  * The list is DERIVED FROM THE LIVE CLUSTER by tools/recovery-prepull-sync.py, not
    from the charts: the chart workloads pull by TAG, so the digest a node runs is what
    it pulled; CNPG chooses its bootstrap and sidecar images at runtime, which no
    render shows. Renovate is therefore OFF for that one file (it would bump lines
    ahead of the charts and create the drift it is meant to prevent); the updater is
    alert RecoveryImageNotPrepulled (monitoring-rules/88) + that tool.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

import fleetlib
from test_seat_image_is_prepulled_on_every_node import (
    PREPULL as SEAT_PREPULL, containers, find, is_pinned, pod_spec, tolerates_everything)

RECOVERY = ("DaemonSet", "recovery-image-prepull")
RECOVERY_FILE = "jump/seat-prepull/recovery-image-prepull.yml"
GUACAMOLE_FILE = "jump/guacamole.yml"

_spec = importlib.util.spec_from_file_location(
    "recovery_prepull_sync", fleetlib.repo_root() / "tools" / "recovery-prepull-sync.py")
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)


def pod(ns: str, name: str, statuses: list[tuple[str, str, str]], phase: str = "Running",
        init: list[tuple[str, str, str]] = ()) -> dict:
    """A pod as `kubectl get pods -o json` returns it: (container, spec image, imageID)."""
    def part(rows):
        return ([{"name": n, "image": i} for n, i, _ in rows],
                [{"name": n, "image": i, "imageID": d} for n, i, d in rows])
    spec_c, st_c = part(statuses)
    spec_i, st_i = part(init)
    return {"metadata": {"namespace": ns, "name": name},
            "spec": {"containers": spec_c, "initContainers": spec_i},
            "status": {"phase": phase, "containerStatuses": st_c, "initContainerStatuses": st_i}}


D1, D2 = "sha256:" + "1" * 64, "sha256:" + "2" * 64


class DeriveTest(unittest.TestCase):
    """tools/recovery-prepull-sync.py: live pods -> the pinned references to hold."""

    def test_tag_from_spec_digest_from_status(self):
        pods = [pod("cert-manager", "cert-manager-abc", [("c", "quay.io/j/ctl:v1", "quay.io/j/ctl@" + D1)])]
        self.assertEqual(sync.derive(pods), ["quay.io/j/ctl:v1@" + D1])

    def test_init_containers_count_and_duplicates_collapse(self):
        pods = [pod("jump", "guac-pg-1", [("postgres", "ghcr.io/c/pg:16.4", "ghcr.io/c/pg@" + D1)],
                    init=[("boot", "ghcr.io/c/op:1.30.2", "ghcr.io/c/op@" + D2)]),
                pod("cnpg-system", "cnpg-x", [("manager", "ghcr.io/c/op:1.30.2", "ghcr.io/c/op@" + D2)])]
        self.assertEqual(sync.derive(pods), ["ghcr.io/c/op:1.30.2@" + D2, "ghcr.io/c/pg:16.4@" + D1])

    def test_a_spec_that_already_pins_keeps_its_own_spelling(self):
        ref = "guacamole/guacd:1.6.0@" + D1
        pods = [pod("jump", "guacd-1", [("guacd", ref, "docker.io/guacamole/guacd@" + D1)])]
        self.assertEqual(sync.derive(pods), [ref])

    def test_only_the_recovery_pods_of_jump_count(self):
        pods = [pod("jump", "openvpn-1", [("o", "alpine:3", "docker.io/library/alpine@" + D1)]),
                pod("jump", "jump-bastion-1", [("b", "zot.mdapi.ch/x:1", "zot.mdapi.ch/x@" + D1)]),
                pod("jump", "recovery-image-prepull-1", [("h", "a/b:1", "a/b@" + D1)]),
                pod("jump", "guacamole-1", [("g", "g/g:1.6.0", "g/g@" + D2)])]
        self.assertEqual(sync.derive(pods), ["g/g:1.6.0@" + D2])

    def test_pods_that_are_not_running_or_not_pulled_yet_do_not_count(self):
        pods = [pod("cert-manager", "startupapicheck-1", [("c", "a/b:1", "a/b@" + D1)], phase="Succeeded"),
                pod("cert-manager", "cert-manager-1", [("c", "a/c:1", "")])]
        self.assertEqual(sync.derive(pods), [])

    def test_two_digests_of_one_tag_are_both_held(self):
        pods = [pod("metallb-system", "speaker-1", [("s", "q/sp:v1", "q/sp@" + D1)]),
                pod("metallb-system", "speaker-2", [("s", "q/sp:v1", "q/sp@" + D2)])]
        self.assertEqual(sync.derive(pods), ["q/sp:v1@" + D1, "q/sp:v1@" + D2])

    def test_untagged_spec_is_refused(self):
        pods = [pod("cert-manager", "x-1", [("c", "a/b", "a/b@" + D1)])]
        with self.assertRaises(ValueError):
            sync.derive(pods)

    def test_container_names_are_unique_dns_labels(self):
        refs = ["q/metallb/speaker:v1@" + D1, "q/metallb/speaker:v1@" + D2,
                "ghcr.io/cloudnative-pg/plugin-barman-cloud-sidecar-with-a-very-long-name-indeed:v0.16.0@" + D1]
        names = sync.container_names(refs)
        self.assertEqual(len(set(names)), 3)
        for n in names:
            self.assertRegex(n, r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
        self.assertEqual(names[0], "speaker")

    def test_rewrite_replaces_only_the_marked_block(self):
        text = "head\n      # BEGIN derived\n      STALE-LINE\n      # END derived\ntail\n"
        out = sync.rewrite(text, ["a/b:1@" + D1])
        self.assertEqual(out, "head\n      # BEGIN derived\n" + sync.block(["a/b:1@" + D1])
                         + "      # END derived\ntail\n")
        self.assertEqual(sync.rewrite(out, ["a/b:1@" + D1]), out, "rewrite must be idempotent")

    def test_markers_on_the_first_and_last_line_work(self):
        out = sync.rewrite("# BEGIN derived\n# END derived", ["a/b:1@" + D1])
        self.assertEqual(out, "# BEGIN derived\n" + sync.block(["a/b:1@" + D1]) + "# END derived")

    def test_holder_block_shape(self):
        self.assertEqual(sync.block(["a/b:1@" + D1]).splitlines()[:3], [
            "      - name: b", "        image: a/b:1@" + D1, "        imagePullPolicy: IfNotPresent"])

    def test_rewrite_refuses_missing_duplicate_or_reversed_markers(self):
        for text in ("no markers\n", "# END derived\n", "# BEGIN derived\n",
                     "# END derived\n# BEGIN derived\n",
                     "# BEGIN derived\n# BEGIN derived\n# END derived\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                sync.rewrite(text, ["a/b:1@" + D1])

    def test_a_registry_port_is_not_a_tag_and_a_bare_name_needs_one_too(self):
        for image in ("reg.example:5000/a/b", "alpine"):
            pods = [pod("cert-manager", "x-1", [("c", image, image + "@" + D1)])]
            with self.subTest(image=image), self.assertRaises(ValueError):
                sync.derive(pods)

    def test_repeated_names_count_up(self):
        refs = ["q/sp:v1@" + D1, "q/sp:v1@" + D2, "r/sp:v2@" + D1]
        self.assertEqual(sync.container_names(refs), ["sp", "sp-2", "sp-3"])
        self.assertEqual(sync.container_names(["x/Some_Thing.v2:1@" + D1, "busybox:1@" + D1]),
                         ["some-thing-v2", "busybox"])


class MainTest(unittest.TestCase):
    """The tool end to end, from a saved pod list into a scratch copy of the file."""

    PODS = [pod("cert-manager", "cm-1", [("c", "q/ctl:v1", "q/ctl@" + D1)])]
    SKEL = "x\n      # BEGIN derived\n      # END derived\n"

    def run_main(self, text, pods, *args):
        with tempfile.TemporaryDirectory() as d:
            target, saved = Path(d) / "ds.yml", Path(d) / "pods.json"
            target.write_text(text)
            saved.write_text(json.dumps({"items": pods}))
            old, sync.SETS = sync.SETS, {"recovery": sync.PrepullSet(target, sync.RECOVERY)}
            try:
                with contextlib.redirect_stdout(io.StringIO()) as out, \
                        contextlib.redirect_stderr(io.StringIO()):
                    rc = sync.main(["--pods", str(saved), *args])
            finally:
                sync.SETS = old
            return rc, target.read_text(), out.getvalue()

    def test_write_then_check_in_sync(self):
        rc, text, out = self.run_main(self.SKEL, self.PODS)
        self.assertEqual(rc, 0)
        self.assertIn("image: q/ctl:v1@" + D1, text)
        self.assertIn("1 images", out)
        rc, again, out = self.run_main(text, self.PODS, "--check")
        self.assertEqual((rc, again), (0, text))
        self.assertIn("in sync", out)

    def test_check_reports_drift_without_writing(self):
        rc, text, out = self.run_main(self.SKEL, self.PODS, "--check")
        self.assertEqual((rc, text), (1, self.SKEL))
        self.assertIn("DRIFT", out)

    def test_every_set_is_checked_and_the_worst_result_wins(self):
        zot_pod = pod("monitoring", "vmsingle-mdapi-1-x", [
            ("v", "zot.mdapi.ch/v/vm:v1", "zot.mdapi.ch/v/vm@" + D2)])
        with tempfile.TemporaryDirectory() as d:
            a, b, saved = Path(d) / "a.yml", Path(d) / "b.yml", Path(d) / "pods.json"
            a.write_text(sync.rewrite(self.SKEL, ["q/ctl:v1@" + D1]))
            b.write_text(self.SKEL)
            saved.write_text(json.dumps({"items": self.PODS + [zot_pod]}))
            old, sync.SETS = sync.SETS, {"recovery": sync.PrepullSet(a, sync.RECOVERY),
                                         "zot-critical-live": sync.PrepullSet(b, sync.ZOT_CRITICAL_LIVE)}
            try:
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(sync.main(["--pods", str(saved), "--check"]), 1)
                    self.assertEqual(sync.main(["--pods", str(saved), "--check", "--set", "recovery"]), 0)
                    self.assertEqual(sync.main(["--pods", str(saved), "--set", "zot-critical-live"]), 0)
                    self.assertEqual(sync.main(["--pods", str(saved), "--check"]), 0)
            finally:
                sync.SETS = old
            self.assertIn("zot-critical-live: DRIFT", out.getvalue())
            self.assertIn("image: zot.mdapi.ch/v/vm:v1@" + D2, b.read_text())

    def test_refuses_to_write_an_empty_list(self):
        rc, text, _ = self.run_main(self.SKEL, [])
        self.assertEqual((rc, text), (2, self.SKEL))


class RecoveryPrepullManifestTest(unittest.TestCase):
    def setUp(self):
        found = find(RECOVERY)
        self.assertEqual(len(found), 1, f"exactly one {RECOVERY} expected")
        self.rel, self.ds = found[0]
        self.assertEqual(self.rel, RECOVERY_FILE)
        self.spec = pod_spec(self.ds)
        self.holders = containers(self.ds, init=False)
        self.assertGreaterEqual(len(self.holders), 10, "the recovery set is not that small")

    def test_every_image_is_pinned_and_pulled_if_not_present(self):
        for c in containers(self.ds):
            with self.subTest(container=c.get("name")):
                self.assertTrue(is_pinned(str(c.get("image", ""))), c.get("image"))
                self.assertEqual(c.get("imagePullPolicy"), "IfNotPresent")

    def test_holders_run_the_staged_static_sleep_from_the_shared_volume(self):
        stage = self.spec.get("initContainers") or []
        self.assertEqual(len(stage), 1, "one init container: the busybox stager")
        self.assertIn("busybox", stage[0]["image"])
        self.assertIn("-musl@", stage[0]["image"], "the glibc busybox build is NOT static")
        for c in self.holders:
            with self.subTest(container=c.get("name")):
                self.assertEqual(c.get("command", [None])[0], sync.HOLD_BIN)

    def test_file_block_is_what_the_tool_writes(self):
        refs = [c["image"] for c in self.holders]
        text = (fleetlib.repo_root() / RECOVERY_FILE).read_text(encoding="utf-8")
        self.assertEqual(sync.rewrite(text, refs), text, "hand-edited block: run the tool")

    def test_separate_from_the_seat_daemonset(self):
        seat = {c["image"] for c in containers(find(SEAT_PREPULL)[0][1])}
        mine = {c["image"] for c in containers(self.ds)}
        self.assertEqual(seat & mine, set())

    def test_every_node_and_old_images_kept_until_new_ones_pulled(self):
        self.assertTrue(tolerates_everything(self.spec))
        rolling = (self.ds["spec"].get("updateStrategy") or {}).get("rollingUpdate") or {}
        self.assertEqual((rolling.get("maxUnavailable"), rolling.get("maxSurge")), (0, 1))

    def test_guacamole_pair_matches_the_web_door_exactly(self):
        wanted = {c["image"] for _, d in [(r, d) for r in [GUACAMOLE_FILE] for d in fleetlib.load_docs(r)]
                  if d.get("kind") == "Deployment" for c in containers(d)
                  if str(c.get("image", "")).startswith("guacamole/")}
        self.assertEqual(len(wanted), 2, "guacamole + guacd expected in jump/guacamole.yml")
        self.assertEqual(wanted - {c["image"] for c in self.holders}, set())

    def test_renovate_leaves_the_derived_file_alone(self):
        cfg = json.loads((fleetlib.repo_root() / "renovate.json").read_text(encoding="utf-8"))
        off = [r for r in cfg["packageRules"]
               if r.get("enabled") is False and RECOVERY_FILE in (r.get("matchFileNames") or [])]
        self.assertEqual(len(off), 1, "renovate.json needs one rule disabling this file")


if __name__ == "__main__":
    unittest.main()
