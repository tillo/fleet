#!/usr/bin/env python3
"""tools/vendor-gitlab-chart.py works from the copy the renovate job installs (infra-ik4p.12).

RENOVATE_ALLOWED_COMMANDS is global: it admits a command for every repo in
RENOVATE_REPOSITORIES. A repo-relative `python3 tools/vendor-gitlab-chart.py` would
therefore run whatever file of that name a listed repo ships, under the bot token. So
the job installs THIS repo's copy at /tmp/renovate-tools/ and the allow-list names only
that path. For that to work, the script has to take its repo root from the cwd (Renovate
runs a postUpgradeTask in the checkout) instead of from its own location. It also has to
refuse to run anywhere that is not a fleet checkout root.

Run for real against a minimal chart archive: the copy lives outside the checkout, just
as the installed one does.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

import fleetlib

SCRIPT = fleetlib.repo_root() / "tools" / "vendor-gitlab-chart.py"
VERSION = "10.5.4"

CHART_YAML = """\
apiVersion: v2
name: gitlab
version: %s
dependencies:
- name: gitlab
  version: "*.*.*"
- name: nginx-ingress
  version: "*.*.*"
""" % VERSION


def chart_tgz(dest: Path) -> Path:
    files = {
        "gitlab/Chart.yaml": CHART_YAML,
        "gitlab/values.yaml": "{}\n",
        "gitlab/CLAUDE.md": "upstream agent instructions\n",
        "gitlab/charts/gitlab/Chart.yaml": "name: gitlab\n",
        "gitlab/charts/nginx-ingress/Chart.yaml": "name: nginx-ingress\n",
    }
    tgz = dest / ("gitlab-%s.tgz" % VERSION)
    with tarfile.open(tgz, "w:gz") as tf:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return tgz


class InstalledCopy(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ik4p12-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        installed = self.tmp / "renovate-tools"
        installed.mkdir()
        self.installed = installed / "vendor-gitlab-chart.py"
        shutil.copy(SCRIPT, self.installed)
        self.tgz = chart_tgz(self.tmp)

    def run_in(self, cwd: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.installed), "--from-tgz", str(self.tgz), VERSION],
            cwd=cwd, capture_output=True, text=True)

    def test_it_vendors_into_the_checkout_it_runs_in(self):
        repo = self.tmp / "checkout"
        (repo / "gitlab").mkdir(parents=True)
        (repo / "gitlab" / "fleet.yaml").write_text("helm:\n  version: 10.5.1\n")

        done = self.run_in(repo)

        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("version: %s" % VERSION,
                      (repo / "gitlab" / "charts" / "gitlab" / "Chart.yaml").read_text())
        self.assertEqual("helm:\n  version: %s\n" % VERSION,
                         (repo / "gitlab" / "fleet.yaml").read_text())
        self.assertFalse((repo / "gitlab" / "charts" / "gitlab" / "CLAUDE.md").exists())
        self.assertEqual(["vendor-gitlab-chart.py"],
                         sorted(p.name for p in self.installed.parent.iterdir()),
                         "the installed copy must not write beside itself")
        self.assertFalse((self.tmp / "gitlab").exists())

    def test_it_refuses_a_cwd_that_is_not_a_fleet_checkout(self):
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()

        done = self.run_in(elsewhere)

        self.assertNotEqual(0, done.returncode)
        self.assertIn("gitlab/fleet.yaml", done.stderr)
        self.assertEqual([], list(elsewhere.iterdir()), "nothing may be written there")


if __name__ == "__main__":
    unittest.main()
