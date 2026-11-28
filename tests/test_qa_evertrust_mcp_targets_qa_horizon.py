"""qa/evertrust-mcp: the QA MCP copies talk to the QA Horizon only, with QA-minted keys.

infra-7rog.41.11, user decision 2026-11-25: horizon-mcp and stream-mcp are pointed at the
QA Horizon (qa/horizon) with API keys created IN that QA Horizon
(~/src/spot-qa/horizon-qa-mcp-key.py -> Secret horizon-mcp-qa). Never a prod Horizon API
key (prod's horizon-mcp reads the prod admin password from Akeyless), never a prod host,
never Stream (QA has none, and its licence is not part of the exception), and
kustodyan-mcp stays out of QA entirely (its RPS client credentials are prod).
"""

from __future__ import annotations

import unittest

import yaml

from fleetlib import repo_root

BUNDLE = repo_root() / "qa" / "evertrust-mcp"
QA_HORIZON = "http://horizon.horizon.svc.cluster.local:9000"


def docs() -> list[dict]:
    out = []
    for f in sorted(BUNDLE.glob("*.yml")):
        out += [d for d in yaml.safe_load_all(f.read_text()) if d]
    return out


def env(name: str) -> dict:
    (d,) = [d for d in docs() if d["kind"] == "Deployment" and d["metadata"]["name"] == name]
    return {e["name"]: e for e in d["spec"]["template"]["spec"]["containers"][0]["env"]}


class QaMcpTargetsQaHorizon(unittest.TestCase):
    def test_no_secret_or_externalsecret_in_git(self):
        kinds = {d["kind"] for d in docs()}
        self.assertFalse(kinds & {"Secret", "ExternalSecret"})

    def test_both_copies_point_at_the_qa_horizon(self):
        self.assertEqual(env("horizon-mcp")["HORIZON_URL"]["value"], QA_HORIZON)
        self.assertEqual(env("stream-mcp")["STREAM_URL"]["value"], QA_HORIZON)

    def test_every_credential_comes_from_the_qa_minted_secret(self):
        for app in ("horizon-mcp", "stream-mcp"):
            for name, e in env(app).items():
                ref = e.get("valueFrom", {}).get("secretKeyRef")
                if ref:
                    self.assertEqual(ref["name"], "horizon-mcp-qa", f"{app}/{name}")
            self.assertNotEqual(env(app).get("HORIZON_API_ID", {}).get("value"), "administrator", app)

    def test_the_tracer_is_on_and_names_the_service(self):
        for app in ("horizon-mcp", "stream-mcp"):
            e = env(app)
            self.assertEqual(e["DD_IAST_ENABLED"]["value"], "true", app)
            self.assertEqual(e["DD_SERVICE"]["value"], app, app)

    def test_kustodyan_mcp_is_not_in_qa(self):
        self.assertNotIn("kustodyan", "\n".join(f.read_text() for f in BUNDLE.glob("*")).replace("kustodyan-mcp stays", ""))


if __name__ == "__main__":
    unittest.main()
