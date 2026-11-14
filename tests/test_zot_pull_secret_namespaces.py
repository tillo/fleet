"""Every namespace that pulls from zot.mdapi.ch gets its `zot-pull` Secret from this bundle.

infra-kaz9d (O2 `lib_image_pull_secret_missing_events`, 2026-11-10 -> 11-13): the Windmill
probe `f/probe/wan_speedtest` creates a bare Pod in `default` with
`imagePullSecrets: [{name: zot-pull}]`. No manifest in this repo deploys that Pod, so no
bundle carried the Secret there. When the registry.mdapi.ch secrets were retired (infra-8f4.54)
every 6-hourly run failed to pull (FailedToRetrieveImagePullSecret + pull access denied). It
was stopped by a HAND-COPIED Secret, which goes stale at the next password rotation.

Pods created outside fleet (Windmill probes, Jobs spawned by scripts) are invisible to any
scan of this repo, so their namespaces are named here, each with its creator.

The second check keeps the copied blocks honest: every block must be the same template, only
`metadata.namespace` may differ ("NEW NAMESPACE? Copy one block, change metadata.namespace only").
"""

from __future__ import annotations

import copy
import pathlib
import unittest

import yaml

ES_FILE = pathlib.Path(__file__).resolve().parent.parent / "zot-pull-secrets" / "zot-pull-es.yml"

# namespace -> what creates zot-pulling Pods there without a fleet manifest
OUT_OF_FLEET_CONSUMERS = {
    "default": "Windmill f/probe/wan_speedtest (ephemeral hostNetwork Ookla pod)",
}


def _docs() -> list[dict]:
    return [d for d in yaml.safe_load_all(ES_FILE.read_text()) if d]


def _without_namespace(doc: dict) -> dict:
    d = copy.deepcopy(doc)
    d["metadata"].pop("namespace", None)
    return d


class ZotPullSecretNamespaces(unittest.TestCase):
    def test_out_of_fleet_consumers_have_an_external_secret(self) -> None:
        namespaces = {d["metadata"]["namespace"] for d in _docs()}
        for ns, creator in OUT_OF_FLEET_CONSUMERS.items():
            with self.subTest(namespace=ns):
                self.assertIn(ns, namespaces, f"{creator} pulls with zot-pull in ns {ns}")

    def test_every_block_is_the_same_template(self) -> None:
        docs = _docs()
        reference = _without_namespace(docs[0])
        for doc in docs[1:]:
            with self.subTest(namespace=doc["metadata"].get("namespace")):
                self.assertEqual(reference, _without_namespace(doc))

    def test_namespaces_are_unique(self) -> None:
        namespaces = [d["metadata"]["namespace"] for d in _docs()]
        self.assertEqual(len(namespaces), len(set(namespaces)))


if __name__ == "__main__":
    unittest.main()
