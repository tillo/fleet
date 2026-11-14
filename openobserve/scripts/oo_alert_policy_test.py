#!/usr/bin/env python3
"""Tests for oo-alert-policy.py. The script name has a hyphen, so it cannot be
imported as a module by name; load it by path the same way as any extension-less
executable on this estate.

    cd ~/fleet && python3 -m unittest discover -s openobserve/scripts -p '*_test.py'
"""
import importlib.util
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location(
    "oo_alert_policy",
    pathlib.Path(__file__).with_name("oo-alert-policy.py"))
pol = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pol)


def rule(name="r", enabled=True, destinations=("pushover_mdapi",), silence=60,
         alert_id="id-1"):
    return {"alert_id": alert_id, "name": name, "enabled": enabled,
            "destinations": list(destinations),
            "trigger_condition": {"silence": silence, "frequency": 5,
                                  "frequency_type": "minutes"}}


class TargetSilence(unittest.TestCase):
    def test_raises_a_low_silence_to_the_target(self):
        self.assertEqual(pol.target_silence(30), pol.TARGET_SILENCE_MIN)

    def test_never_lowers_an_already_quiet_rule(self):
        self.assertEqual(pol.target_silence(1440), 1440)

    def test_treats_missing_as_zero(self):
        self.assertEqual(pol.target_silence(None), pol.TARGET_SILENCE_MIN)

    def test_is_idempotent(self):
        once = pol.target_silence(30)
        self.assertEqual(pol.target_silence(once), once)


class PushoverDetection(unittest.TestCase):
    def test_matches_the_managed_template(self):
        self.assertTrue(pol.is_pushover(rule()))

    def test_other_destinations_are_not_ours(self):
        self.assertFalse(pol.is_pushover(rule(destinations=("msteams_x",))))

    def test_no_destinations(self):
        self.assertFalse(pol.is_pushover(rule(destinations=())))


class Plan(unittest.TestCase):
    def test_flags_a_low_silence_pushover_rule(self):
        out = pol.plan([rule(silence=30)])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["was"], 30)
        self.assertEqual(out[0]["want"], pol.TARGET_SILENCE_MIN)

    def test_ignores_a_rule_already_at_target(self):
        self.assertEqual(pol.plan([rule(silence=120)]), [])

    def test_ignores_disabled_rules(self):
        self.assertEqual(pol.plan([rule(enabled=False, silence=0)]), [])

    def test_ignores_non_pushover_rules(self):
        self.assertEqual(pol.plan([rule(destinations=("slack",), silence=0)]), [])

    def test_carries_the_id_so_apply_can_put(self):
        out = pol.plan([rule(alert_id="abc", silence=5)])
        self.assertEqual(out[0]["id"], "abc")


class PriorityProblem(unittest.TestCase):
    def test_missing_priority_is_the_original_defect(self):
        prob = pol.priority_problem({"body": {}})
        self.assertIn("NO priority", prob)

    def test_audible_default_is_a_problem(self):
        self.assertIsNotNone(pol.priority_problem({"body": {"priority": 0}}))

    def test_quiet_target_passes(self):
        self.assertIsNone(pol.priority_problem({"body": {"priority": -1}}))

    def test_badge_only_is_quieter_so_it_passes(self):
        self.assertIsNone(pol.priority_problem({"body": {"priority": -2}}))


class SilenceOf(unittest.TestCase):
    def test_reads_the_trigger_condition(self):
        self.assertEqual(pol.silence_of(rule(silence=45)), 45)

    def test_missing_reads_as_zero(self):
        self.assertEqual(pol.silence_of({"trigger_condition": {}}), 0)

    def test_absent_trigger_condition_reads_as_zero(self):
        self.assertEqual(pol.silence_of({}), 0)


if __name__ == "__main__":
    unittest.main()
