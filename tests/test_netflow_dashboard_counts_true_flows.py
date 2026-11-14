"""The netflow dashboard counts TRUE flows and keeps NTP/DNS out of the conversations.

cribl-syslog/scripts/oo-netflow-dashboard.py is the source of the O2 dashboard
'Network flows — perimeter and inter-VLAN' (infra-g1xc). Cribl samples NTP 1:100
and stamps `sampled` on every event, so a panel that counts rows undercounts NTP
100x; and NTP + DNS are ~97 % of all flows, so a who-talks-to-whom panel that
keeps them shows nothing else.
"""
import importlib.util
import re
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "cribl-syslog" / "scripts" / "oo-netflow-dashboard.py"


def _load():
    spec = importlib.util.spec_from_file_location("oo_netflow_dashboard", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


D = _load()
SQL = {p["id"]: p["queries"][0]["query"] for p in D.PANELS}


class TrueCounts(unittest.TestCase):
    def test_no_panel_counts_rows(self):
        for pid, sql in SQL.items():
            with self.subTest(pid=pid):
                self.assertNotRegex(sql.lower(), r"count\s*\(")

    def test_every_sum_is_weighted_by_sampled_cast_to_a_number(self):
        for pid, sql in SQL.items():
            with self.subTest(pid=pid):
                sums = re.findall(r"sum\((?:[^()]|\([^()]*\))*\)", sql)
                self.assertTrue(sums, "a panel with no sum shows no counts")
                for s in sums:
                    self.assertIn("cast(sampled as bigint)", s)


class Exclusion(unittest.TestCase):
    def test_every_conversation_table_excludes_ntp_and_dns(self):
        tables = [p for p in D.PANELS if p["type"] == "table"]
        self.assertGreaterEqual(len(tables), 5)
        for p in tables:
            with self.subTest(pid=p["id"]):
                self.assertIn(D.NOT_NTP_DNS, p["queries"][0]["query"])

    def test_the_exclusion_negates_both_services(self):
        self.assertIn(f"NOT {D.NTP}", D.NOT_NTP_DNS)
        self.assertIn(f"NOT {D.DNS}", D.NOT_NTP_DNS)

    def test_the_exclusion_names_both_directions_of_both_services(self):
        for port in ("123", "53", "853"):
            self.assertIn(port, D.NTP + D.DNS)
        for side in ("destinationtransportport", "sourcetransportport"):
            self.assertIn(side, D.NTP)
            self.assertIn(side, D.DNS)

    def test_the_excluded_classes_stay_visible_somewhere(self):
        self.assertTrue([p for p in D.PANELS if "breakdown_1" in p["queries"][0]["query"]
                         and D.NOT_NTP_DNS not in p["queries"][0]["query"]])


class Shape(unittest.TestCase):
    def test_panel_ids_and_layout_slots_are_unique(self):
        self.assertEqual(len({p["id"] for p in D.PANELS}), len(D.PANELS))
        self.assertEqual(len({p["layout"]["i"] for p in D.PANELS}), len(D.PANELS))

    def test_o2_rejects_a_panel_without_description(self):
        for p in D.PANELS:
            with self.subTest(pid=p["id"]):
                self.assertTrue(p["description"])

    def test_every_panel_is_sent_as_sql(self):
        self.assertEqual({p.get("queryType") for p in D._with_query_type()["tabs"][0]["panels"]},
                         {"sql"})


if __name__ == "__main__":
    unittest.main()
