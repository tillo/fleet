"""The CIS regression baseline must name the kube-bench version and profile that run.

2026-09-15 Renovate bumped `aquasec/kube-bench` v0.13.0 -> v0.16.0. Nothing on the
estate changed, yet CisBenchmarkRegression fired on all three nodes a week later: the new
release measures a different set of checks (two retired, three added). Running both
versions against the same nodes in the same second proved it was the tool. The rule's
comment already said "keep the baseline pinned to the TOOL VERSION", and a comment did
not stop it.

Since 2026-10-30 the accepted baseline is a SET of check ids in
monitoring-rules/51-cis-benchmark.yml, and CisBenchmarkToolChanged names the version
and profile that set was derived with. This test holds those equal to the image tag and
the BENCHMARK env in kube-bench/cronjob.yml. A bump MR (kube-bench is MR-only in
renovate.json) therefore stays red until the person merging it has re-derived the set
and moved the version in the rule — before the alert, not after it.
"""

import re
import unittest

import fleetlib

CRONJOB = "kube-bench/cronjob.yml"
RULES = "monitoring-rules/51-cis-benchmark.yml"


def pinned():
    """(image tag, BENCHMARK env) of the kube-bench container in the CronJob."""
    for doc in fleetlib.load_docs(CRONJOB):
        if doc.get("kind") != "CronJob":
            continue
        pod = doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        for c in pod.get("containers") or []:
            if c.get("name") != "kube-bench":
                continue
            m = re.match(r"^[^@]*/kube-bench:([^@]+)", c.get("image", ""))
            env = {e.get("name"): e.get("value") for e in c.get("env") or []}
            return (m.group(1) if m else None), env.get("BENCHMARK")
    return None, None


def rules():
    for doc in fleetlib.load_docs(RULES):
        for group in (doc.get("spec") or {}).get("groups") or []:
            for rule in group.get("rules") or []:
                if isinstance(rule, dict):
                    yield rule


class CisBaselineMatchesPinTest(unittest.TestCase):
    def test_tool_changed_rule_names_the_pinned_version_and_profile(self):
        tag, benchmark = pinned()
        # ⛔ Empty selection is not a pass: a moved file or renamed container must
        # fail here, not silently skip the comparison.
        self.assertTrue(tag, f"no kube-bench image tag found in {CRONJOB}")
        self.assertTrue(benchmark, f"no BENCHMARK env found in {CRONJOB}")

        tool_rule = [r for r in rules() if r.get("alert") == "CisBenchmarkToolChanged"]
        self.assertEqual(1, len(tool_rule), f"{RULES}: expected one CisBenchmarkToolChanged")
        expr = tool_rule[0]["expr"]
        versions = set(re.findall(r'version!="([^"]+)"', expr))
        profiles = set(re.findall(r'benchmark!="([^"]+)"', expr))

        self.assertEqual(
            {tag}, versions,
            f"{CRONJOB} runs kube-bench {tag}, but the accepted CIS baseline in {RULES} "
            f"was derived with {sorted(versions)}. Re-derive the failing SET with {tag} on "
            f"every node, update the check regexes, then move the version in "
            f"CisBenchmarkToolChanged (and the summaries) to {tag}.")
        self.assertEqual(
            {benchmark}, profiles,
            f"{CRONJOB} runs profile {benchmark}, but CisBenchmarkToolChanged in {RULES} "
            f"names {sorted(profiles)}. Re-derive the baseline for the new profile.")

    def test_summaries_quote_the_same_version(self):
        tag, _ = pinned()
        quoted = set()
        for rule in rules():
            for text in (rule.get("annotations") or {}).values():
                quoted.update(re.findall(r"kube-bench (v\d+\.\d+\.\d+)", str(text)))
        self.assertTrue(quoted, f"{RULES}: no annotation quotes a kube-bench version")
        self.assertEqual({tag}, quoted,
                         f"{RULES}: annotations quote {sorted(quoted)}, the CronJob runs {tag}")


if __name__ == "__main__":
    unittest.main()
