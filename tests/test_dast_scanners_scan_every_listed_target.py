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
LISTS = {"zap": "hosts", "nuclei": "hosts", "nuclei-net": "endpoints"}

# Fakes: drain stdin like nuclei, then write the report file the script expects.
FAKE_NUCLEI = """#!/bin/sh
cat > /dev/null
out=""
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done
[ -n "$out" ] && : > "$out"
exit 0
"""
FAKE_ZAP = """#!/bin/sh
cat > /dev/null
while [ $# -gt 0 ]; do [ "$1" = "-x" ] && out="$2"; shift; done
echo '<OWASPZAPReport/>' > "$WRK/$out"
exit 0
"""
TARGETS = {"hosts": "a.example\nb.example\nc.example\n",
           "endpoints": "x.ns.svc 192.0.2.1 22\ny.ns.svc 192.0.2.2 25,993\nz.ns.svc 192.0.2.3 53\n"}


def run(name: str, script: str) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "bin").mkdir()
        (root / "wrk").mkdir()
        (root / "home").mkdir()
        (root / "targets").mkdir()
        for tool, body in (("nuclei", FAKE_NUCLEI), ("zap-baseline.py", FAKE_ZAP)):
            p = root / "bin" / tool
            p.write_text(body)
            p.chmod(p.stat().st_mode | stat.S_IEXEC)
        for f, text in TARGETS.items():
            (root / "targets" / f).write_text(text)
        script = script.replace("/targets/", f"{root}/targets/").replace("/zap/wrk", str(root / "wrk"))
        env = {**os.environ, "PATH": f"{root}/bin:/usr/bin:/bin", "HOME": str(root / "home"),
               "WRK": str(root / "wrk")}
        # stdin is an open pipe on purpose: a scanner that is not cut off from it must not
        # find the script's own input either.
        p = subprocess.run(["sh", "-c", script], input="", capture_output=True, text=True,
                           env=env, timeout=60)
        return p.returncode, p.stdout


class EveryTargetIsReported(unittest.TestCase):
    def test_each_scanner_reports_every_target_of_its_list(self):
        for name, script in SCRIPTS.items():
            with self.subTest(name):
                rc, out = run(name, script)
                want = len(TARGETS[LISTS[name]].splitlines())
                self.assertEqual(out.count(f"-----BEGIN DAST REPORT {name} "), want, out[-400:])
                self.assertEqual(rc, 0, out[-400:])

    def test_a_short_run_fails_even_when_every_call_succeeded(self):
        # Re-create the incident: put the swallowing back and the run must fail, not pass.
        for name in ("zap", "nuclei", "nuclei-net"):
            with self.subTest(name):
                swallowing = SCRIPTS[name].replace(" < /dev/null", "")
                self.assertNotEqual(swallowing, SCRIPTS[name], "no stdin cut-off to remove")
                rc, out = run(name, swallowing)
                self.assertEqual(rc, 1, out[-400:])
                self.assertIn("reports for", out)


if __name__ == "__main__":
    unittest.main()
