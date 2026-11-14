"""Every caller-login Claude group carries the same fallback tail, and so does every
alias that points at one.

The login groups (Claude Code's own model ids, served on the caller's claude.ai login)
are DB deployments, so this file is the only place their failover is declared. A group
with no `litellm_settings.fallbacks` row raises at the subscription cap instead of tailing
to the API credit and DeepSeek flash like its siblings. An alias does NOT inherit its
target's row (the Router looks fallbacks up by the name the client SENT), so an alias
moved to a new target still needs its own row with that target's tail.
"""

import pathlib
import unittest

import yaml

CONFIG = pathlib.Path(__file__).resolve().parent.parent / "litellm" / "litellm-cm.yml"
LOGIN_GROUPS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1",
                "claude-fable-5-2", "claude-mythos-5-2")


def load_config() -> dict:
    return yaml.safe_load(yaml.safe_load(CONFIG.read_text())["data"]["config.yaml"])


def fallback_rows(config: dict) -> dict:
    rows = {}
    for row in config["litellm_settings"]["fallbacks"]:
        rows.update(row)
    return rows


class LoginGroupsTail(unittest.TestCase):
    def setUp(self):
        config = load_config()
        self.settings = config["router_settings"]
        self.rows = fallback_rows(config)
        self.tail = self.rows["claude-opus-5-5"]

    def test_every_login_group_tails_like_opus(self):
        for group in LOGIN_GROUPS:
            with self.subTest(group=group):
                self.assertEqual(self.rows.get(group), self.tail)

    def test_every_login_twin_tails_like_the_opus_twin(self):
        twin_tail = self.rows["claude-opus-5-5-gheim"]
        for twin in ("claude-fable-5-1-gheim", "claude-mythos-5-2-gheim"):
            with self.subTest(twin=twin):
                self.assertEqual(self.rows.get(twin), twin_tail)

    def test_bare_fable_is_the_current_fable(self):
        self.assertEqual(self.settings["model_group_alias"]["fable"], "claude-fable-5-2")

    def test_an_alias_onto_a_login_group_has_its_own_row(self):
        for alias, target in self.settings["model_group_alias"].items():
            if target in LOGIN_GROUPS:
                with self.subTest(alias=alias):
                    self.assertEqual(self.rows.get(alias), self.rows[target])


if __name__ == "__main__":
    unittest.main()
