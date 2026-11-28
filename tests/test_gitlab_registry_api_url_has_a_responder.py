#!/usr/bin/env python3
"""GitLab's registry api_url answers, and answers "no tags", while the registry is retired.

WHY THIS FILE EXISTS (2026-11-25, infra-5jnt8)
The registry SERVICE was retired on 2026-11-08 (top-level `registry.enabled: false`),
but `global.registry.enabled` must stay true so CI keeps CI_REGISTRY_IMAGE. Rails
therefore still calls api_url http://gitlab-registry.bootstrap.svc:5000 before it renames,
transfers or deletes a project (Project#has_container_registry_tags?,
Projects::DestroyService#remove_legacy_registry_tags). The Service no longer existed, so
every rename/transfer/delete failed for 17 days with "failed to connect to the container
registry", by API and from rails alike.

WHAT IS ASSERTED
- The chart still renders no registry of its own, so the stub's Service cannot collide.
- A tracked Service `gitlab-registry` in `bootstrap` serves port 5000.
- The stub's nginx config answers the three calls GitLab 19.6 makes, the way that makes
  rails conclude "no tags": GET /v2/<path>/tags/list -> 200 with an empty list, GET /v2/
  -> 200, and /gitlab/v1/ NOT 200/401 (else rails switches to the GitLab API and pages tags).
"""

from __future__ import annotations

import re
import unittest

import yaml

import fleetlib

STUB_DIR = "gitlab-registry-stub"


def tracked(kind: str, name: str, namespace: str) -> list[dict]:
    return [d for f in fleetlib.tracked_yaml() for d in fleetlib.load_docs(f)
            if d.get("kind") == kind and (d.get("metadata") or {}).get("name") == name
            and (d.get("metadata") or {}).get("namespace") == namespace]


def stub_conf() -> str:
    cms = tracked("ConfigMap", "gitlab-registry-stub", "bootstrap")
    if len(cms) != 1:
        raise AssertionError(f"expected one gitlab-registry-stub ConfigMap, found {len(cms)}")
    return cms[0]["data"]["default.conf"]


def location_returns(conf: str, location: str) -> tuple[int, str] | None:
    """(status, body) when the block opened by `location` starts with a `return`.

    Matches up to the closing quote, not a closing brace: the bodies are JSON."""
    m = re.search(re.escape(location) + r"\s*\{\s*return\s+(\d{3})\s+'([^']*)';", conf)
    return (int(m.group(1)), m.group(2)) if m else None


class RegistryStub(unittest.TestCase):
    def test_the_chart_renders_no_registry_of_its_own(self):
        values = yaml.safe_load((fleetlib.repo_root() / "gitlab" / "values.yaml").read_text())
        self.assertIs(values["registry"]["enabled"], False)
        self.assertIs(values["global"]["registry"]["enabled"], True)

    def test_a_service_answers_on_the_api_url(self):
        svcs = tracked("Service", "gitlab-registry", "bootstrap")
        self.assertEqual(len(svcs), 1, "no tracked Service gitlab-registry in bootstrap")
        ports = [p.get("port") for p in svcs[0]["spec"]["ports"]]
        self.assertIn(5000, ports)

    def test_tag_lists_are_empty(self):
        status, body = location_returns(stub_conf(), r"location ~ ^/v2/(.+)/tags/list$")
        self.assertEqual(status, 200)
        self.assertEqual(yaml.safe_load(body.replace("$1", "x"))["tags"], [])

    def test_the_base_endpoint_answers(self):
        self.assertEqual(location_returns(stub_conf(), "location = /v2/")[0], 200)

    def test_the_gitlab_api_is_not_advertised(self):
        got = location_returns(stub_conf(), "location /gitlab/")
        self.assertIsNotNone(got, "no explicit /gitlab/ location")
        self.assertNotIn(got[0], (200, 401))

    def test_location_returns_reads_status_and_body(self):
        conf = ("location = /a { return 418 '{\"t\":[]}'; }\n"
                "location = /b { add_header X 1; }")
        self.assertEqual(location_returns(conf, "location = /a"), (418, '{"t":[]}'))
        self.assertIsNone(location_returns(conf, "location = /b"))
        self.assertIsNone(location_returns(conf, "location = /c"))


if __name__ == "__main__":
    unittest.main()
