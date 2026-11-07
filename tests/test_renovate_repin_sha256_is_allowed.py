#!/usr/bin/env python3
"""The image repos' SHA re-pin is on the bot's allow-list, and nothing else rides on it.

An image repo's renovate.json runs `python3 /tmp/renovate-tools/repin_sha256.py
{{{depName}}}` after every update, so a version+SHA pair (gh, node, herdr, clamav,
step-cli) lands with its SHA re-pinned instead of failing `sha256sum -c` (jump-bastion
!64, !66 on 2026-11-02). The script is THIS repo's tools/repin_sha256.py, installed by
the renovate job's before_script: the image repos used to carry their own copy as
bin/repin_sha256.py, which let any listed repo run its own code under the bot token
(infra-ik4p.1). Renovate tests the TEMPLATED command against RENOVATE_ALLOWED_COMMANDS,
and a miss is an artifact error on the MR, not a failed pipeline. The bot runs the
command through a shell, so a depName is admitted only from a charset with no shell
meaning.
"""

from __future__ import annotations

import importlib.util
import json
import re
import unittest

import yaml

import fleetlib

INSTALLED = "/tmp/renovate-tools/repin_sha256.py"
TEMPLATE = "python3 %s {{{depName}}}" % INSTALLED
# Every depName jump-bastion's Renovate updates: the Dockerfile `# renovate:` pins, its
# base images, and the npm/pip packages (the task runs on every update; the script is a
# no-op for those). Taken from the repo and its open MRs on 2026-11-03.
DEP_NAMES = (
    "cli/cli", "node", "herdrdev/herdr", "herdr-core", "go.podman.io/skopeo",
    "google.golang.org/grpc", "golang.org/x/crypto",
    "gitlab.mdapi.ch/mdapi/dependency_proxy/containers/debian",
    "@earendil-works/pi-coding-agent", "tree-sitter-language-pack",
    # mdapi/docker-mailserver (infra-ik4p.1): its two pins and its frozen base.
    "Cisco-Talos/clamav", "smallstep/cli", "registry.mdapi.ch/mdapi/docker-mailserver",
    "docker/dockerfile",
    # The rest of the rollout: chrony, kubernetes-mcp-server (Go modules), stream-mcp and
    # horizon-mcp (npm), wan-speedtest (a custom datasource), and their docker bases.
    "chrony/chrony", "github.com/containers/kubernetes-mcp-server", "golang.org/x/net",
    "golang.org/x/text", "oras.land/oras-go/v2", "github.com/containerd/containerd",
    "golang", "supergateway", "@evertrust/stream-mcp", "@evertrust/horizon-mcp", "npm",
    "ookla-speedtest", "debian",
    # horizon-ciphertrust is a maven project, and a maven depName is `groupId:artifactId`.
    "org.javassist:javassist", "ch.tillo.tink:tink-ciphertrust",
    "org.apache.maven.plugins:maven-jar-plugin", "maven",
    # A docker image on a registry that has a port.
    "registry.example:5000/team/image",
)
HOSTILE = (
    "node; id", "node && id", "$(id)", "`id`", "node | sh", "node > /tmp/x",
    "node --dockerfile /etc/passwd", "-h", "node\nid", "'node'",
)


def renovate_job() -> dict:
    ci = yaml.safe_load((fleetlib.repo_root() / ".gitlab-ci.yml").read_text(encoding="utf-8"))
    return ci["renovate"]


def allow_list() -> list[str]:
    return json.loads(renovate_job()["variables"]["RENOVATE_ALLOWED_COMMANDS"]["value"])


def allowed(cmd: str) -> bool:
    return any(re.search(p, cmd) for p in allow_list())


class RepinSha256IsAllowed(unittest.TestCase):
    def test_every_real_dep_name_is_admitted(self):
        for dep in DEP_NAMES:
            with self.subTest(dep=dep):
                self.assertTrue(allowed(TEMPLATE.replace("{{{depName}}}", dep)))

    def test_nothing_but_a_dep_name_is_admitted(self):
        for dep in HOSTILE:
            with self.subTest(dep=dep):
                self.assertFalse(allowed(TEMPLATE.replace("{{{depName}}}", dep)))
        self.assertFalse(allowed("sh -c 'python3 %s node'" % INSTALLED))
        self.assertFalse(allowed("python3 %s" % INSTALLED))

    def test_the_script_refuses_exactly_what_the_allow_list_refuses(self):
        # Two copies of one charset: a depName the bot admits but the script refuses is
        # an artifact error on every such MR (the maven `groupId:artifactId` gap).
        spec = importlib.util.spec_from_file_location(
            "repin_sha256", fleetlib.repo_root() / "tools" / "repin_sha256.py")
        repin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(repin)
        for dep in DEP_NAMES + HOSTILE:
            with self.subTest(dep=dep):
                self.assertEqual(bool(re.match(repin.DEP_NAME, dep)),
                                 allowed(TEMPLATE.replace("{{{depName}}}", dep)))

    def test_no_repo_supplied_script_is_admitted(self):
        # The command must name the copy this job installs, never a path inside the
        # repo being updated: that file is whatever the repo's main holds.
        for path in ("bin/repin_sha256.py", "./repin_sha256.py", "tools/repin_sha256.py",
                     "/tmp/renovate-tools/../x.py", "renovate-tools/repin_sha256.py"):
            with self.subTest(path=path):
                self.assertFalse(allowed("python3 %s node" % path))

    def test_the_job_installs_this_repos_script_where_the_command_names_it(self):
        install = "install -D -m 0555 tools/repin_sha256.py %s" % INSTALLED
        self.assertIn(install, renovate_job()["before_script"])
        self.assertTrue((fleetlib.repo_root() / "tools" / "repin_sha256.py").is_file())


if __name__ == "__main__":
    unittest.main()
