"""The Harvester event-tailer must run with its Prometheus counters switched off.

harvester-default-event-tailer (EventTailer from the rancher-logging Addon) OOMKilled every
2-4 days at its 100Mi limit (O2 "PodOOMKilled on event-tailer", infra-2j06z): a clean
sawtooth from ~30Mi to 100Mi on whichever node it ran, 2026-10-20 .. 11-18. Not a load
problem: eventrouter (heptiolabs, unchanged in the harvester fork) defaults
`enable-prometheus` to true and counts every event into CounterVecs labelled by
involved_object_name. Those series are never deleted, so every churned pod (CI runners,
Windmill jobs, Longhorn replicas) adds series forever: 36,746 after 44 h of uptime. Nothing
scrapes them (no ServiceMonitor/VMPodScrape, 0 series in vmsingle).

The key can only be set in /etc/eventrouter/config.json (no env, no flag), and the
logging-operator generates that ConfigMap with no field for it. So the Addon overrides the
pod's `config-volume` with a ConfigMap we own. Probed live 2026-11-19 with a throwaway
EventTailer: /metrics not served, RSS flat at 20Mi, events still on stdout.

The ConfigMap must keep the operator's own `sink` and position path: dropping
`lastResourceVersionPositionPath` would replay every event on each restart.
"""

import json
import unittest

import yaml

import fleetlib

BUNDLE = "monitoring-logging"
ADDON = f"{BUNDLE}/addon.yml"
# What logging-operator 4.10 writes into the ConfigMap it generates (read live).
OPERATOR_CONFIG = {"sink": "stdout", "lastResourceVersionPositionPath": "/var/pos/event-tailer"}


def event_tailer_values() -> dict:
    addon = next(d for d in fleetlib.load_docs(ADDON)
                 if d.get("kind") == "Addon" and d["metadata"]["name"] == "rancher-logging")
    return yaml.safe_load(addon["spec"]["valuesContent"])["eventTailer"]


def bundle_configmap(name: str) -> dict | None:
    for f in fleetlib.files_under(BUNDLE):
        for d in fleetlib.load_docs(f):
            if d.get("kind") == "ConfigMap" and d["metadata"]["name"] == name:
                return d
    return None


class EventTailerCountersDisabled(unittest.TestCase):
    def test_config_volume_is_ours_and_disables_prometheus(self):
        volumes = event_tailer_values().get("workloadOverrides", {}).get("volumes", [])
        cfg = next((v for v in volumes if v.get("name") == "config-volume"), None)
        self.assertIsNotNone(cfg, "eventTailer.workloadOverrides must override config-volume")
        cm = bundle_configmap(cfg["configMap"]["name"])
        self.assertIsNotNone(cm, f"ConfigMap {cfg['configMap']['name']} not in {BUNDLE}")
        self.assertEqual(cm["metadata"].get("namespace"), "cattle-logging-system")
        config = json.loads(cm["data"]["config.json"])
        self.assertIs(config.get("enable-prometheus"), False)
        for key, want in OPERATOR_CONFIG.items():
            self.assertEqual(config.get(key), want, key)


if __name__ == "__main__":
    unittest.main()
