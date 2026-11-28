"""Every edge response carries HSTS, nosniff and an anti-clickjacking header (infra-7rog.31.10).

The first edge-dast triage (2026-11-23) found three headers missing per host, 88
findings in all:

- HSTS on the oauth2-proxy sign-in 302 of every SSO-gated host. The controller
  emits HSTS from Lua (lua_ingress.header()), and the generated named location
  that answers `error_page 401` with `return 302 <auth-signin>` carries no
  `header_filter_by_lua_file`, so that 302 has none.
- X-Content-Type-Options and X-Frame-Options on every app that does not send
  them itself (static sites, tenant sites, most apps).

The fix is one http-level block in the controller's http-snippet: headers-more
`more_set_headers` applies to every location, named ones included, and a
location's own `more_set_headers` (a configuration-snippet) ADDS to it rather
than replacing it (measured on the controller image).

X-Frame-Options never overrides the app: a backend's own X-Frame-Options is
passed through, and a backend CSP carrying frame-ancestors suppresses it (the
CSP is the modern control; sending both would let the stricter XFO win).

Opt-out: an Ingress whose app must be framed by another origin sets
`set $edge_frame_guard "off";` in its configuration-snippet; the app's own
headers then pass through untouched. Keycloak (its login-status and 3p-cookies
iframes are framed by every client origin) and grafana-embed (framed by the Ceph
dashboard) opt out.
"""

import re
import unittest

import yaml

import fleetlib

HELM = "rke2-ingress-nginx-config/ingress-nginx-helmchartconfig.yml"
OPT_OUT = re.compile(r'^\s*set \$edge_frame_guard "off";\s*$', re.M)


def http_snippet() -> str:
    for d in fleetlib.load_docs(HELM):
        if d.get("kind") == "HelmChartConfig":
            return yaml.safe_load(d["spec"]["valuesContent"])["controller"]["config"]["http-snippet"]
    raise AssertionError("no HelmChartConfig in " + HELM)


def nginx_map(snippet: str, var: str) -> tuple[str, list[tuple[str, str]]]:
    """(source expression, [(pattern, value)]) of `map <source> $var { ... }`."""
    m = re.search(r"map\s+(\"[^\"]*\"|\S+)\s+\$" + re.escape(var) + r"\s*\{(.*?)\}", snippet, re.S)
    if not m:
        raise AssertionError(f"no map for ${var} in the http-snippet")
    entries = []
    for line in m.group(2).splitlines():
        line = line.strip()
        if line:
            em = re.fullmatch(r'("[^"]*"|\S+)\s+("[^"]*"|\S+);', line)
            assert em, f"unparsed map line: {line!r}"
            entries.append(tuple(t.strip('"') for t in em.groups()))
    return m.group(1).strip('"'), entries


def evaluate(entries: list[tuple[str, str]], value: str) -> str:
    """nginx map semantics for this shape: exact strings, then regexes in order, then default."""
    for pat, out in entries:
        if pat != "default" and not pat.startswith("~") and pat == value:
            return out
    for pat, out in entries:
        if pat.startswith("~"):
            flags = re.I if pat.startswith("~*") else 0
            rx = re.compile(pat.lstrip("~*").replace("(?<", "(?P<"), flags)
            hit = rx.search(value)
            if hit:
                return re.sub(r"\$(\w+)", lambda g: hit.group(g.group(1)), out)
    return dict(entries).get("default", "")


def xfo(guard: str, backend_xfo: str = "", backend_csp: str = "") -> str:
    source, entries = nginx_map(http_snippet(), "edge_xfo")
    assert source == "$edge_frame_guard|$upstream_http_x_frame_options|$upstream_http_content_security_policy", source
    return evaluate(entries, f"{guard}|{backend_xfo}|{backend_csp}")


def ingresses():
    for rel in fleetlib.tracked_yaml():
        for d in fleetlib.load_docs(rel):
            if d.get("kind") == "Ingress":
                yield rel, d


def backends(ing: dict) -> set[str]:
    return {
        ((p.get("backend") or {}).get("service") or {}).get("name")
        for r in (ing.get("spec") or {}).get("rules") or []
        for p in ((r.get("http") or {}).get("paths") or [])
    }


def hosts(ing: dict) -> set[str]:
    return {r.get("host", "") for r in (ing.get("spec") or {}).get("rules") or []}


def snippet_of(ing: dict) -> str:
    ann = (ing.get("metadata") or {}).get("annotations") or {}
    return ann.get("nginx.ingress.kubernetes.io/configuration-snippet", "")


class EdgeSecurityHeaders(unittest.TestCase):
    def test_the_three_headers_are_set_at_http_level(self):
        s = http_snippet()
        self.assertIn('more_set_headers "Strict-Transport-Security: $edge_hsts";', s)
        self.assertIn('more_set_headers "X-Content-Type-Options: nosniff";', s)
        self.assertIn('more_set_headers "X-Frame-Options: $edge_xfo";', s)

    def test_hsts_only_over_https_and_matches_the_lua_value(self):
        source, entries = nginx_map(http_snippet(), "edge_hsts")
        self.assertEqual("$https", source)
        self.assertEqual("max-age=31536000; includeSubDomains", evaluate(entries, "on"))
        self.assertEqual("", evaluate(entries, ""))

    def test_frame_guard_defaults_on(self):
        source, entries = nginx_map(http_snippet(), "edge_frame_guard")
        self.assertEqual("on", evaluate(entries, "any.host"))

    def test_xfo_defaults_to_sameorigin(self):
        self.assertEqual("SAMEORIGIN", xfo("on"))
        self.assertEqual("SAMEORIGIN", xfo("on", backend_csp="default-src 'self'"))

    def test_backend_xfo_and_frame_ancestors_win(self):
        self.assertEqual("DENY", xfo("on", backend_xfo="DENY"))
        self.assertEqual("sameorigin", xfo("on", backend_xfo="sameorigin", backend_csp="frame-ancestors 'none'"))
        self.assertEqual("", xfo("on", backend_csp="default-src 'self'; frame-ancestors https://a"))

    def test_opt_out_passes_the_backend_through(self):
        self.assertEqual("", xfo("off"))
        self.assertEqual("DENY", xfo("off", backend_xfo="DENY"))
        self.assertEqual("", xfo("off", backend_csp="frame-ancestors 'self'"))

    def test_framed_apps_opt_out(self):
        framed = [(rel, d) for rel, d in ingresses()
                  if "keycloak" in backends(d) or any(h.startswith("grafana-embed.") for h in hosts(d))]
        self.assertGreaterEqual(len(framed), 3, [rel for rel, _ in framed])
        for rel, d in framed:
            self.assertRegex(snippet_of(d), OPT_OUT, rel)


if __name__ == "__main__":
    unittest.main()
