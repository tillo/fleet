"""Every kube-state-metrics series an ALERT reads must be fed to OpenObserve.

THE INCIDENT (2026-11-08, infra-r9mzd.46). The O2 SRE agent's only metrics tool
(PrometheusRangeQuery) queries O2's OWN store, not VictoriaMetrics. So an alert whose
evidence is a KSM series hands the autonomous RCA an empty result; the RCA reports that
honestly and then concludes "cause unknown". Measured on the PodBelowVpaFloor incident
(infra-b3ojh), through the agent's own tool as the agent's own service account:
`kube_pod_container_resource_requests` and `kube_verticalpodautoscaler_..._lowerbound`
both returned `[]` in O2, while both exist in VictoriaMetrics -- which is how the alert
itself evaluates, and how the incident was finally measured. The RCA was not wrong to say
it was blind; it had no path to the evidence.

The fix added that class to the O2 remote-write keep-list in monitoring-vm/vmagent.yml,
bound to `resource="memory"`. This test asserts the invariant the incident taught:
whatever resource dimension an ALERT queries, O2 must receive it. It replays the real
relabel chain read out of vmagent.yml instead of restating it, so the list and the test
cannot drift apart.

Two deliberate consequences, both asserted below:
  * `resource="cpu"` stays OUT. No alert reads it -- the cpu consumers are the recorded
    namespace_/cluster_ aggregates in 35-k8s-recording-rules.yml, which no rule file uses
    -- and it would be +776 series for nothing.
  * the pre-existing value-level filters (kube_pod_status_reason on Evicted,
    kube_persistentvolumeclaim_status_phase on Lost/Pending) keep working.
"""

from __future__ import annotations

import re
import unittest

import yaml

import fleetlib

# The metric families here that alerts pair with a resource dimension. `container_*` and
# the node/exporter families are in the feed for other reasons and are not the subject of
# this invariant.
RESOURCE_METRICS = re.compile(
    r"\b(kube_pod_container_resource_(?:requests|limits)|kube_verticalpodautoscaler_[a-z_]+)"
    r'\{[^}]*resource="([a-z_]+)"'
)


def o2_chain() -> list[dict]:
    """The inlineUrlRelabelConfig of the remoteWrite that targets OpenObserve."""
    spec = yaml.safe_load((fleetlib.repo_root() / "monitoring-vm/vmagent.yml").read_text())
    blocks = [rw for rw in spec["spec"]["remoteWrite"] if "openobserve" in rw["url"]]
    assert len(blocks) == 1, f"expected exactly one O2 remoteWrite, found {len(blocks)}"
    chain = blocks[0]["inlineUrlRelabelConfig"]
    assert chain, "the O2 remoteWrite has no relabel chain to test"
    return chain


def decides(chain: list[dict], name: str, labels: dict[str, str]) -> str:
    """Replay the chain in order, exactly as vmagent would for one series.

    Returns "keep", or a short reason the series never reaches O2.
    """
    labels = {**labels, "__name__": name}
    keep = next((r for r in chain if r.get("action") == "keep"), None)
    if keep and not re.fullmatch(keep["regex"], name):
        return "not in the keep list"
    # A marked group keeps only the label values a mark rule selected; everything else in
    # that group is dropped. RE2 has no negative lookahead, hence the two-step.
    for rule in chain:
        source = rule.get("source_labels", [])
        if rule.get("target_label") != "__tmp_keep" or not source:
            continue
        value = ";".join(labels.get(s, "") for s in source)
        if re.fullmatch(rule.get("regex", ""), value):
            labels["__tmp_keep"] = "1"
    for rule in chain:
        source = rule.get("source_labels", [])
        if rule.get("action") != "drop" or "__tmp_keep" not in source or not source:
            continue
        value = ";".join(labels.get(s, "") for s in source)
        if re.fullmatch(rule.get("regex", ""), value):
            return "marked group, unmarked value"
    return "keep"


def alert_rules():
    """(file, alert name, rule) for every alerting rule in a PrometheusRule/VMRule."""
    for rel in fleetlib.tracked_yaml():
        if fleetlib.is_vendored(rel):
            continue
        for doc in fleetlib.load_docs(rel):
            if not isinstance(doc, dict) or doc.get("kind") not in ("PrometheusRule", "VMRule"):
                continue
            for group in (doc.get("spec") or {}).get("groups") or []:
                for rule in group.get("rules") or []:
                    if isinstance(rule, dict) and rule.get("alert") and "expr" in rule:
                        yield rel, rule["alert"], rule


class RcaEvidenceReachesO2Test(unittest.TestCase):
    def test_every_resource_dimension_an_alert_reads_reaches_o2(self):
        chain = o2_chain()
        rules = list(alert_rules())
        self.assertTrue(rules, "discovery found no alerting rules - an empty selection is not a pass")

        needed: dict[tuple[str, str], str] = {}
        for rel, alert, rule in rules:
            for metric, resource in RESOURCE_METRICS.findall(str(rule["expr"])):
                needed.setdefault((metric, resource), f"{rel}:{alert}")
        self.assertTrue(needed, "no alert pairs a KSM metric with a resource dimension - check the regex")

        missing = [
            f"{metric} resource={resource} (read by {where}) -> {decides(chain, metric, {'resource': resource})}"
            for (metric, resource), where in sorted(needed.items())
            if decides(chain, metric, {"resource": resource}) != "keep"
        ]
        self.assertEqual(
            missing, [],
            "alerts read series that never reach O2, so the RCA is blind to them:\n  "
            + "\n  ".join(missing),
        )

    def test_the_cpu_dimension_stays_out(self):
        """Deliberate bound: no alert reads resource="cpu" from these metrics."""
        chain = o2_chain()
        for metric in (
            "kube_pod_container_resource_requests",
            "kube_pod_container_resource_limits",
            "kube_verticalpodautoscaler_status_recommendation_containerrecommendations_lowerbound",
        ):
            self.assertNotEqual(
                decides(chain, metric, {"resource": "cpu"}), "keep",
                f'{metric}{{resource="cpu"}} is being fed to O2; no alert reads it (+776 series)',
            )

    def test_the_existing_value_level_filters_still_hold(self):
        """The pre-existing marked-group rules must survive any edit to this chain."""
        chain = o2_chain()
        self.assertEqual(decides(chain, "kube_pod_status_reason", {"reason": "Evicted"}), "keep")
        self.assertNotEqual(decides(chain, "kube_pod_status_reason", {"reason": "NodeLost"}), "keep")
        self.assertEqual(decides(chain, "kube_persistentvolumeclaim_status_phase", {"phase": "Lost"}), "keep")
        self.assertNotEqual(decides(chain, "kube_persistentvolumeclaim_status_phase", {"phase": "Bound"}), "keep")
