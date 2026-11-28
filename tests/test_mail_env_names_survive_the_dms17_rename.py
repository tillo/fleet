"""The mail pod's env carries both the DMS <=16 and the DMS 17 names (chart 7.0).

Chart 7.0.0 targets docker-mailserver 17, which renamed two env families, and the
image ignores the old names:

  * ENABLE_RSPAMD_REDIS -> ENABLE_RSPAMD_VALKEY. The chart dropped the old key
    from its defaults. The frozen pre-v16 base still reads ENABLE_RSPAMD_REDIS and
    only gets 1 by falling back to ENABLE_RSPAMD, so it is set here explicitly.
  * LDAP_QUERY_FILTER_<KIND> -> POSTFIX_<KINDS>_QUERY_FILTER. DMS 17 has no default
    filters, and a lookup table without one is DISABLED. An image unfreeze that
    finds only the old names leaves Postfix with no LDAP users, aliases or
    domains, so every inbound message is rejected.

Each pair renders from one YAML anchor, so the two names cannot drift apart.
"""

from __future__ import annotations

import unittest

import yaml

from tests.fleetlib import repo_root

VALUES = repo_root() / "mail" / "docker-mailserver" / "values.yml"

# DMS <=16 name -> DMS 17 name (CHANGELOG v17.0.0, chart 7.0.0 README).
RENAMED = {
    "ENABLE_RSPAMD_REDIS": "ENABLE_RSPAMD_VALKEY",
    "LDAP_QUERY_FILTER_USER": "POSTFIX_USERS_QUERY_FILTER",
    "LDAP_QUERY_FILTER_GROUP": "POSTFIX_GROUPS_QUERY_FILTER",
    "LDAP_QUERY_FILTER_ALIAS": "POSTFIX_ALIASES_QUERY_FILTER",
    "LDAP_QUERY_FILTER_DOMAIN": "POSTFIX_DOMAINS_QUERY_FILTER",
}


def env() -> dict:
    return yaml.safe_load(VALUES.read_text())["deployment"]["env"]


class EnvNamesSurviveTheDms17Rename(unittest.TestCase):
    def test_each_renamed_variable_is_set_under_both_names(self) -> None:
        e = env()
        for old, new in RENAMED.items():
            with self.subTest(old=old, new=new):
                self.assertIn(old, e, "the running frozen image reads this name")
                self.assertIn(new, e, "DMS 17 reads only this name")
                self.assertTrue(str(e[old]).strip(), f"{old} is empty")
                self.assertEqual(e[old], e[new], f"{old} and {new} disagree")

    def test_rspamd_keeps_its_bayes_store(self) -> None:
        self.assertEqual(str(env()["ENABLE_RSPAMD_REDIS"]), "1")


if __name__ == "__main__":
    unittest.main()
