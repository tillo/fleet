"""`keel.sh/pollSchedule` is SECONDS-FIRST, and a 5-field crontab is misparsed silently.

Keel parses this with a cron library whose expressions carry a leading seconds field, so
a perfectly ordinary 5-field crontab is ACCEPTED and means something else entirely --
"30 2 * * *" becomes "at second 30 of minute 2 of every hour", not "02:30 daily". Nothing
logs a warning. An unparseable value is worse: Keel falls back to `@every 4h` silently.

The schedule is also evaluated in UTC, not Europe/Zurich.

Accepted forms:
  * `@every <duration>` / other `@`-macros -- no fields to miscount
  * a 6-field cron expression (sec min hour dom mon dow)
"""

import re
import unittest

import fleetlib

ANNOTATION = "keel.sh/pollSchedule"
# strip a trailing comment, then quotes
VALUE_RE = re.compile(r"keel\.sh/pollSchedule:\s*(?P<value>.+?)\s*$")


def poll_schedules() -> list[tuple[str, int, str]]:
    """(file, line_no, value) for every real pollSchedule annotation.

    Read as text rather than parsed YAML because the annotation frequently lives inside
    Helm values at arbitrary depth, where a structured walk would need the chart schema.
    Comment lines are skipped so prose describing the annotation is not mistaken for a
    use -- a grep hit is not a use.
    """
    found = []
    for rel in fleetlib.tracked_yaml():
        try:
            text = (fleetlib.repo_root() / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        if ANNOTATION not in text:
            continue
        for n, line in enumerate(text.splitlines(), start=1):
            if ANNOTATION not in line or line.strip().startswith("#"):
                continue
            m = VALUE_RE.search(line)
            if not m:
                continue
            value = m.group("value").split(" #")[0].strip().strip("\"'")
            found.append((rel, n, value))
    return found


class KeelPollScheduleTest(unittest.TestCase):
    def test_cron_expressions_have_six_fields(self):
        schedules = poll_schedules()

        # ⛔ Empty selection is not a pass.
        self.assertGreater(
            len(schedules), 0,
            f"no {ANNOTATION} annotations found at all -- the scan is broken",
        )

        bad = []
        for rel, line_no, value in schedules:
            if value.startswith("@"):
                continue  # @every / @daily: no fields to miscount
            fields = value.split()
            if len(fields) != 6:
                bad.append(
                    f"{rel}:{line_no}: pollSchedule {value!r} has {len(fields)} fields. "
                    f"Keel is SECONDS-FIRST and needs 6 (sec min hour dom mon dow); a "
                    f"5-field crontab is accepted and silently means something else. "
                    f"Prepend a seconds field, e.g. '0 {value}'."
                )
        self.assertEqual([], bad, "\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
