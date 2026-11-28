"""Each edge-dast scanner must report on EVERY target in its list, or fail.

The incident this encodes (2026-11-10, infra-7rog.31.6): the first run over the discovered
list (22 hosts) produced ONE nuclei report and exited 0. The scanner loop is
`while read host; do nuclei ...; done < /targets/hosts`, and nuclei reads its standard input
(it accepts targets there), so the first call swallowed every remaining line. With the old
one-host list it never showed. The guard "a missing report exits 1" did not fire, because
the loop never saw the swallowed hosts.

So the container scripts are run here for real, under sh, against fake scanners that read
their stdin dry, the way nuclei does:
  * every scanner call is cut off from the loop's stdin, so every target gets a report;
  * a run that reports on fewer targets than its list holds exits 1, whatever the cause.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from tests.fleetlib import repo_root

POD = yaml.safe_load((Path(repo_root()) / "dast" / "cronjob.yml").read_text())[
    "spec"]["jobTemplate"]["spec"]["template"]["spec"]
SCRIPTS = {c["name"]: c["args"][0] for c in POD["containers"]}
LISTS = {"zap": "hosts", "nuclei": "hosts", "nuclei-net": "endpoints", "testssl": "starttls"}

# Fakes: drain stdin like nuclei, then write the report file the script expects.
FAKE_NUCLEI = """#!/bin/sh
cat > /dev/null
out=""
while [ $# -gt 0 ]; do
  [ "$1" = "-o" ] && out="$2"
  [ "$1" = "-u" ] && echo "target $2"
  shift
done
[ -n "$out" ] && : > "$out"
exit 0
"""
FAKE_ZAP = """#!/bin/sh
cat > /dev/null
while [ $# -gt 0 ]; do [ "$1" = "-x" ] && out="$2"; shift; done
echo '<OWASPZAPReport/>' > "$WRK/$out"
exit 0
"""
FAKE_TESTSSL = """#!/bin/sh
cat > /dev/null
echo "argv $*" >> "$WRK/argv"
while [ $# -gt 0 ]; do [ "$1" = "--csvfile" ] && out="$2"; shift; done
[ -n "${NO_CSV:-}" ] || printf '"id","fqdn/ip","port","severity","finding","cve","cwe"\\n' > "$out"
exit "${EXIT_RC:-0}"
"""
TARGETS = {"starttls": "y.ns.svc smtp 198.51.100.4:25 mail.example\ny.ns.svc imap 192.0.2.2:143 -\n",
           "hosts": "a.example\nb.example\nc.example\n",
           "endpoints": "x.ns.svc 192.0.2.1:22\ny.ns.svc 192.0.2.2:993,198.51.100.4:25\nz.ns.svc 192.0.2.3:53\n"}


def run(name: str, script: str, env: dict | None = None, extra: dict | None = None) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "bin").mkdir()
        (root / "wrk").mkdir()
        (root / "home").mkdir()
        (root / "targets").mkdir()
        fakes = {("nuclei", FAKE_NUCLEI), ("zap-baseline.py", FAKE_ZAP), ("testssl.sh", FAKE_TESTSSL)}
        for tool, body in fakes | set((extra or {}).items()):
            p = root / "bin" / tool
            p.write_text(body)
            p.chmod(p.stat().st_mode | stat.S_IEXEC)
        for f, text in TARGETS.items():
            (root / "targets" / f).write_text(text)
        script = script.replace("/targets/", f"{root}/targets/").replace("/zap/wrk", str(root / "wrk"))
        env = {**os.environ, "PATH": f"{root}/bin:/usr/bin:/bin", "HOME": str(root / "home"),
               "WRK": str(root / "wrk"), **(env or {})}
        # stdin is an open pipe on purpose: a scanner that is not cut off from it must not
        # find the script's own input either.
        p = subprocess.run(["sh", "-c", script], input="", capture_output=True, text=True,
                           env=env, timeout=60)
        argv = root / "wrk" / "argv"
        return p.returncode, p.stdout + (argv.read_text() if argv.exists() else "")


class EveryTargetIsReported(unittest.TestCase):
    def test_each_scanner_reports_every_target_of_its_list(self):
        for name, script in SCRIPTS.items():
            with self.subTest(name):
                rc, out = run(name, script)
                want = len(TARGETS[LISTS[name]].splitlines())
                self.assertEqual(out.count(f"-----BEGIN DAST REPORT {name} "), want, out[-400:])
                self.assertEqual(rc, 0, out[-400:])

    def test_nuclei_net_hands_every_address_of_a_line_to_the_scanner(self):
        # infra-7rog.31.9: a line is `<id> <addr:port>,...` -- one Service can be scanned
        # at its public front (router DNAT/haproxy) and at its LAN-only ports at once.
        _, out = run("nuclei-net", SCRIPTS["nuclei-net"])
        self.assertEqual(sorted(line for line in out.splitlines() if line.startswith("target ")),
                         ["target 192.0.2.1:22", "target 192.0.2.2:993", "target 192.0.2.3:53",
                          "target 198.51.100.4:25"])

    def test_testssl_negotiates_starttls_at_the_address_with_the_server_name(self):
        _, out = run("testssl", SCRIPTS["testssl"])
        argv = sorted(line for line in out.splitlines() if line.startswith("argv "))
        self.assertEqual(len(argv), 2, out[-400:])
        self.assertIn("--starttls imap", argv[0])
        self.assertTrue(argv[0].endswith(" 192.0.2.2:143"), argv[0])
        self.assertIn("--starttls smtp", argv[1])
        self.assertIn("--ip=198.51.100.4", argv[1])
        self.assertTrue(argv[1].endswith(" mail.example:25"), argv[1])

    def test_a_listener_with_no_starttls_is_reported_not_failed(self):
        # testssl exits 246 with a FATAL scanProblem row when the port never offers STARTTLS
        # (mail:2525, measured 2026-11-23): that is the finding, and its CSV must still ship.
        rc, out = run("testssl", SCRIPTS["testssl"], env={"EXIT_RC": "246"})
        self.assertEqual(out.count("-----BEGIN DAST REPORT testssl "), 2, out[-400:])
        self.assertEqual(rc, 0, out[-400:])

    def test_a_scanner_error_without_a_csv_is_a_failed_run(self):
        rc, out = run("testssl", SCRIPTS["testssl"], env={"EXIT_RC": "249", "NO_CSV": "1"})
        self.assertIn("failed (rc=249)", out)
        self.assertEqual(rc, 1, out[-400:])

    def test_testssl_falls_back_to_busybox_when_the_image_has_no_gzip(self):
        # The testssl.sh image ships no gzip (measured 2026-11-23: every report body was
        # empty); busybox does, so a failing gzip must not empty the report.
        rc, out = run("testssl", SCRIPTS["testssl"],
                      extra={"gzip": "#!/bin/sh\nexit 1\n",
                             "busybox": "#!/bin/sh\nshift\necho COMPRESSED-BY-BUSYBOX\n"})
        import base64
        bodies = [line for line in out.splitlines() if line.startswith("Q09N")]  # "COMPRESSED-"
        self.assertEqual(len(bodies), 2, out[-400:])
        self.assertIn("COMPRESSED-BY-BUSYBOX", base64.b64decode(bodies[0]).decode())
        self.assertEqual(rc, 0, out[-400:])

    def test_a_short_run_fails_even_when_every_call_succeeded(self):
        # Re-create the incident: put the swallowing back and the run must fail, not pass.
        for name in ("zap", "nuclei", "nuclei-net", "testssl"):
            with self.subTest(name):
                swallowing = SCRIPTS[name].replace(" < /dev/null", "")
                self.assertNotEqual(swallowing, SCRIPTS[name], "no stdin cut-off to remove")
                rc, out = run(name, swallowing)
                self.assertEqual(rc, 1, out[-400:])
                self.assertIn("reports for", out)


if __name__ == "__main__":
    unittest.main()
