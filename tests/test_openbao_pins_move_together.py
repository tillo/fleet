"""openbao: every pin of the image moves in ONE Renovate MR, and CI fails if they differ.

Incident (infra-fsjv, 2026-09-13 coverage sweep): openbao/fleet.yaml pins the same image
twice, as a Helm object (server.image.{registry,repository,tag}) for the server and as a
bare string on the unsealer sidecar, and openbao-bootstrap's CronJob pins it a third
time. No manager read the two fleet.yaml pins at all; a customManager on only one of
them would run a secrets store and its own unsealer on different builds. Decision
2026-11-06: one Renovate group for every pin, and this test.
"""
import json
import re
import unittest

import yaml

from tests import fleetlib

REPO = "zot.mdapi.ch/openbao/openbao"
FLEET = "openbao/fleet.yaml"


def js_to_py(rx: str) -> str:
    return re.sub(r"\(\?<([A-Za-z]\w*)>", r"(?P<\1>", rx)


def renovate():
    return json.loads((fleetlib.repo_root() / "renovate.json").read_text())


def string_pins():
    """(file, tag) for every `image: zot.mdapi.ch/openbao/openbao:<tag>` in the repo."""
    rx = re.compile(r"image:\s*[\"']?" + re.escape(REPO) + r":([^\s@\"']+)")
    root = fleetlib.repo_root()
    return [(str(p.relative_to(root)), m.group(1))
            for p in sorted(root.glob("**/*.y*ml")) if ".git" not in p.parts
            for m in rx.finditer(p.read_text(errors="replace"))]


def helm_object_tag():
    img = yaml.safe_load((fleetlib.repo_root() / FLEET).read_text())["helm"]["values"]["server"]["image"]
    assert f'{img["registry"]}/{img["repository"]}' == REPO, img
    return str(img["tag"])


def custom_extractions(rel):
    """[(depName, currentValue)] the regex customManagers pull out of one file."""
    text = (fleetlib.repo_root() / rel).read_text()
    out = []
    for cm in renovate().get("customManagers", []):
        pats = [re.compile(p.strip("/")) for p in cm.get("managerFilePatterns", [])]
        if not any(p.search(rel) for p in pats):
            continue
        for ms in cm["matchStrings"]:
            for m in re.finditer(js_to_py(ms), text):
                g = m.groupdict()
                out.append((g.get("depName") or cm.get("depNameTemplate"), g["currentValue"]))
    return out


class OpenbaoPinsMoveTogether(unittest.TestCase):
    def test_every_pin_names_the_same_version(self):
        pins = string_pins() + [(FLEET + " (helm object)", helm_object_tag())]
        self.assertGreaterEqual(len(pins), 3, f"scan found too few openbao pins: {pins}")
        self.assertEqual(1, len({tag for _, tag in pins}),
                         f"openbao pins disagree - server, unsealer and bootstrap must run one build: {pins}")

    def test_both_fleet_yaml_pins_are_read_by_a_custom_manager(self):
        got = custom_extractions(FLEET)
        self.assertEqual(sorted([(REPO, helm_object_tag())] * 2), sorted(got),
                         "a customManager must extract BOTH openbao/fleet.yaml pins "
                         "(helm object tag and unsealer image string), and nothing else")

    def test_every_openbao_pin_lands_in_one_group(self):
        rules = [r for r in renovate().get("packageRules", [])
                 # matchDepNames: the depName is the zot spelling for BOTH managers, while
                 # registryAliases / packageNameTemplate move packageName to quay.io.
                 if REPO in r.get("matchDepNames", []) and r.get("groupName")]
        self.assertEqual(1, len(rules), "exactly one packageRule must group openbao")
        self.assertTrue({"kubernetes", "custom.regex"} <= set(rules[0].get("matchManagers", [])),
                        "the group must span the CronJob pin (kubernetes) and the fleet.yaml "
                        "pins (custom.regex), or one Renovate MR bumps only some of them")
        self.assertIsNot(rules[0].get("automerge"), True, "the secrets standby stays MR-only")


if __name__ == "__main__":
    unittest.main()
