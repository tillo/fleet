#!/usr/bin/env python3
"""The bot can read this GitLab's own maven package registry.

mdapi/horizon-ciphertrust depends on ch.tillo.tink:tink-ciphertrust, which is published
to the private GitLab project mdapi/tink-java-ciphertrust (maven packages API). The
platform token Renovate registers for GitLab is a `hostType: gitlab` rule, and the maven
datasource does not use it: without a `hostType: maven` rule the lookup gets 401 and
returns no releases, with no error MR to say so (checked against Renovate's own lookup,
infra-ik4p.1). The rule names only this GitLab, the host the token already goes to.
"""

from __future__ import annotations

import json
import unittest

import yaml

import fleetlib


def host_rules() -> list[dict]:
    ci = yaml.safe_load((fleetlib.repo_root() / ".gitlab-ci.yml").read_text(encoding="utf-8"))
    return json.loads(ci["renovate"]["variables"]["RENOVATE_HOST_RULES"])


class MavenHostRule(unittest.TestCase):
    def test_the_gitlab_maven_registry_gets_the_bot_token(self):
        maven = [r for r in host_rules() if r.get("hostType") == "maven"]
        self.assertEqual(
            maven,
            [{"matchHost": "gitlab.mdapi.ch", "hostType": "maven",
              "username": "renovate-bot", "password": "$RENOVATE_TOKEN"}],
        )


if __name__ == "__main__":
    unittest.main()
