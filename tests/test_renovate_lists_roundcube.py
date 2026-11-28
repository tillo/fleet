#!/usr/bin/env python3
"""mdapi/roundcube is one of the bot's repositories.

Its image builds from apache/Dockerfile (no root Dockerfile), so the image-repo
preset's repin_sha256 task finds nothing to re-pin there and exits 0 rather than
failing every update (infra-ik4p.14). Its renovate.json (mdapi/roundcube!3) extends
local>mdapi/renovate-config:image-repo; unlisted here, the bot never visits it and
those pins age until a CVE alert, as they did before infra-ik4p.
"""

from __future__ import annotations

import unittest

import yaml

import fleetlib


class RoundcubeIsARenovateRepository(unittest.TestCase):
    def test_the_renovate_job_lists_mdapi_roundcube(self):
        ci = yaml.safe_load((fleetlib.repo_root() / ".gitlab-ci.yml").read_text(encoding="utf-8"))
        repos = ci["renovate"]["variables"]["RENOVATE_REPOSITORIES"].split(",")
        self.assertIn("mdapi/roundcube", repos)


if __name__ == "__main__":
    unittest.main()
