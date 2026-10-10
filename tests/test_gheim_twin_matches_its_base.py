"""A `-gheim` twin must be its base's params plus exactly one guardrail name.

WHY THIS EXISTS (written 2026-10-08 with the twins, before it bit):

The `-gheim` model entries in `litellm/litellm-cm.yml` are near-duplicates of an existing
group -- same upstream model, same api_base, same timeouts, same retry/cooldown knobs --
with `gheim-rt-guard` added to `guardrails`. That is a copy, and a copy drifts. Nothing in
litellm notices: the twin keeps serving, silently on a STALE model. Concretely, when
`local-tail-cn-pro-gemma-large` was repointed from one LM Studio identifier to another, a
stale twin would keep asking for the old one, and the only symptom would be a door that
answers differently from the door it is named after.

So the assertion is deliberately mechanical: parse both entries and require the twin to
differ from its base by the `guardrails` key and NOTHING else, with the twin's list being
the base's list plus `gheim-rt-guard` appended. Editing a base without editing its twin
fails here, in the ~15 s `fleet-validate` job, instead of in use.

The guard list rule also encodes two decisions that were measured, not assumed:

⛔ `gheim-pii-guard` must never appear on a model entry. It and `gheim-rt-guard` mask the
same spans to DIFFERENT sentinels, so what reaches the upstream depends on which ran last,
and the mask-only sibling destroys the round trip. No entry may name it.

⭐ A twin MAY be a KEY in `litellm_settings.fallbacks`, but every target it names must
ITSELF be a twin. A base's tail exists for availability; on a twin the danger is not the
tail but the DESTINATION — a non-twin target carries no round trip, so the request would
continue to the upstream with the sentinels still sealed in the text. The earlier rule
("a twin has no tail at all") was too strong: it turned `work-gheim`, whose identity is
"Claude, then DeepSeek flash", into a hard failure at the Max cap. Superseded 2026-10-08
by the user's option (ii); the assertion below states the real invariant.

⚠️ `customer-data-guard` (the customer-name denylist floor) is intentionally KEPT on the
cloud twins: the twin ADDS the round trip rather than replacing the floor. Measured
2026-10-08, in both orders, that the floor leaves a `<PERSON_1>.<blob>` sentinel untouched
and the value still restores. Do not "simplify" a twin to the round-trip guard alone.
"""

import pathlib
import unittest

import yaml

CONFIG = pathlib.Path(__file__).resolve().parent.parent / "litellm" / "litellm-cm.yml"
SUFFIX = "-gheim"
ROUND_TRIP = "gheim-rt-guard"
MASK_ONLY = "gheim-pii-guard"


def config() -> dict:
    doc = yaml.safe_load(CONFIG.read_text())
    parsed = yaml.safe_load(doc["data"]["config.yaml"])
    return parsed


class GheimTwinTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config()
        self.entries = self.cfg["model_list"]
        self.by_name = {}
        for entry in self.entries:
            name = entry["model_name"]
            self.assertNotIn(
                name, self.by_name,
                f"{name!r} is defined twice in model_list -- litellm would serve "
                f"whichever it read last",
            )
            self.by_name[name] = entry

    def twin_names(self):
        return [n for n in self.by_name if n.endswith(SUFFIX)]

    def test_every_twin_is_its_base_plus_one_guardrail(self):
        twins = self.twin_names()
        # ⛔ Empty selection is not a pass.
        self.assertGreater(
            len(twins), 0,
            "no '-gheim' twin found in model_list at all -- discovery is broken "
            "(renamed suffix? config.yaml moved?)",
        )
        violations = []
        for name in sorted(twins):
            base_name = name[: -len(SUFFIX)]
            base = self.by_name.get(base_name)
            if base is None:
                violations.append(
                    f"{name}: no base entry {base_name!r}. A twin is a copy of an "
                    f"existing group; a twin without a base is a group nobody meant to add."
                )
                continue

            twin_params = dict(self.by_name[name]["litellm_params"])
            base_params = dict(base["litellm_params"])
            got = twin_params.pop("guardrails", None)
            want = list(base_params.pop("guardrails", []) or []) + [ROUND_TRIP]

            for key in sorted(set(twin_params) | set(base_params)):
                if twin_params.get(key) != base_params.get(key):
                    violations.append(
                        f"{name}: '{key}' is {twin_params.get(key)!r} but its base "
                        f"{base_name!r} has {base_params.get(key)!r}. A twin differs from "
                        f"its base by the guardrail alone -- re-sync it."
                    )
            if got != want:
                violations.append(
                    f"{name}: guardrails is {got!r}, expected {want!r} "
                    f"(= its base's list plus {ROUND_TRIP!r})."
                )

            twin_rest = {k: v for k, v in self.by_name[name].items()
                         if k not in ("model_name", "litellm_params", "model_info")}
            base_rest = {k: v for k, v in base.items()
                         if k not in ("model_name", "litellm_params", "model_info")}
            if twin_rest != base_rest:
                violations.append(
                    f"{name}: the entry's non-litellm_params keys differ from its base "
                    f"({sorted(twin_rest)} vs {sorted(base_rest)})."
                )

            # `model_info` is the ONE key a twin may drop, and only in the
            # native-passthrough case below. If it keeps one, it must be the base's.
            twin_mi = self.by_name[name].get("model_info")
            base_mi = base.get("model_info")
            if twin_mi is not None and twin_mi != base_mi:
                violations.append(
                    f"{name}: model_info differs from its base {base_name!r} "
                    f"({twin_mi!r} vs {base_mi!r})."
                )

        self.assertEqual([], violations, "\n" + "\n".join(violations))

    def test_no_twin_advertises_a_guardrail_it_cannot_apply(self):
        """`/v1/messages` in supported_endpoints = native untranslated passthrough: the
        raw Anthropic body is forwarded to {api_base}/v1/messages, which resolves no
        deployment, so NO guardrail runs. On a twin that is the worst case -- the door
        is named for a round trip that silently never happens. lint.py fails the build
        on the combination; this asserts it again on the twins, where the name makes
        the lie specific."""
        twins = self.twin_names()
        # ⛔ Empty selection is not a pass.
        self.assertGreater(len(twins), 0, "no '-gheim' twin found at all")
        violations = []
        for name in sorted(twins):
            endpoints = (self.by_name[name].get("model_info") or {}).get(
                "supported_endpoints") or []
            if "/v1/messages" in endpoints:
                violations.append(
                    f"{name} declares supported_endpoints {endpoints!r}. The native "
                    f"/v1/messages passthrough applies NO guardrail, so this twin would "
                    f"advertise a round trip that never runs. It must serve a translated "
                    f"path instead: drop the endpoint."
                )
        self.assertEqual([], violations, "\n" + "\n".join(violations))

    def test_the_mask_only_sibling_is_on_no_model(self):
        offenders = [
            entry["model_name"] for entry in self.entries
            if MASK_ONLY in (entry["litellm_params"].get("guardrails") or [])
        ]
        # The population is every model entry, so this one cannot be vacuous.
        self.assertGreater(len(self.entries), 0, "model_list is empty")
        self.assertEqual(
            [], offenders,
            f"{MASK_ONLY} is attached to {offenders}. It masks the same spans as "
            f"{ROUND_TRIP} to different sentinels and destroys the round trip. "
            f"No model entry may name it.",
        )

    def test_every_twin_tail_target_is_itself_a_twin(self):
        """A twin may have a tail; it may not have an UNGUARDED one.

        The invariant is about the TARGET, not the presence of a row: whatever a twin
        falls over to must itself carry the round trip, or the request reaches the
        upstream with the sentinels still sealed in it — the exact leak the door exists
        to prevent, arriving silently in the middle of a request that had already
        succeeded at masking."""
        fallbacks = self.cfg["litellm_settings"].get("fallbacks") or []
        rows = [r for r in fallbacks if isinstance(r, dict)]
        # ⛔ Empty selection is not a pass: rows must still exist to inspect, otherwise a
        # renamed key would make this assertion check nothing.
        self.assertGreater(len(rows), 0, "no fallbacks rows found at all")

        twins = set(self.twin_names())
        violations = []
        for row in rows:
            for key, targets in row.items():
                if not key.endswith(SUFFIX):
                    continue
                for target in targets or []:
                    if target not in twins:
                        violations.append(
                            f"{key}: tail target {target!r} is not a '-gheim' twin. A "
                            f"twin's chain must stay on guarded groups end to end, or the "
                            f"request continues upstream with the sentinels still in the "
                            f"text -- a leak that no error surfaces."
                        )
        self.assertEqual([], violations, "\n" + "\n".join(violations))

        # ⛔ Non-vacuity, stated as a fact about the feature rather than the population:
        # the guarded tail is deliberate, so assert the door it was built for actually
        # carries one. A row dropped in a later edit fails HERE, not at the Max cap.
        twin_keys = [k for row in rows for k in row if k.endswith(SUFFIX)]
        self.assertIn(
            "work-gheim", twin_keys,
            f"no `work-gheim` fallback row (twins with a chain: {sorted(twin_keys)}). The "
            f"guarded tail is missing, so exhausting the Max quota becomes a hard failure "
            f"instead of landing on `cn-deepseek-flash-gheim`.",
        )


if __name__ == "__main__":
    unittest.main()
