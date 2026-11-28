"""Mail in Chat: one token door — the picker — on 2.3 and on Dovecot 2.4 (infra-47nq.2.4, infra-hn5aw.5).

The v1 claim login (a bare <mailbox> XOAUTH2 login whose identity came from the
token's `mailbox` claim) is retired on both dialects:

  * no real client used it — in a month of prod mail logs every successful token
    login was the probe's own canary, and mail-mcp has been LOGIN_MODE=master
    since the picker went live (2026-10-28);
  * on Dovecot 2.4 it CANNOT coexist with the picker: 2.4 validates every token
    in the MECHANISM against one global oauth2 block (measured 2026-11-23), so
    there is no second username registry.

The 2.3 path keeps an env switch, OAUTH_CLAIM_LOGIN, default 0.
The 2.4 path is picker-only, with five rules each measured in a sandbox; the
full proof (incl. fail-closed with the owner passdb removed) is on infra-hn5aw.5
and the config is archived at ~/mail-dms17-picker-20261123/.
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


def block(begin: str, end: str) -> str:
    s = patches()
    return s[s.index(begin):s.index(end)]


class ClaimLoginIsRetired(unittest.TestCase):
    def test_the_switch_defaults_off(self) -> None:
        self.assertEqual(values()["deployment"]["env"]["OAUTH_CLAIM_LOGIN"], 0)

    def test_23_drops_the_v1_passdb_unless_the_switch_is_on(self) -> None:
        b = block("if [ \"$OAUTH_CLAIM_LOGIN\" != \"1\" ]; then", "if [ \"$MAIL_PICKER\" = \"1\" ]; then")
        self.assertIn("sed -i '/^passdb {/,/^}/d' /etc/dovecot/conf.d/auth-oauth2.conf.ext", b)
        # and a bare token login is refused cleanly instead of hitting "no passdb"
        self.assertIn("96-mdapi-token-deny.conf", b)
        self.assertIn("skip = authenticated", b)

    def test_no_door_at_all_closes_the_mechanisms(self) -> None:
        b = block("if [ \"$MAIL_PICKER\" != \"1\" ] && [ \"$OAUTH_CLAIM_LOGIN\" != \"1\" ]", "elif [ \"$DV24\" = 1 ]")
        self.assertIn("#!include auth-oauth2.conf.ext", b)


class Dovecot24PickerOnly(unittest.TestCase):
    def setUp(self) -> None:
        self.b = block("elif [ \"$DV24\" = 1 ] && [ \"$MAIL_PICKER\" = \"1\" ]", "# === SMTP AUTH through Dovecot")

    def test_door_needs_the_picker_and_the_secret(self) -> None:
        self.assertIn('[ "$MAIL_PICKER" = "1" ]', self.b)

    def test_one_global_username_registry(self) -> None:
        self.assertIn("oauth2_username_attribute = username", self.b)

    def test_master_marker_never_completes_a_login(self) -> None:
        self.assertIn("result_success = continue-fail", self.b)
        self.assertNotIn("result_success = continue-ok", self.b)

    def test_ownership_veto_always_runs_and_supplies_the_credential(self) -> None:
        owner = self.b[self.b.index("passdb owner {"):]
        self.assertIn("skip = never", owner)          # skip=unauthenticated silently skipped it
        self.assertIn("password = {PLAIN}%{passdb:token}", owner)  # nopassword alone => "NULL password"
        self.assertIn("result_failure = return-fail", owner)
        self.assertIn("(&(objectClass=room)(cn=%{user}))", owner)
        self.assertIn("uid=%{master_user}", owner)

    def test_readonly_acl_is_per_session(self) -> None:
        owner = self.b[self.b.index("passdb owner {"):]
        for want in ("userdb_mail_plugins/acl = yes", "userdb_acl_driver = vfile",
                     "userdb_acl_global_path = /etc/dovecot/oauth2-readonly.acl"):
            self.assertIn(want, owner)
        # a global acl_driver would hit password sessions too
        self.assertNotIn("acl_driver = vfile", patches().replace("userdb_acl_driver = vfile", ""))

    def test_password_passdb_stays_off_the_token_mechanisms(self) -> None:
        self.assertIn("printf 'passdb ldap {\\n  mechanisms_filter = plain login\\n}\\n'", self.b)

    def test_client_credentials_travel_in_the_introspection_url(self) -> None:
        self.assertIn("oauth2_introspection_url = https://dovecot:$SEC@", self.b)

    def test_fails_closed_when_the_chain_is_incomplete(self) -> None:
        tail = self.b[self.b.index("if grep -q '^oauth2_username_attribute = username$'"):]
        self.assertIn("#!include auth-oauth2.conf.ext", tail)
        self.assertIn("rm -f $A4 $PW", tail)


if __name__ == "__main__":
    unittest.main()
