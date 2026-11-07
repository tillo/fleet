#!/usr/bin/env python3
"""The 32 mdapi repos onboarded by infra-ik4p.28 are the bot's repositories.

The job runs with autodiscover off, so a repo it does not list is never read and its
pins age until a CVE alert. Each of these carries a renovate.json that extends
local>mdapi/renovate-config:image-repo, MR-only (their infra-ik4p.28 MRs).
mdapi/test-secrets-deletion_scheduled-27 is left out: it is marked for deletion.
"""

from __future__ import annotations

import unittest

import yaml

import fleetlib

ONBOARDED = (
        "mdapi/autoconfig",
        "mdapi/bussola",
        "mdapi/coiffuredreams",
        "mdapi/dellambrogio",
        "mdapi/elco-remocon-net-appdaemon",
        "mdapi/envuassu",
        "mdapi/external-dns-unbound-webhook",
        "mdapi/flaresolverr",
        "mdapi/formbricks-kustodyan",
        "mdapi/iomemory-vsl-loader",
        "mdapi/ivodellambrogio",
        "mdapi/joplin-mcp",
        "mdapi/keycloak",
        "mdapi/kustodyan-mcp",
        "mdapi/mail-mcp",
        "mdapi/mardi-gras",
        "mdapi/memsearch",
        "mdapi/mirror",
        "mdapi/nameserver",
        "mdapi/nextcloud-mcp",
        "mdapi/ntppool-agent",
        "mdapi/opennic-tier2",
        "mdapi/paperless-kustodyan",
        "mdapi/paperless-mcp",
        "mdapi/portal",
        "mdapi/rsync",
        "mdapi/spider3",
        "mdapi/threadfin",
        "mdapi/tink-java-ciphertrust",
        "mdapi/unbound",
        "mdapi/vecchiserver",
        "mdapi/znc",
)


class Ik4p28ReposAreRenovateRepositories(unittest.TestCase):
    def test_the_renovate_job_lists_every_onboarded_repo(self):
        ci = yaml.safe_load((fleetlib.repo_root() / ".gitlab-ci.yml").read_text(encoding="utf-8"))
        repos = ci["renovate"]["variables"]["RENOVATE_REPOSITORIES"].split(",")
        self.assertEqual([r for r in ONBOARDED if r not in repos], [])
        self.assertEqual(len(repos), len(set(repos)), "a repository is listed twice")


if __name__ == "__main__":
    unittest.main()
