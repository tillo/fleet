"""A values string Harvester rewrites must already be spelled the way Harvester writes it.

After the 2026-10-24 04:30Z certificate renewal, BundleDeployment
mdapi-prod-rke2-ingress-nginx-config sat Modified with nothing actually wrong. The only
diff was `spec.valuesContent` of HelmChartConfig kube-system/rke2-ingress-nginx, and the
live object's managedFields named the writer: manager `harvester`.

Harvester v1.8.2's setting controller (pkg/controller/master/setting/ssl_certificate.go,
updateIngressDefaultCertificate) runs on every change of the `ssl-certificates` Setting.
It yaml.v3-Unmarshals valuesContent into a map, PutValue's
controller.extraArgs.default-ssl-certificate, and writes yaml.Marshal's output back
UNCONDITIONALLY: 4-space indent, sorted keys, no comments, the emitter's own quoting.
Fleet compares the string byte for byte. Git held a commented, 2-space, hand-ordered
spelling of the SAME values, so every renewal turned the bundle Modified, and the next
deploy wrote git's spelling back for the following renewal to undo. ssl_parameters.go
does the same round-trip, for controller.config.ssl-ciphers / ssl-protocols.

The fix is to hold Harvester's spelling in git: its rewrite becomes a byte-for-byte
no-op. Hiding valuesContent with a comparePatch was rejected: it would hide REAL drift
in every value of the bundle. This test keeps the spelling canonical. It applies the
rewrite Harvester performs (tests/yamlv3.py, a byte-exact model of yaml.v3 v3.0.1,
checked against the real library) and asserts the result equals what git holds.

⛔ yamlv3 FAILS CLOSED: an input it does not model raises Unsupported and the test
errors. Never catch that; reshape the value or extend the model.
⚠️ On a Harvester minor upgrade, re-check which HelmChartConfigs it rewrites
(grep pkg/ for `helmChartConfigs.Update` and `ValuesContent`) and update REWRITTEN.
"""

import difflib
import textwrap
import unittest

import fleetlib
import yamlv3

# (namespace, name) of a HelmChartConfig -> the writes Harvester's syncers make to it
REWRITTEN = {
    ("kube-system", "rke2-ingress-nginx"): [
        # ssl_certificate.go: a custom certificate is installed as cattle-system/tls-ingress
        (("controller", "extraArgs", "default-ssl-certificate"), "cattle-system/tls-ingress"),
    ],
}


def put(values: dict, path: tuple[str, ...], value) -> None:
    """Harvester's data.PutValue: create intermediate maps, set the leaf."""
    for k in path[:-1]:
        if not isinstance(values.get(k), dict):
            values[k] = {}
        values = values[k]
    values[path[-1]] = value


def harvester_rewrite(text: str, writes) -> str:
    values = yamlv3.load(text)
    for path, value in writes:
        put(values, path, value)
    return yamlv3.dump(values)


class HarvesterRewrittenValuesTest(unittest.TestCase):
    def test_values_are_spelled_as_harvester_writes_them(self):
        found = {}
        for rel in fleetlib.tracked_yaml():
            for doc in fleetlib.load_docs(rel):
                if doc.get("kind") != "HelmChartConfig":
                    continue
                meta = doc.get("metadata") or {}
                key = (meta.get("namespace"), meta.get("name"))
                if key in REWRITTEN:
                    found.setdefault(key, []).append((rel, doc))

        # ⛔ Empty selection is not a pass — and two copies would fight each other.
        for key in REWRITTEN:
            self.assertEqual(
                1, len(found.get(key, [])),
                f"HelmChartConfig {key[0]}/{key[1]} must be in git exactly once, found "
                f"{[rel for rel, _ in found.get(key, [])]}",
            )

        violations = []
        for key, [(rel, doc)] in found.items():
            vc = (doc.get("spec") or {}).get("valuesContent")
            self.assertIsInstance(vc, str, f"{rel}: spec.valuesContent is not a string")
            canonical = harvester_rewrite(vc, REWRITTEN[key])
            if vc != canonical:
                diff = "".join(difflib.unified_diff(
                    vc.splitlines(True), canonical.splitlines(True), "git", "harvester"))
                violations.append(
                    f"{rel}: spec.valuesContent is not Harvester's spelling, so Fleet "
                    f"will show the bundle Modified after the next certificate renewal.\n"
                    f"{diff}\nPaste this as the block (keep notes in the header):\n"
                    f"  valuesContent: |\n{textwrap.indent(canonical, '    ')}"
                )
        if violations:  # self.fail: assertEqual would dump the whole text twice
            self.fail("\n" + "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
