"""SMTP AUTH: a person needs a mailbox; apps get SMTP-only sender logins (infra-o4i3m.1).

Postfix used to authenticate through saslauthd `rimap`, i.e. an IMAP login, so only a
person WITH a mailbox (an LDAP `room`) could relay. Apps had no login at all and relayed
through `mynetworks`, which trusted every pod. Step 2 gives each app its own sender login
in a Dovecot passwd-file, which means Postfix must ask Dovecot directly. Measured
2026-11-13: 24 of 45 LDAP `mail` values have no room (SSO-only accounts). A plain switch
to Dovecot SASL would have let all of them relay, so the LDAP passdb continues into a
room check that fails the login when there is no room.

Pinned below:
  * the room check exists, follows `continue-ok`, and fails closed;
  * SMTP offers PLAIN/LOGIN only. Dovecot also offers XOAUTH2/OAUTHBEARER, whose
    `dovecot-mail` tokens are read-only IMAP tokens for mail-mcp and must not submit;
  * the mail-picker guard names its own passdb: it used to count
    `skip = unauthenticated` == 1, and the room check adds a second, which would
    silently switch the picker off at the next restart;
  * the sender passwd-file comes from Akeyless via the ExternalSecret.
"""

from __future__ import annotations

import unittest

import yaml

from tests.fleetlib import repo_root

VALUES = repo_root() / "mail" / "docker-mailserver" / "values.yml"


def values() -> dict:
    return yaml.safe_load(VALUES.read_text())


def patches() -> str:
    return values()["configMaps"]["user-patches.sh"]["data"]


class SmtpAuthRequiresAMailboxOrASenderLogin(unittest.TestCase):
    def test_saslauthd_is_off(self):
        self.assertEqual(values()["deployment"]["env"]["ENABLE_SASLAUTHD"], 0)

    def test_postfix_asks_dovecot(self):
        p = patches()
        self.assertIn("smtpd_sasl_type = dovecot", p)
        self.assertIn("smtpd_sasl_path = /dev/shm/sasl-auth.sock", p)

    def test_smtp_offers_plain_and_login_only(self):
        self.assertIn("smtpd_sasl_mechanism_filter = plain, login", patches())

    def test_ldap_login_continues_into_a_room_check_that_fails_closed(self):
        p = patches()
        self.assertIn("result_success = continue-ok", p)
        self.assertIn("pass_filter = (&(objectClass=room)(cn=%u)(ou:dn:=People))", p)
        room = p[p.index("args = /etc/dovecot/dovecot-ldap-room.conf.ext") - 200:]
        for line in ("skip = unauthenticated", "result_failure = return-fail",
                     "result_internalfail = return-fail"):
            self.assertIn(line, room[:600])

    def test_incomplete_chain_disables_smtp_auth(self):
        self.assertIn("SMTP AUTH DISABLED", patches())

    def test_picker_guard_names_its_own_passdb(self):
        self.assertNotIn("grep -c 'skip = unauthenticated')\" = 1", patches())
        self.assertIn("dovecot-ldap-owner.conf.ext", patches())

    def test_sender_logins_come_from_akeyless(self):
        es = next(d for d in values()["extraDeploy"] if d["kind"] == "ExternalSecret")
        keys = {d["secretKey"]: d["remoteRef"]["key"] for d in es["spec"]["data"]}
        self.assertEqual(keys.get("SMTP_SENDERS_PASSWD"), "/mdapi/mail/smtp-senders/passwd")


if __name__ == "__main__":
    unittest.main()
