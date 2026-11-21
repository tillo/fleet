"""Only the heal job may write O2's shared `pushover_mdapi` template and destination.

WHY THIS EXISTS (infra-ijhhj, 2026-11-09):

Every O2 alert on the estate pages through the ONE `pushover_mdapi` template, whose token
Windmill f/heal/openobserve_pushover_sync reconciles from Akeyless /mdapi/pushover/o2-api-token.
technitium/scripts/oo-technitium-alerts.py also rewrote that template on every run, with
/mdapi/pushover/mdapi-alertmanager-token -- an app token Pushover now rejects ("application
token is invalid", measured 2026-11-09). One re-run of the script would have broken every O2
page until the heal's next pass, with nothing erroring locally.

The test runs the script's main() the way an operator does, with Akeyless, kubectl and the
O2 API stubbed, and asserts it only touches its own two alerts.
"""

import contextlib
import importlib.util
import io
import pathlib
import subprocess
import unittest
from unittest import mock

SCRIPT = (pathlib.Path(__file__).resolve().parent.parent
          / "technitium" / "scripts" / "oo-technitium-alerts.py")
SHARED = ("/alerts/templates", "/alerts/destinations")


def run_script():
    """main() with every side effect stubbed; returns (akeyless names, O2 requests)."""
    names, calls = [], []

    def fake_run(argv, **_):
        names.append(argv[argv.index("--name") + 1])
        return subprocess.CompletedProcess(argv, 0, stdout="stub\n", stderr="")

    def fake_req(method, path, body=None, auth=None):
        calls.append((method, path))
        return (200, {"list": []}) if method == "GET" else (200, {})

    with mock.patch("subprocess.run", fake_run), mock.patch("subprocess.Popen"):
        spec = importlib.util.spec_from_file_location("oo_technitium_alerts", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with mock.patch.object(mod, "req", fake_req), mock.patch.object(mod.time, "sleep"), \
                contextlib.redirect_stdout(io.StringIO()):
            mod.main()
    return names, calls


class TechnitiumAlertsLeaveTheSharedTemplateAlone(unittest.TestCase):
    def setUp(self):
        self.names, self.calls = run_script()

    def test_never_writes_the_shared_template_or_destination(self):
        touched = [c for c in self.calls if any(s in c[1] for s in SHARED)]
        self.assertEqual(touched, [], "pushover_mdapi is owned by f/heal/openobserve_pushover_sync")

    def test_never_reads_a_pushover_token(self):
        self.assertEqual([n for n in self.names if n.startswith("/mdapi/pushover/")], [])

    def test_still_upserts_both_alerts_to_the_shared_destination(self):
        writes = [c for c in self.calls if c[0] in ("POST", "PUT") and c[1].startswith("/v2/")]
        self.assertEqual(len(writes), 2, self.calls)


if __name__ == "__main__":
    unittest.main()
