"""Every Ingress host, wildcards included, lands in exactly one WAF engine-state class.

Incident (infra-az8.52, found by infra-lcrt 2026-11-01): the named list of the
DetectionOnly catch-all, rule 1914 in rke2-ingress-nginx-config/modsec-global-cm.yml,
spelled the wildcard Ingress hosts as escaped literals, `\\*\\.mdapi\\.org` and
`\\*\\.pages\\.mdapi\\.ch`. ModSecurity sees the real Host header (`foo.mdapi.org`), which
never equals `*.mdapi.org`, so every real name fell through to the catch-all and ran
DetectionOnly while the file's ENFORCING comment listed `*.mdapi.org` as enforced. For
the GitLab Pages names it was worse in a different way: 1901 sets the engine Off, then
1914 (later in the file, and `ctl:` is last-writer-wins) set it back to DetectionOnly.

Six more live hosts were in no list at all (agentgateway, backstage, defectdojo, kagent,
plex-auth, rum) and silently ran DetectionOnly. An unmatched Host can only ever WEAKEN
enforcement, so no false-positive audit surfaces this class; only a test can.

How the rules combine (phase 1, in file order, the last `ctl:ruleEngine` wins):
  1900 / 1901  Off for the hosts they name
  1913         DetectionOnly for the hosts it names
  1914         DetectionOnly for every host its negated list does NOT name
So a host's state is decided only if 1914's list names it (otherwise 1914 overrides
everything), and then by at most one of 1900, 1901 and 1913 (none of them = On).

To add an Ingress: name its host in 1914's list, and also in 1900 (Off) or 1913
(DetectionOnly, with a reason in test_modsec_detection_only_hosts_are_sanctioned.py) if it
must not be enforced. A wildcard host is written as a label regex, `[^.]+\\.example\\.org`.
"""

import re
import unittest

import fleetlib

ANNOT_MODSEC = "nginx.ingress.kubernetes.io/enable-modsecurity"

# Ingress hosts rendered by a Helm chart, so no `kind: Ingress` document in this repo
# names them. Keep the source next to each one.
CHART_RENDERED_HOSTS = {
    "*.pages.mdapi.ch": "GitLab Pages (gitlab chart, bootstrap/gitlab-gitlab-pages)",
    "defectdojo.mdapi.ch": "defectdojo/fleet.yaml host: (DefectDojo chart)",
}

# The one host that is MEANT to be the catch-all: the *.mdapi.ch 404 Ingress catches every
# name nothing else serves (infra-az8.19.5), and those requests belong in 1914 by design.
CATCH_ALL_BY_DESIGN = {"*.mdapi.ch"}

ENGINE_RULE = re.compile(
    r'SecRule\s+REQUEST_HEADERS:Host\s+"(!?)@rx\s+([^"]+)"\s*\\\s*\n'
    r'\s*"id:(19\d\d),[^"]*?ctl:ruleEngine=(Off|DetectionOnly)"')

STATE_RULES = {"1900": "Off", "1901": "Off", "1913": "DetectionOnly"}


def engine_rules(conf: str) -> dict[str, re.Pattern]:
    rules = {rid: re.compile(arg) for _neg, arg, rid, _state in ENGINE_RULE.findall(conf)}
    missing = sorted((set(STATE_RULES) | {"1914"}) - rules.keys())
    if missing:
        raise AssertionError(
            f"measured NOTHING: engine-state rule(s) {missing} did not parse out of "
            f"{fleetlib.MODSEC_CONFIGMAP}; their shape changed, update ENGINE_RULE.")
    return rules


def concrete_names(host: str) -> list[str]:
    """What ModSecurity actually sees for this Ingress host, with and without a port.
    A wildcard stands for any one label (Kubernetes Ingress semantics)."""
    name = host.replace("*", "wildcard-probe", 1) if host.startswith("*.") else host
    return [name, f"{name}:443"]


def engine_state(rules: dict[str, re.Pattern], name: str) -> list[str]:
    """Every class that claims `name`; exactly one is the only healthy answer."""
    if not rules["1914"].match(name):
        return ["DetectionOnly (1914 catch-all)"]
    claims = [f"{STATE_RULES[rid]} ({rid})" for rid in STATE_RULES if rules[rid].match(name)]
    return claims or ["On"]


def waf_ingress_hosts() -> set[str]:
    """Hosts of every Ingress the WAF runs on (an Ingress with enable-modsecurity "false"
    gets no `modsecurity on;` from nginx, so its engine state does not exist)."""
    hosts = set(CHART_RENDERED_HOSTS)
    for rel in fleetlib.tracked_yaml():
        if fleetlib.is_vendored(rel):
            continue
        for doc in fleetlib.load_docs(rel):
            if not isinstance(doc, dict) or doc.get("kind") != "Ingress":
                continue
            annotations = (doc.get("metadata") or {}).get("annotations") or {}
            if str(annotations.get(ANNOT_MODSEC, "")).lower() == "false":
                continue
            for rule in (doc.get("spec") or {}).get("rules") or []:
                if rule.get("host"):
                    hosts.add(rule["host"])
    return hosts


class EveryIngressHostHasOneEngineStateTest(unittest.TestCase):
    def setUp(self):
        self.rules = engine_rules(fleetlib.modsec_global_conf())

    def test_every_ingress_host_is_decided(self):
        hosts = waf_ingress_hosts()
        self.assertGreaterEqual(
            len(hosts), 50, f"measured NOTHING: only {len(hosts)} Ingress hosts found")

        problems = []
        for host in sorted(hosts - CATCH_ALL_BY_DESIGN):
            for name in concrete_names(host):
                states = engine_state(self.rules, name)
                if len(states) != 1 or states[0].endswith("catch-all)"):
                    problems.append(f"{host} (as {name!r}) -> {states}")
        self.assertEqual(
            problems, [],
            "Ingress hosts whose WAF engine state nobody decided (or decided twice). Name "
            "each in rule 1914's list, and in 1900/1913 only if it must not be enforced; "
            "write a wildcard as a label regex [^.]+\\.domain:\n  " + "\n  ".join(problems))

    def test_the_catch_all_ingress_stays_in_the_catch_all(self):
        for host in CATCH_ALL_BY_DESIGN:
            for name in concrete_names(host):
                self.assertEqual(engine_state(self.rules, name),
                                 ["DetectionOnly (1914 catch-all)"], name)

    def test_no_engine_list_names_a_literal_wildcard(self):
        conf = fleetlib.modsec_global_conf()
        literals = sorted(rid for _n, arg, rid, _s in ENGINE_RULE.findall(conf)
                          if "\\*" in arg)
        self.assertEqual(
            literals, [],
            "an engine-state list contains an escaped '*': no Host header ever equals "
            "'*.domain', so the entry matches nothing. Use a label regex [^.]+\\.domain.")

    def test_a_named_list_does_not_claim_a_neighbour(self):
        # The label regex must stay one label wide and anchored: these are NOT ours.
        for name in ("mdapi.org.evil.example", "a.b.pages.mdapi.ch", "xmdapi.org"):
            self.assertFalse(self.rules["1901"].match(name), name)
        self.assertFalse(self.rules["1914"].match("mdapi.org.evil.example"))


if __name__ == "__main__":
    unittest.main()
