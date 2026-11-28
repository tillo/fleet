"""Every `-gheim` twin fails over exactly like its base, one guarded hop for each hop.

The twins are DB deployments, so this file is the only place their failover is declared.
The Router looks fallbacks up by the name the client SENT, so a twin inherits nothing
from its base: a twin with no row raises where its base tails (claude-api-sonnet-gheim
errored at the API cap while claude-api-sonnet fell to flash). Each hop must itself be a
twin, or the round trip is lost on the way down. A base with no row (the us-claude-haiku
vision path) means the twin has none either.
"""

import pathlib
import unittest

import yaml

CONFIG = pathlib.Path(__file__).resolve().parent.parent / "litellm" / "litellm-cm.yml"
SUFFIX = "-gheim"
# The DB's -gheim model_names (GET /model/info), as of 2026-11-16.
TWINS = (
    "claude-api-sonnet-gheim",
    "claude-fable-5-1-gheim",
    "claude-haiku-4-5-20251001-gheim",
    "claude-mythos-5-2-gheim",
    "claude-opus-5-5-gheim",
    "claude-sonnet-5-5-gheim",
    "cn-deepseek-flash-gheim",
    "cn-deepseek-pro-gheim",
    "us-claude-haiku-gheim",
    "us-claude-sonnet-gheim",
    "us-to-cn-gheim",
    "work-gheim",
)


def fallback_rows() -> dict:
    config = yaml.safe_load(yaml.safe_load(CONFIG.read_text())["data"]["config.yaml"])
    rows = {}
    for row in config["litellm_settings"]["fallbacks"]:
        rows.update(row)
    return rows


class GheimTwinsTailLikeTheirBase(unittest.TestCase):
    def test_every_twin_row_is_its_base_row_guarded(self):
        rows = fallback_rows()
        for twin in TWINS:
            base = rows.get(twin[: -len(SUFFIX)])
            want = None if base is None else [hop + SUFFIX for hop in base]
            with self.subTest(twin=twin):
                self.assertEqual(rows.get(twin), want)


if __name__ == "__main__":
    unittest.main()
