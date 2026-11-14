"""A caller's claude.ai login must reach the caller-login Claude groups only.

WHY THIS EXISTS (2026-10-28):

litellm forwards a client's `Authorization: Bearer sk-ant-oat…` to every anthropic/
deployment, where it beats the deployment's own key. A gwlogin Claude Code session that
reached `claude-api-*` (by /model, or by the claude-opus-5-5 -> claude-api-sonnet
fallback) was therefore billed to the SUBSCRIPTION instead of the org API credit, and
failed with the subscription's 429 / "credit balance is too low" once that was spent.

`custom_callbacks.client_oauth_scope` drops the OAuth entry from
`provider_specific_header` unless the deployment's api_key is the caller-login
placeholder. CI has no litellm, so its CustomLogger base class is stubbed.
"""

import asyncio
import pathlib
import sys
import types
import unittest

import yaml

CONFIG = pathlib.Path(__file__).resolve().parent.parent / "litellm" / "litellm-cm.yml"
OAUTH = {"custom_llm_provider": "anthropic,bedrock,bedrock_mantle,vertex_ai",
         "extra_headers": {"Authorization": "Bearer sk-ant-oat01-CLIENT"}}
BETA = {"custom_llm_provider": "anthropic", "extra_headers": {"anthropic-beta": "effort-2025-11-24"}}


class BadRequestError(Exception):
    """Stand-in for litellm.BadRequestError: same keywords, same status_code."""

    def __init__(self, message, model, llm_provider):
        super().__init__(message)
        self.status_code = 400


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


class ClientOAuthScopeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module = load_module()
        module.litellm.BadRequestError = BadRequestError
        cls.hook = module.client_oauth_scope

    def run_hook(self, kwargs):
        return asyncio.run(self.hook.async_pre_call_deployment_hook(dict(kwargs)))

    def test_registered(self):
        cfg = yaml.safe_load(CONFIG.read_text())
        config = yaml.safe_load(cfg["data"]["config.yaml"])
        self.assertIn("custom_callbacks.client_oauth_scope", config["litellm_settings"]["callbacks"])

    def test_own_key_drops_login(self):
        out = self.run_hook({"api_key": "sk-ant-api03-ORG", "provider_specific_header": dict(OAUTH)})
        self.assertIsNotNone(out)
        self.assertIsNone(out["provider_specific_header"])

    def test_own_key_keeps_other_headers(self):
        out = self.run_hook({"api_key": "sk-ant-api03-ORG", "provider_specific_header": [dict(BETA), dict(OAUTH)]})
        self.assertEqual(out["provider_specific_header"], BETA)

    def test_caller_login_group_keeps_login(self):
        self.assertIsNone(self.run_hook({"api_key": "client-login-required",
                                         "provider_specific_header": dict(OAUTH)}))

    def test_no_login_untouched(self):
        self.assertIsNone(self.run_hook({"api_key": "sk-ant-api03-ORG", "provider_specific_header": dict(BETA)}))
        self.assertIsNone(self.run_hook({"api_key": "sk-ant-api03-ORG"}))

    # 2026-11-13: a SERVICE key (windmill-ai) sent `work-gheim` with no claude.ai login.
    # The placeholder reached Anthropic, came back 401, and litellm cooled the SHARED
    # caller-login deployment for 1800 s -- every seat's `ccx work-gheim` with it.
    # A login-less request must be refused here, as a 400 (litellm never cools a 400).
    def test_caller_login_group_without_login_is_refused_as_400(self):
        for psh in (None, dict(BETA), [dict(BETA)]):
            kwargs = {"api_key": "client-login-required", "model": "anthropic/claude-opus-5-5"}
            if psh is not None:
                kwargs["provider_specific_header"] = psh
            with self.subTest(psh=psh), self.assertRaises(Exception) as caught:
                self.run_hook(kwargs)
            self.assertEqual(caught.exception.status_code, 400)
            self.assertIn("claude.ai login", str(caught.exception))

    def test_caller_login_group_with_login_among_other_headers_passes(self):
        self.assertIsNone(self.run_hook({"api_key": "client-login-required",
                                         "provider_specific_header": [dict(BETA), dict(OAUTH)]}))

    def test_caller_kwargs_not_mutated(self):
        psh = [dict(BETA), dict(OAUTH)]
        kwargs = {"api_key": "sk-ant-api03-ORG", "provider_specific_header": psh}
        asyncio.run(self.hook.async_pre_call_deployment_hook(dict(kwargs)))
        self.assertEqual(psh, [BETA, OAUTH])


if __name__ == "__main__":
    unittest.main()
