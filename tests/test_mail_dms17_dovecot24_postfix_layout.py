"""The mail config works on both images: the frozen pre-v16 one and DMS 17 (infra-hn5aw).

Each fact below was measured in a sandbox pod on docker-mailserver 17.0.0 (Dovecot
2.4.1) against the real LDAP on 2026-11-23, and each one broke mail there:

  * Postfix LDAP tables moved from /etc/postfix/ldap-<kind>.cf to
    /etc/postfix/ldap/<kind>.cf. The perl edits that set our result attributes
    named the old files, so on DMS 17 they changed nothing and virtual_alias_maps
    pointed at files that do not exist. The block now finds the layout itself.
  * fts-xapian is gone from DMS 17; loading it is a Dovecot config FATAL, so the
    2.4 FTS payload must name flatcurve.
  * an EMPTY userdb mail_path overrides the global one in 2.4: every mailbox then
    fails with "Root mail directory not given", although logins still succeed.
  * the 2.3 SMTP AUTH passdbs read /etc/dovecot/dovecot-ldap.conf.ext, which 2.4
    does not have; the 2.4 chain is named passdb blocks, and the room check must
    still fail the login when there is no room.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from tests.fleetlib import repo_root

VALUES = repo_root() / "mail" / "docker-mailserver" / "values.yml"
BEGIN = "# === Postfix LDAP tables"
END_LINE = 'postconf "virtual_alias_maps = '

# stock DMS result attribute -> what our schema needs (users.cf is copied to usersmap.cf first)
EXPECT = {"domains": "cn", "aliases": "description", "groups": "description",
          "users": "description", "usersmap": "cn"}


def values() -> dict:
    return yaml.safe_load(VALUES.read_text())


def patches() -> str:
    return values()["configMaps"]["user-patches.sh"]["data"]


def postfix_block() -> str:
    s = patches()
    a = s.index(BEGIN)
    b = s.index("\n", s.index(END_LINE, a))
    return s[a:b]


def run_postfix_block(new_layout: bool) -> tuple[dict, str, str]:
    """Run the block against a fake /etc/postfix; return (attrs, postconf args, stderr)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "postfix"
        prefix = root / "ldap" if new_layout else root
        prefix.mkdir(parents=True)
        for kind in ("domains", "aliases", "groups", "users"):
            name = f"{kind}.cf" if new_layout else f"ldap-{kind}.cf"
            pad = "         " if (kind == "groups" and not new_layout) else ""
            (prefix / name).write_text(f"server_host = ldap://x\nresult_attribute {pad}= mail\n")
        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir()
        log = Path(tmp) / "postconf.log"
        (bin_dir / "postconf").write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {log}\n')
        (bin_dir / "postconf").chmod(0o755)
        r = subprocess.run(["bash", "-euo", "pipefail", "-c",
                            postfix_block().replace("/etc/postfix", str(root))],
                           env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
                           check=True, capture_output=True, text=True)
        attrs = {}
        for kind in EXPECT:
            f = prefix / (f"{kind}.cf" if new_layout else f"ldap-{kind}.cf")
            line = [l for l in f.read_text().splitlines() if l.startswith("result_attribute")]
            attrs[kind] = line[0].split("=", 1)[1].strip()
        return attrs, log.read_text().replace(str(root), "/etc/postfix"), r.stderr


class PostfixLdapTablesOnBothLayouts(unittest.TestCase):
    def check(self, new_layout: bool, base: str) -> None:
        attrs, postconf, stderr = run_postfix_block(new_layout)
        self.assertEqual(attrs, EXPECT)
        self.assertIn(f"virtual_alias_maps = ldap:{base}aliases.cf, ldap:{base}groups.cf, "
                      f"ldap:{base}usersmap.cf", postconf)
        self.assertEqual(stderr, "", "the block reports a table it could not set")

    def test_dms17_layout(self) -> None:
        self.check(True, "/etc/postfix/ldap/")

    def test_frozen_image_layout(self) -> None:
        self.check(False, "/etc/postfix/ldap-")

    def test_no_fleet_brace_vars(self) -> None:
        # Fleet substitutes the brace form of a shell variable in values.
        self.assertNotIn("${", postfix_block())


class Dovecot24Config(unittest.TestCase):
    def cm(self, key: str) -> str:
        return values()["configMaps"][key]["data"]

    def test_24_fts_is_flatcurve(self) -> None:
        conf = [l.strip() for l in self.cm("dovecot-fts-24.conf").splitlines()
                if l.strip() and not l.strip().startswith("#")]
        self.assertIn("fts_flatcurve = yes", conf)
        self.assertEqual([l for l in conf if "xapian" in l], [])

    def test_24_userdb_mail_path_is_not_empty(self) -> None:
        line = [l.strip() for l in self.cm("dovecot-ldap-24-override.conf").splitlines()
                if l.strip().startswith("mail_path")]
        self.assertEqual(line, ["mail_path = /var/mail/%{user | domain}/%{user | username}"])

    def test_24_room_check_fails_closed(self) -> None:
        p = patches()
        room = p[p.index("passdb room {"):p.index("passdb smtp-senders {")]
        for want in ("skip = unauthenticated", "result_success = return-ok",
                     "result_failure = return-fail", "result_internalfail = return-fail",
                     "(&(objectClass=room)(cn=%{user})(ou:dn:=People))"):
            self.assertIn(want, room)
        # SMTP AUTH turns on only when the effective config carries that room filter
        self.assertIn('[ "$ROOM_OK" = 1 ]', p)


if __name__ == "__main__":
    unittest.main()
