"""Every MQTT client authenticates; the broker knows exactly those users.

infra-az8.24 (pentest 2026-10-22 F24, re-found by the DAST network scan 2026-11-10): the
mosquitto broker accepted anonymous clients from every pod and the LAN. The clients, read
from the broker's own connection log: Home Assistant, Frigate, the findmy collector (all
pods) and the Ruuvi Gateway (LAN, 192.168.1.172) -- whose MQTT output was then switched off,
since nothing read it. Each pod client has its own user (password in Akeyless at
/mdapi/mqtt/users/<user>), a topic ACL holding it to what it does today, and a
NetworkPolicy admitting only those three pods.

What breaks if it regresses:
  * a client without credentials is refused once anonymous access is off -- so every
    in-git client must carry its user and a password from the secret store (Home
    Assistant's lives in its config entry, set through HA's reconfigure flow, and the
    Ruuvi Gateway's in its own web UI);
  * the broker's password file is built at start from the secret, never from git, and must
    hold every user -- a missing one locks that client out;
  * findmy must fail loud without credentials rather than connect anonymously.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import yaml

from tests.fleetlib import repo_root

ROOT = Path(repo_root())
# ruuvi-gateway was a user until its MQTT output was switched off (nothing read it; HA reads
# the gateway over HTTP), so its stored credential grants nothing.
USERS = {"homeassistant", "frigate", "findmy"}
# Who reaches the broker: exactly the three client pods.
CLIENT_PODS = {("home-assistant", "home-assistant"), ("frigate", "frigate"), ("findmy", "findmy-collector")}


def docs(rel: str) -> list[dict]:
    return [d for d in yaml.safe_load_all((ROOT / rel).read_text()) if d]


def one(rel: str, kind: str, name: str | None = None) -> dict:
    (d,) = [d for d in docs(rel) if d["kind"] == kind and (name is None or d["metadata"]["name"] == name)]
    return d


def es_keys(es: dict) -> dict[str, str]:
    return {e["secretKey"]: e["remoteRef"]["key"] for e in es["spec"]["data"]}


class Broker(unittest.TestCase):
    def setUp(self):
        self.conf = one("mqtt/eclipse-mosquitto-cm.yml", "ConfigMap")["data"]["mosquitto.conf"]
        self.pod = one("mqtt/eclipse-mosquitto-deploy.yml", "Deployment")["spec"]["template"]["spec"]

    def conf_values(self, key):
        return [line.split(None, 1)[1] for line in self.conf.splitlines() if line.split(None, 1)[:1] == [key]]

    def test_every_user_has_its_password_from_the_secret_store(self):
        es = one("mqtt/eclipse-mosquitto-es.yml", "ExternalSecret")
        self.assertEqual(es_keys(es), {u: f"/mdapi/mqtt/users/{u}" for u in USERS})

    def test_the_password_file_is_built_from_that_secret_and_read_by_the_broker(self):
        (path,) = self.conf_values("password_file")
        (init,) = self.pod["initContainers"]
        script = init["args"][0]
        self.assertIn("mosquitto_passwd -U", script)
        for u in USERS:
            self.assertIn(u, script.split("for u in", 1)[1].split(";", 1)[0])
        es_name = one("mqtt/eclipse-mosquitto-es.yml", "ExternalSecret")["spec"]["target"]["name"]
        secret_vols = {v["name"] for v in self.pod["volumes"] if v.get("secret", {}).get("secretName") == es_name}
        self.assertTrue(secret_vols & {m["name"] for m in init["volumeMounts"]})
        broker = self.pod["containers"][0]
        self.assertTrue(any(path.startswith(m["mountPath"] + "/") for m in broker["volumeMounts"]), path)
        # The broker never mounts the plaintext secret, only the hashed file.
        self.assertFalse(secret_vols & {m["name"] for m in broker["volumeMounts"]})

    def test_each_user_is_held_to_its_own_topics(self):
        (path,) = self.conf_values("acl_file")
        acl = one("mqtt/eclipse-mosquitto-cm.yml", "ConfigMap")["data"]["aclfile"]
        rules, user = {}, None
        for line in acl.splitlines():
            words = line.split()
            if not words or words[0].startswith("#"):
                continue
            if words[0] == "user":
                user = words[1]
                rules[user] = []
            else:
                self.assertIsNotNone(user, f"rule before any user applies to anonymous: {line}")
                self.assertEqual(words[0], "topic", line)
                rules[user].append(tuple(words[1:]))
        self.assertEqual(set(rules), USERS)
        # No user may write everywhere, and findmy only ever writes its own tracker topics.
        self.assertTrue(all(t[-1] not in ("#", "+/#") for rs in rules.values() for t in rs), rules)
        self.assertEqual(sorted(rules["findmy"]), [("write", "findmy/+/attributes"),
                                                   ("write", "homeassistant/device_tracker/+/config")])
        # 2026-11-10 21:24Z: a ConfigMap-mounted acl_file is root-owned and world-readable, and
        # mosquitto 2.1 refuses to open it -> CrashLoop. It must be the init container's 0600 copy.
        self.assertEqual(path, "/mosquitto/auth/aclfile")
        init = self.pod["initContainers"][0]["args"][0]
        self.assertIn("cp /config/aclfile /mosquitto/auth/aclfile", init)
        self.assertIn("chown mosquitto:mosquitto /mosquitto/auth/aclfile", init)
        self.assertIn("chmod 0600 /mosquitto/auth/aclfile", init)

    def test_only_the_client_pods_reach_the_broker(self):
        (np,) = [d for d in docs("mqtt/eclipse-mosquitto-netpol.yml") if d["kind"] == "NetworkPolicy"]
        self.assertEqual(np["spec"]["podSelector"], {"matchLabels": {"app": "eclipse-mosquitto"}})
        self.assertEqual(np["spec"]["policyTypes"], ["Ingress"])
        (rule,) = np["spec"]["ingress"]
        self.assertEqual(rule["ports"], [{"protocol": "TCP", "port": 1883}])
        got = {(p["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"], p["podSelector"]["matchLabels"]["app"])
               for p in rule["from"]}
        self.assertEqual(got, CLIENT_PODS)
        self.assertTrue(all(set(p) == {"namespaceSelector", "podSelector"} for p in rule["from"]))

    def test_anonymous_access_is_off(self):
        self.assertEqual(self.conf_values("allow_anonymous"), ["false"])


class Frigate(unittest.TestCase):
    def test_frigate_logs_in_as_frigate_with_the_stored_password(self):
        cfg = yaml.safe_load(one("frigate/frigate-cm.yml", "ConfigMap")["data"]["config.yaml"])
        self.assertEqual((cfg["mqtt"]["user"], cfg["mqtt"]["password"]), ("frigate", "{FRIGATE_MQTT_PASSWORD}"))
        es = one("frigate/frigate-es.yml", "ExternalSecret", "frigate-env-secret")
        self.assertEqual(es_keys(es)["FRIGATE_MQTT_PASSWORD"], "/mdapi/mqtt/users/frigate")


def load_collector() -> types.ModuleType:
    src = one("findmy/findmy-code-cm.yml", "ConfigMap")["data"]["collector.py"]
    stubs = {"paramiko": types.ModuleType("paramiko"), "findmy": types.ModuleType("findmy")}
    stubs["findmy"].AppleAccount = stubs["findmy"].FindMyAccessory = object
    mod = types.ModuleType("collector")
    with mock.patch.dict(sys.modules, stubs):
        exec(compile(src, "collector.py", "exec"), mod.__dict__)
    return mod


class FakeClient:
    def __init__(self, *_a):
        self.calls = []

    def username_pw_set(self, user, password):
        self.calls.append(("auth", user, password))

    def connect(self, host, port, keepalive):
        self.calls.append(("connect", host, port))


class Findmy(unittest.TestCase):
    def setUp(self):
        self.c = load_collector()
        self.mqtt = types.SimpleNamespace(Client=FakeClient, CallbackAPIVersion=types.SimpleNamespace(VERSION2=2))

    def test_credentials_are_set_before_connecting(self):
        with mock.patch.dict(self.c.os.environ, {"MQTT_USER": "findmy", "MQTT_PASSWORD": "pw"}):
            client = self.c.mqtt_connect(self.mqtt)
        self.assertEqual(client.calls[0], ("auth", "findmy", "pw"))
        self.assertEqual(client.calls[1][0], "connect")

    def test_no_credentials_fails_loud_instead_of_connecting_anonymously(self):
        for env in ({}, {"MQTT_USER": "findmy"}, {"MQTT_PASSWORD": "pw"}, {"MQTT_USER": "", "MQTT_PASSWORD": ""}):
            with self.subTest(env), mock.patch.dict(self.c.os.environ, env, clear=True):
                with self.assertRaises(RuntimeError):
                    self.c.mqtt_connect(self.mqtt)

    def test_the_deployment_passes_the_stored_credentials(self):
        (c,) = one("findmy/findmy-deploy.yml", "Deployment")["spec"]["template"]["spec"]["containers"]
        env = {e["name"]: e for e in c["env"]}
        self.assertEqual(env["MQTT_USER"]["value"], "findmy")
        ref = env["MQTT_PASSWORD"]["valueFrom"]["secretKeyRef"]
        es = one("findmy/findmy-es.yml", "ExternalSecret", ref["name"])
        self.assertEqual(es_keys(es)[ref["key"]], "/mdapi/mqtt/users/findmy")


if __name__ == "__main__":
    unittest.main()
