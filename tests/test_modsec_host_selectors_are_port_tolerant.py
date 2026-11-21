"""Every ModSecurity Host selector must tolerate an explicit `:port` in the Host header.

Incident (infra-ajq0, 2026-10-08; re-confirmed by /waf-audit 2026-10-11 and 2026-10-14):
ModSecurity's `REQUEST_HEADERS:Host` keeps the port -- `webmail.mdapi.ch:443` -- while
nginx strips it for `server_name` matching, so the SAME vhost serves the request. Every
engine-state list in `rke2-ingress-nginx-config/modsec-global-cm.yml` was anchored with a
bare `$`, so a port-suffixed Host matched NONE of them. Rule 1914 is a NEGATION
(`!@rx ^(...)$`), so the unmatched Host fell into its DetectionOnly catch-all: an
ENFORCED host silently dropped to DetectionOnly for any client that sends one extra
header. Measured A/B with an identical payload:

    curl 'https://webmail.mdapi.ch/probe?cmd=$(cat%20/etc/passwd)'                 -> 403
    curl -H 'Host: webmail.mdapi.ch:443' 'https://webmail.mdapi.ch/probe?cmd=...'  -> 404

The direction is monotonic (an unmatched Host can only ever WEAKEN enforcement), which is
why no false-positive audit surfaced it. The same hole applied to the per-application
carveouts 1920-1943: once the engine lists are fixed, a port-suffixed request to a
carveout host is enforced without its carveout and starts false-positiving.

So the invariant is: every `SecRule REQUEST_HEADERS:Host` selector carries the optional
port immediately before its anchor, and none uses `@streq` (which compares the exact
value and therefore cannot match it).

⚠️ `@streq` is the trap for new rules: it is correct for every request without a port and
silently wrong for one with it. It also looks tidier than an escaped regex, so it is the
form someone adds next.
"""

import re
import unittest

import fleetlib

# "<op> <arg>" on the SecRule line, then the rule id on the action line that follows.
HOST_RULE = re.compile(
    r'SecRule\s+REQUEST_HEADERS:Host\s+"(!?)(@rx|@streq)\s+([^"]+)"\s*\\\s*\n'
    r'\s*"id:(\d+),')

# This repo's spellings for "optional colon followed by digits". A suffix test, not a
# semantic one: the property being asserted is that this group sits immediately before $.
PORT_SUFFIXES = ("(?::[0-9]+)?$", r"(?::\d+)?$")


class ModsecHostSelectorPortTest(unittest.TestCase):
    def test_every_host_selector_tolerates_an_optional_port(self):
        conf = fleetlib.modsec_global_conf()
        rules = HOST_RULE.findall(conf)

        # ⛔ An empty selection is not a pass.
        self.assertGreaterEqual(
            len(rules), 10,
            f"measured NOTHING: only {len(rules)} REQUEST_HEADERS:Host selectors parsed "
            f"out of {fleetlib.MODSEC_CONFIGMAP}; the rule shape changed and this check no "
            f"longer sees the rule set."
        )
        ids = {int(rule[3]) for rule in rules}
        for structural in (1899, 1914):
            self.assertIn(
                structural, ids,
                f"engine-state rule id:{structural} is missing from the Host selectors; "
                f"the deny-IP-literal and DetectionOnly catch-all rules are structural."
            )

        violations = []
        for _neg, op, arg, rid in rules:
            if op == "@streq":
                violations.append(
                    f"id:{rid} uses @streq, which compares the exact header value and "
                    f"can never match a port-suffixed Host"
                )
            elif not arg.endswith(PORT_SUFFIXES):
                violations.append(
                    f"id:{rid} does not end with an optional-port group before the "
                    f"anchor: {arg[-40:]!r}"
                )

        self.assertEqual(
            violations, [],
            "REQUEST_HEADERS:Host selectors that cannot see the port (nginx strips it "
            "for routing, ModSecurity keeps it; an unmatched Host lands in 1914's "
            "DetectionOnly catch-all):\n  " + "\n  ".join(violations)
        )


if __name__ == "__main__":
    unittest.main()
