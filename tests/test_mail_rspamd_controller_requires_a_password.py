"""The rspamd controller grants nothing by source address (infra-o4i3m.3).

On 2026-11-10 any pod, and any LAN host, could write to the rspamd controller on
192.168.1.48:11334 with no credential: `GET /auth` answered `read_only: false`.
A hand-written line on the mail-config PVC (`custom-commands.conf`:
`set-option-for-controller secure_ip "0.0.0.0/0"`) made every source address
"secure", which skips the password check entirely. The base config still held
rspamd's shipped default password, and `allow_file_and_shm_inputs = true` lets a
trusted client make rspamd read any local file. With that, a caller could train
ham, change actions, edit maps and whitelist its own mail. The oauth2 ingress in
front of rspamd.mdapi.ch was decoration: the LB port bypassed it.

Pinned here:
  * user-patches.sh writes override.d/worker-controller.inc. Only loopback is
    secure_ip (the Dovecot learn pipes call 127.0.0.1:11334), file/shm inputs
    are off, and password + enable_password are the bcrypt hash from Akeyless.
  * FAIL CLOSED: with no valid hash the controller binds to 127.0.0.1 only, so a
    missing secret never re-opens it (and never falls back to the shipped default).
  * 11334 and 9102 are not on the LoadBalancer; the ingress and the ServiceMonitor
    reach them through a ClusterIP Service instead.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from tests.fleetlib import repo_root

DMS = repo_root() / "mail" / "docker-mailserver"
VALUES = DMS / "values.yml"
INC = "/etc/rspamd/override.d/worker-controller.inc"
BEGIN = "# BEGIN rspamd-controller"
END = "# END rspamd-controller"
GOOD = "$2$ygfx9phq3tc4ifjfzor9ju59t5snuwca$nuyt7s6psp9jr6rmqp4x8iz4wfp44ri7qf3kw3xx7o3zzen4k7uy"


def values() -> dict:
    return yaml.safe_load(VALUES.read_text())


def controller_block() -> str:
    script = values()["configMaps"]["user-patches.sh"]["data"]
    start, end = script.index(BEGIN), script.index(END)
    return script[start:end]


def run_block(hash_value: str | None) -> list[str]:
    """Run the user-patches block with INC redirected; return the written lines."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "worker-controller.inc"
        env = {"PATH": os.environ["PATH"]}
        if hash_value is not None:
            env["RSPAMD_CONTROLLER_HASH"] = hash_value
        subprocess.run(["bash", "-euo", "pipefail", "-c",
                        controller_block().replace(INC, str(out))],
                       env=env, check=True, capture_output=True, text=True)
        return [l.strip() for l in out.read_text().splitlines() if l.strip()]


def kustomize_docs(name: str) -> list[dict]:
    return [d for d in yaml.safe_load_all((DMS / "kustomize" / name).read_text()) if d]


class ControllerConfig(unittest.TestCase):
    def test_valid_hash_sets_both_passwords_and_loopback_only(self):
        lines = run_block(GOOD)
        self.assertIn(f'password = "{GOOD}";', lines)
        self.assertIn(f'enable_password = "{GOOD}";', lines)
        self.assertEqual(sorted(l for l in lines if l.startswith("secure_ip")),
                         ['secure_ip = "127.0.0.1";', 'secure_ip = "::1";'])
        self.assertIn("allow_file_and_shm_inputs = false;", lines)
        self.assertFalse(any(l.startswith("bind_socket") for l in lines), lines)

    def test_missing_or_malformed_hash_binds_localhost_only(self):
        for bad in (None, "", "q1", "plaintext-password", "$2$only-one-part"):
            with self.subTest(hash=bad):
                lines = run_block(bad)
                self.assertIn('bind_socket = "127.0.0.1:11334";', lines)
                self.assertFalse(any("password" in l for l in lines), lines)
                self.assertIn("allow_file_and_shm_inputs = false;", lines)

    def test_no_secure_ip_beyond_loopback_anywhere_in_values(self):
        for line in VALUES.read_text().splitlines():
            if "secure_ip" in line and not line.lstrip().startswith("#"):
                self.assertRegex(line, r'"(127\.0\.0\.1|::1)"', line)

    def test_hash_comes_from_akeyless(self):
        es = next(d for d in values()["extraDeploy"]
                  if d["kind"] == "ExternalSecret"
                  and d["metadata"]["name"] == "docker-mailserver-env-secret")
        keys = {e["secretKey"]: e["remoteRef"]["key"] for e in es["spec"]["data"]}
        self.assertIn("RSPAMD_CONTROLLER_HASH", keys)


class ControllerNotOnTheLoadBalancer(unittest.TestCase):
    def test_lb_service_drops_controller_and_metrics_ports(self):
        svc = kustomize_docs("service-lb-no-controller.yaml")[0]
        deleted = {p["port"] for p in svc["spec"]["ports"] if p.get("$patch") == "delete"}
        self.assertEqual(deleted, {11334, 9102})

    def test_port_deletes_run_before_any_port_is_added(self):
        """After a patch adds a port with `protocol`, kustomize keys ports on
        port+protocol and a later delete silently matches nothing (both ports stayed
        on the LB in the first render of this change)."""
        k = yaml.safe_load((DMS / "kustomize" / "kustomization.yaml").read_text())
        order = k["patchesStrategicMerge"]
        self.assertLess(order.index("service-lb-no-controller.yaml"),
                        order.index("service-lan-plain-smtp.yaml"))

    def test_internal_service_is_clusterip_with_both_ports(self):
        svc = next(d for d in values()["extraDeploy"]
                   if d["kind"] == "Service"
                   and d["metadata"]["name"] == "docker-mailserver-internal")
        self.assertEqual(svc["spec"].get("type", "ClusterIP"), "ClusterIP")
        self.assertEqual({p["name"]: p["port"] for p in svc["spec"]["ports"]},
                         {"rspamd": 11334, "metrics": 9102})

    def test_ingress_backend_is_the_internal_service(self):
        ing = kustomize_docs("ingress-rspamd-internal.yaml")[0]
        backends = {p["backend"]["service"]["name"]
                    for r in ing["spec"]["rules"] for p in r["http"]["paths"]}
        self.assertEqual(backends, {"docker-mailserver-internal"})


if __name__ == "__main__":
    unittest.main()
