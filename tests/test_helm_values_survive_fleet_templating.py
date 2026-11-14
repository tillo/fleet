"""A Helm values file may not contain "${" unless its bundle disables Fleet's templating.

Fleet pre-processes every bundle's helm values as a Go template with ${ } delimiters
(for ${ .ClusterValues ... }), unless the bundle sets helm.disablePreProcess. Anything
else that happens to be spelled ${...} is evaluated, and both outcomes are silent until
deploy time. Both happened on 2026-11-07 (infra-8f4.50):

- monitoring-blackbox: a blackbox regexp label `value: "${1}"` reached the exporter as
  the literal 1, so every clamd probe series carried daily="2" and the signature-age
  alert could never see a new daily build. Green pipeline, wrong data.
- mail/docker-mailserver: rspamd's antivirus message '${SCANNER} FOUND VIRUS' made the
  bundle fail to target ("function SCANNER not defined") after a green merge, so the
  change never deployed and nothing outside Fleet said so.

Comments do not count (Fleet templates the parsed values); "$1" / "$VAR" are fine.
"""

import unittest
from pathlib import Path

import yaml

import fleetlib


def _strings(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)
    elif isinstance(node, str):
        yield node


class HelmValuesSurviveFleetTemplatingTest(unittest.TestCase):
    def test_no_template_delimiters_unless_preprocess_disabled(self):
        root = fleetlib.repo_root()
        checked = 0
        offenders = []
        for bundle_dir, marker in fleetlib.bundles():
            helm = fleetlib.bundle_spec(marker).get("helm") or {}
            if not helm or helm.get("disablePreProcess"):
                continue
            sources = [("inline helm.values", helm.get("values") or {})]
            for vf in helm.get("valuesFiles") or []:
                p = root / bundle_dir / vf
                if p.exists():
                    sources.append((str(Path(bundle_dir) / vf), yaml.safe_load(p.read_text()) or {}))
            for name, values in sources:
                checked += 1
                for s in _strings(values):
                    if "${" in s:
                        line = next(l for l in s.splitlines() if "${" in l).strip()
                        offenders.append(f"{marker} ({name}): {line[:100]}")

        # ⛔ Empty selection is not a pass.
        self.assertGreater(checked, 20, "found almost no helm values -- discovery is broken")
        self.assertEqual(
            [], offenders,
            "\nFleet will evaluate these as templates (set helm.disablePreProcess: true, "
            "or spell the placeholder without ${ }):\n" + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
