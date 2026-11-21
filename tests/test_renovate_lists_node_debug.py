#!/usr/bin/env python3
"""mdapi/node-debug is one of the bot's repositories.

The host-disk toolbox the OSD rebuild runbook runs on the nodes (infra-weops: tools baked
in, so no `apk add` in a privileged pod trips Falco critical). Its Dockerfile pins alpine by
digest and its renovate.json extends local>mdapi/renovate-config:image-repo; unlisted here,
the bot never visits it and that base digest ages until a CVE alert.
"""

from __future__ import annotations

import unittest

import yaml

import fleetlib


class NodeDebugIsARenovateRepository(unittest.TestCase):
    def test_the_renovate_job_lists_mdapi_node_debug(self):
        ci = yaml.safe_load((fleetlib.repo_root() / ".gitlab-ci.yml").read_text(encoding="utf-8"))
        repos = ci["renovate"]["variables"]["RENOVATE_REPOSITORIES"].split(",")
        self.assertIn("mdapi/node-debug", repos)


if __name__ == "__main__":
    unittest.main()
