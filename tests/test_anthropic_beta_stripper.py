"""Claude Code's `anthropic-beta` marker must not reach the org-key deployments.

WHY THIS EXISTS (2026-10-29):

litellm forwards the client's `anthropic-beta` header to every anthropic/bedrock/vertex
deployment (`litellm_pre_call_utils.ANTHROPIC_API_HEADERS`, alongside `anthropic-version`).
Anthropic refuses `anthropic-beta: claude-code-*` when the request is authenticated with the
org API credit -- measured in-pod: same key and body, header present -> 400 "credit balance is
too low", header absent -> 200. Through the gateway the refusal was MASKED: the chain fell
back to `cn-deepseek-flash` and returned 200, so `ccx api-*` looked like it was working.

`custom_callbacks.anthropic_beta_stripper` drops the `claude-code-*` value from
`provider_specific_header` unless the deployment's api_key is the caller-login placeholder,
where the request really is Code traffic and the header must survive. CI has no litellm, so
its CustomLogger base class is stubbed -- same harness as test_client_oauth_scope.py.

The scope is deliberately narrow: only values starting with `claude-code-` are touched, so an
unrelated beta (`effort-2025-11-24`) still reaches the vendor.
"""

import asyncio
import pathlib
import sys
import types
import unittest

import yaml

CONFIG = pathlib.Path(__file__).resolve().parent.parent / "litellm" / "litellm-cm.yml"
PROVIDERS = "anthropic,bedrock,bedrock_mantle,vertex_ai"
CODE_BETA = "claude-code-20250219"
OTHER_BETA = "effort-2025-11-24"


def entry(**headers) -> dict:
    return {"custom_llm_provider": PROVIDERS, "extra_headers": headers}


def oauth_entry() -> dict:
    return {"custom_llm_provider": "anthropic",
            "extra_headers": {"Authorization": "Bearer sk-ant-oat01-CLIENT"}}


def load_module() -> types.ModuleType:
    source = yaml.safe_load(CONFIG.read_text())["data"]["custom_callbacks.py"]
    stub = types.ModuleType("litellm.integrations.custom_logger")
    stub.CustomLogger = type("CustomLogger", (), {})
    saved = {k: sys.modules.get(k) for k in
             ("litellm", "litellm.integrations", "litellm.integrations.custom_logger")}
    sys.modules.update({"litellm": types.ModuleType("litellm"),
                        "litellm.integrations": types.ModuleType("litellm.integrations"),
                        "litellm.integrations.custom_logger": stub})
    try:
        module = types.ModuleType("custom_callbacks")
        exec(compile(source, "custom_callbacks.py", "exec"), module.__dict__)
        return module
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


class AnthropicBetaStripperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hook = load_module().anthropic_beta_stripper

    def run_hook(self, kwargs):
        return asyncio.run(self.hook.async_pre_call_deployment_hook(dict(kwargs)))

    def test_registered(self):
        cfg = yaml.safe_load(CONFIG.read_text())
        config = yaml.safe_load(cfg["data"]["config.yaml"])
        callbacks = config["litellm_settings"]["callbacks"]
        self.assertIn("custom_callbacks.anthropic_beta_stripper", callbacks)
        # Order matters: the OAuth scope runs first and may collapse a two-entry list to a
        # scalar, which this hook must then handle. Assert the sequence, not just membership.
        self.assertLess(callbacks.index("custom_callbacks.client_oauth_scope"),
                        callbacks.index("custom_callbacks.anthropic_beta_stripper"))

    def test_own_key_strips_code_beta(self):
        out = self.run_hook({"api_key": "sk-ant-api03-ORG",
                             "provider_specific_header": entry(**{"anthropic-beta": CODE_BETA,
                                                                  "anthropic-version": "2023-06-01"})})
        self.assertIsNotNone(out)
        self.assertEqual(out["provider_specific_header"]["extra_headers"],
                         {"anthropic-version": "2023-06-01"})

    def test_own_key_beta_only_clears_header(self):
        # extra_headers would empty, so the entry goes and the whole field is cleared --
        # never an entry left with an empty dict.
        out = self.run_hook({"api_key": "sk-ant-api03-ORG",
                             "provider_specific_header": entry(**{"anthropic-beta": CODE_BETA})})
        self.assertIsNotNone(out)
        self.assertIsNone(out["provider_specific_header"])

    def test_caller_login_group_keeps_beta(self):
        self.assertIsNone(self.run_hook({"api_key": "client-login-required",
                                         "provider_specific_header": entry(**{"anthropic-beta": CODE_BETA})}))

    def test_other_beta_values_untouched(self):
        self.assertIsNone(self.run_hook({"api_key": "sk-ant-api03-ORG",
                                         "provider_specific_header": entry(**{"anthropic-beta": OTHER_BETA})}))

    def test_no_beta_untouched(self):
        self.assertIsNone(self.run_hook({"api_key": "sk-ant-api03-ORG",
                                         "provider_specific_header": entry(**{"anthropic-version": "2023-06-01"})}))
        self.assertIsNone(self.run_hook({"api_key": "sk-ant-api03-ORG"}))

    def test_list_keeps_oauth_entry(self):
        out = self.run_hook({"api_key": "sk-ant-api03-ORG",
                             "provider_specific_header": [oauth_entry(),
                                                          entry(**{"anthropic-beta": CODE_BETA,
                                                                   "anthropic-version": "2023-06-01"})]})
        self.assertIsInstance(out["provider_specific_header"], list)
        self.assertEqual(out["provider_specific_header"][0], oauth_entry())
        self.assertEqual(out["provider_specific_header"][1]["extra_headers"], {"anthropic-version": "2023-06-01"})

    def test_list_collapses_to_scalar(self):
        out = self.run_hook({"api_key": "sk-ant-api03-ORG",
                             "provider_specific_header": [
                                 entry(**{"anthropic-beta": CODE_BETA}),
                                 {"custom_llm_provider": "anthropic",
                                  "extra_headers": {"anthropic-beta": CODE_BETA, "x": "1"}}]})
        self.assertEqual(out["provider_specific_header"],
                         {"custom_llm_provider": "anthropic", "extra_headers": {"x": "1"}})

    def test_caller_kwargs_not_mutated(self):
        # utils.async_pre_call_deployment_hook hands callbacks a SHALLOW copy and the router
        # reuses the same entry dicts on every fallback hop.
        psh = [dict(entry(**{"anthropic-beta": CODE_BETA})), oauth_entry()]
        before = [dict(p, extra_headers=dict(p["extra_headers"])) for p in psh]
        asyncio.run(self.hook.async_pre_call_deployment_hook(
            {"api_key": "sk-ant-api03-ORG", "provider_specific_header": psh}))
        self.assertEqual(psh, before)


if __name__ == "__main__":
    unittest.main()
