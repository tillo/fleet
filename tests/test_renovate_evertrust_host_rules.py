#!/usr/bin/env python3
"""The bot can read Evertrust's two private sources, and only Evertrust gets that login.

registry.evertrust.io (the Horizon base image of mdapi/horizon-ciphertrust) and
repo.evertrust.io (the horizon-cli RPM that mdapi/horizon-cli bakes) both answer 401
to anonymous lookups (infra-ik4p.15). Without a host rule, Renovate returns no
releases and opens no MR, so the pin silently stops moving. One read-only vendor PULL
credential serves both hosts. It reaches the job as the protected fleet CI variables
EVERTRUST_REGISTRY_USER/PASSWORD, projected from Akeyless
/kyos/evertrust/registry/dockerconfigjson.

Each rule names its host EXACTLY and is scoped to the one datasource that reads it,
so the credential is never sent anywhere else.
"""

from __future__ import annotations

import json
import unittest

import yaml

import fleetlib

LOGIN = {"username": "$EVERTRUST_REGISTRY_USER", "password": "$EVERTRUST_REGISTRY_PASSWORD"}


def renovate_job() -> dict:
    ci = yaml.safe_load((fleetlib.repo_root() / ".gitlab-ci.yml").read_text(encoding="utf-8"))
    return ci["renovate"]


def host_rules() -> list[dict]:
    return json.loads(renovate_job()["variables"]["RENOVATE_HOST_RULES"])


class EvertrustHostRules(unittest.TestCase):
    def test_each_evertrust_host_gets_the_login_for_its_own_datasource_only(self):
        evertrust = [r for r in host_rules() if "evertrust" in json.dumps(r)]
        self.assertEqual(
            [dict(matchHost="registry.evertrust.io", hostType="docker", **LOGIN),
             dict(matchHost="repo.evertrust.io", hostType="rpm", **LOGIN)],
            evertrust)

    def test_the_login_goes_to_no_other_host(self):
        for rule in host_rules():
            if "$EVERTRUST_" in json.dumps(rule):
                with self.subTest(host=rule.get("matchHost")):
                    self.assertTrue(rule["matchHost"].endswith(".evertrust.io"))

    def test_horizon_cli_is_a_renovate_repository(self):
        repos = renovate_job()["variables"]["RENOVATE_REPOSITORIES"].split(",")
        self.assertIn("mdapi/horizon-cli", repos)


if __name__ == "__main__":
    unittest.main()
