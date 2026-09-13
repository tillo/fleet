"""Every alerting rule must carry a human-readable summary or description.

A page whose text does not say what happened costs a diagnosis round-trip at 03:00. The
standing rule is that a notification carries content plus a link, and that its text VARIES
with the cause -- a rule with no annotation cannot satisfy either.

This guards a convention that currently holds across all 488 alerting rules, which is
exactly when it is cheap to lock in: the next rule added without a summary fails here
rather than being discovered on the night it fires.

Recording rules (`record:`) are deliberately not covered -- they never notify anyone.
"""

import unittest

import fleetlib


def alert_rules():
    """(file, group, alert_name, rule) for every alerting rule in the repo."""
    for rel in fleetlib.tracked_yaml():
        for doc in fleetlib.load_docs(rel):
            groups = ((doc.get("spec") or {}).get("groups")
                      or (doc.get("groups") if isinstance(doc.get("groups"), list) else None)
                      or [])
            for group in groups:
                if not isinstance(group, dict):
                    continue
                for rule in group.get("rules") or []:
                    if isinstance(rule, dict) and rule.get("alert"):
                        yield rel, group.get("name", "<unnamed>"), rule["alert"], rule


class AlertAnnotationTest(unittest.TestCase):
    def test_every_alert_has_a_summary_or_description(self):
        seen = 0
        bad = []
        for rel, group, name, rule in alert_rules():
            seen += 1
            ann = rule.get("annotations") or {}
            summary = str(ann.get("summary") or "").strip()
            description = str(ann.get("description") or "").strip()
            if not summary and not description:
                bad.append(f"{rel}: alert {name!r} in group {group!r} has neither "
                           f"annotations.summary nor annotations.description")

        # ⛔ Empty selection is not a pass. If the walker stops finding rules -- a schema
        # change, a move to a different CRD shape -- this test would go green while
        # checking nothing at all.
        self.assertGreater(
            seen, 0, "no alerting rules found at all -- the rule walker is broken")
        self.assertEqual([], bad, "\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
