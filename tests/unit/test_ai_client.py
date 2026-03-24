"""
tests/unit/test_ai_client.py
=============================
Unit tests for ai_client.py — provider factory, complete(), acomplete(),
small_model routing, model_override, error forwarding.

No network calls. All SDK clients are mocked at the import level.

Coverage:
  TestAIClientBase         — base class interface: small_model, acomplete wires complete()
  TestAnthropicClient      — _AnthropicClient.complete() + model_override + system_prompt
  TestOpenAIClient         — _OpenAIClient.complete() + small_model + base_url routing
  TestOpenAIDirectComplete — _complete_direct() JSON and SSE parsing
  TestNonstandardEndpoint  — _is_nonstandard_endpoint() URL path detection
  TestFactoryFromEnv       — AIClient.from_env() env-var dispatch and error paths
  TestFactorySmallFromEnv  — AIClient.small_from_env() small model defaults
  TestModelOverride        — model_override param respected by both providers
  TestTokenTracking        — usage stats forwarded to token tracker
"""

import asyncio
import sys
import os
import unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from ai_client import AIClient, _AnthropicClient, _OpenAIClient


def _run(coro):
    return asyncio.run(coro)


# ── Helpers ───────────────────────────────────────────────────────────

def _make_anthropic_response(text: str, in_tok=10, out_tok=20):
    resp = MagicMock()
    content_block = MagicMock()
    content_block.text = text
    resp.content = [content_block]
    resp.usage = MagicMock()
    resp.usage.input_tokens        = in_tok
    resp.usage.output_tokens       = out_tok
    resp.usage.cache_read_input_tokens = 0
    return resp


def _make_openai_response(text: str, prompt_tok=5, completion_tok=15):
    resp = MagicMock()
    choice = MagicMock()
    choice.message.content = text
    resp.choices = [choice]
    resp.usage = MagicMock()
    resp.usage.prompt_tokens     = prompt_tok
    resp.usage.completion_tokens = completion_tok
    return resp


def _make_anthropic_client(text="OK"):
    """Return a patched _AnthropicClient with a stubbed SDK client."""
    c = _AnthropicClient(api_key="test-key", model="claude-3-5-sonnet-20241022")
    mock_sdk = MagicMock()
    mock_sdk.messages.create.return_value = _make_anthropic_response(text)
    c._client = mock_sdk
    return c, mock_sdk


def _make_openai_client(text="OK", base_url=None):
    """Return a patched _OpenAIClient with a stubbed SDK client."""
    c = _OpenAIClient(api_key="test-key", model="gpt-4o-mini", base_url=base_url)
    mock_sdk = MagicMock()
    mock_sdk.chat.completions.create.return_value = _make_openai_response(text)
    c._client = mock_sdk
    return c, mock_sdk


# ═══════════════════════════════════════════════════════════════════════
# Suite 1 — AIClient base
# ═══════════════════════════════════════════════════════════════════════

class TestAIClientBase(unittest.TestCase):

    def test_provider_stored(self):
        c = _AnthropicClient(api_key="k", model="claude-3-5-sonnet-20241022")
        self.assertEqual(c.provider, "anthropic")

    def test_model_stored(self):
        c = _AnthropicClient(api_key="k", model="claude-custom")
        self.assertEqual(c.model, "claude-custom")

    def test_base_small_model_defaults_to_model(self):
        """Base AIClient.small_model returns self.model (subclass may override)."""
        c = AIClient.__new__(AIClient)
        c.provider = "test"
        c.model    = "base-model"
        self.assertEqual(c.small_model, "base-model")

    def test_acomplete_delegates_to_complete(self):
        """acomplete() runs complete() and returns its value."""
        c, _ = _make_anthropic_client("hello from async")
        result = _run(c.acomplete("Test prompt"))
        self.assertEqual(result, "hello from async")

    def test_acomplete_passes_all_kwargs(self):
        c, sdk = _make_anthropic_client("result")
        _run(c.acomplete("prompt", system_prompt="sys", max_tokens=512,
                         temperature=0.5, model_override="claude-haiku"))
        call_kwargs = sdk.messages.create.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "claude-haiku")
        self.assertEqual(call_kwargs["max_tokens"], 512)
        self.assertEqual(call_kwargs["temperature"], 0.5)
        self.assertEqual(call_kwargs["system"], "sys")

    def test_base_complete_raises_not_implemented(self):
        c = AIClient.__new__(AIClient)
        c.provider = "test"
        c.model = "x"
        with self.assertRaises(NotImplementedError):
            c.complete("prompt")


# ═══════════════════════════════════════════════════════════════════════
# Suite 2 — _AnthropicClient
# ═══════════════════════════════════════════════════════════════════════

class TestAnthropicClient(unittest.TestCase):

    def test_complete_returns_text(self):
        c, _ = _make_anthropic_client("Hello world")
        self.assertEqual(c.complete("prompt"), "Hello world")

    def test_complete_sends_user_message(self):
        c, sdk = _make_anthropic_client()
        c.complete("Say hi")
        messages = sdk.messages.create.call_args.kwargs["messages"]
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[0]["content"], "Say hi")

    def test_complete_sends_model_name(self):
        c, sdk = _make_anthropic_client()
        c.complete("x")
        self.assertEqual(sdk.messages.create.call_args.kwargs["model"],
                         "claude-3-5-sonnet-20241022")

    def test_model_override_respected(self):
        c, sdk = _make_anthropic_client()
        c.complete("x", model_override="claude-haiku-4")
        self.assertEqual(sdk.messages.create.call_args.kwargs["model"],
                         "claude-haiku-4")

    def test_system_prompt_added_to_kwargs(self):
        c, sdk = _make_anthropic_client()
        c.complete("x", system_prompt="You are a tester")
        self.assertIn("system", sdk.messages.create.call_args.kwargs)
        self.assertEqual(sdk.messages.create.call_args.kwargs["system"],
                         "You are a tester")

    def test_no_system_prompt_not_in_kwargs(self):
        c, sdk = _make_anthropic_client()
        c.complete("x")
        self.assertNotIn("system", sdk.messages.create.call_args.kwargs)

    def test_max_tokens_forwarded(self):
        c, sdk = _make_anthropic_client()
        c.complete("x", max_tokens=1024)
        self.assertEqual(sdk.messages.create.call_args.kwargs["max_tokens"], 1024)

    def test_temperature_forwarded(self):
        c, sdk = _make_anthropic_client()
        c.complete("x", temperature=0.7)
        self.assertEqual(sdk.messages.create.call_args.kwargs["temperature"], 0.7)

    def test_empty_content_returns_empty_string(self):
        c = _AnthropicClient(api_key="k", model="claude-3-5-sonnet-20241022")
        mock_sdk = MagicMock()
        resp = MagicMock()
        resp.content = []
        resp.usage   = None
        mock_sdk.messages.create.return_value = resp
        c._client = mock_sdk
        self.assertEqual(c.complete("x"), "")

    def test_small_model_is_haiku(self):
        c = _AnthropicClient(api_key="k", model="claude-3-5-sonnet-20241022")
        self.assertIn("haiku", c.small_model.lower())

    def test_small_model_differs_from_main(self):
        c = _AnthropicClient(api_key="k", model="claude-3-5-sonnet-20241022")
        self.assertNotEqual(c.small_model, c.model)

    def test_lazy_client_creation(self):
        """SDK client is not instantiated until first complete() call."""
        c = _AnthropicClient(api_key="k", model="x")
        self.assertIsNone(c._client)

    def test_client_reused_across_calls(self):
        c, sdk = _make_anthropic_client()
        c.complete("first")
        c.complete("second")
        # _get() should return the same client both times
        self.assertEqual(sdk.messages.create.call_count, 2)


# ═══════════════════════════════════════════════════════════════════════
# Suite 3 — _OpenAIClient
# ═══════════════════════════════════════════════════════════════════════

class TestOpenAIClient(unittest.TestCase):

    def test_complete_returns_text(self):
        c, _ = _make_openai_client("response text")
        self.assertEqual(c.complete("prompt"), "response text")

    def test_user_message_in_messages(self):
        c, sdk = _make_openai_client()
        c.complete("Ask me anything")
        messages = sdk.chat.completions.create.call_args.kwargs["messages"]
        user_msg = next((m for m in messages if m["role"] == "user"), None)
        self.assertIsNotNone(user_msg)
        self.assertEqual(user_msg["content"], "Ask me anything")

    def test_system_prompt_prepended(self):
        c, sdk = _make_openai_client()
        c.complete("x", system_prompt="Be precise")
        messages = sdk.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], "Be precise")

    def test_no_system_prompt_single_message(self):
        c, sdk = _make_openai_client()
        c.complete("x")
        messages = sdk.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")

    def test_model_override_respected(self):
        c, sdk = _make_openai_client()
        c.complete("x", model_override="gpt-4-turbo")
        self.assertEqual(sdk.chat.completions.create.call_args.kwargs["model"],
                         "gpt-4-turbo")

    def test_empty_choices_returns_empty_string(self):
        c = _OpenAIClient(api_key="k", model="gpt-4o-mini")
        mock_sdk = MagicMock()
        resp = MagicMock()
        resp.choices = []
        resp.usage   = None
        mock_sdk.chat.completions.create.return_value = resp
        c._client = mock_sdk
        self.assertEqual(c.complete("x"), "")

    def test_small_model_no_base_url(self):
        c = _OpenAIClient(api_key="k", model="gpt-4o-mini")
        self.assertIn("mini", c.small_model.lower())

    def test_small_model_with_custom_base_url_returns_model(self):
        """Custom endpoint → small_model == model (no cheaper fallback known)."""
        c = _OpenAIClient(api_key="k", model="my-local-model",
                          base_url="http://localhost:8080/v1")
        self.assertEqual(c.small_model, "my-local-model")

    def test_lazy_client_creation(self):
        c = _OpenAIClient(api_key="k", model="gpt-4o-mini")
        self.assertIsNone(c._client)

    def test_max_tokens_forwarded(self):
        c, sdk = _make_openai_client()
        c.complete("x", max_tokens=256)
        self.assertEqual(sdk.chat.completions.create.call_args.kwargs["max_tokens"], 256)


# ═══════════════════════════════════════════════════════════════════════
# Suite 4 — _is_nonstandard_endpoint()
# ═══════════════════════════════════════════════════════════════════════

class TestNonstandardEndpoint(unittest.TestCase):

    def _c(self, base_url):
        return _OpenAIClient(api_key="k", model="m", base_url=base_url)

    def test_no_base_url_is_standard(self):
        c = _OpenAIClient(api_key="k", model="m")
        self.assertFalse(c._is_nonstandard_endpoint())

    def test_v1_base_url_is_standard(self):
        self.assertFalse(self._c("https://api.openai.com/v1")._is_nonstandard_endpoint())

    def test_v1_trailing_slash_is_standard(self):
        self.assertFalse(self._c("https://api.openai.com/v1/")._is_nonstandard_endpoint())

    def test_root_url_is_standard(self):
        self.assertFalse(self._c("https://api.example.com")._is_nonstandard_endpoint())

    def test_custom_path_is_nonstandard(self):
        self.assertTrue(self._c("https://api.example.com/v1/chat")._is_nonstandard_endpoint())

    def test_deep_path_is_nonstandard(self):
        self.assertTrue(self._c("https://my-proxy.com/llm/api/v1/chat/completions")._is_nonstandard_endpoint())

    def test_groq_v1_is_standard(self):
        self.assertFalse(self._c("https://api.groq.com/openai/v1")._is_nonstandard_endpoint())


# ═══════════════════════════════════════════════════════════════════════
# Suite 5 — _complete_direct() — SSE and JSON response parsing
# ═══════════════════════════════════════════════════════════════════════

class TestOpenAIDirectComplete(unittest.TestCase):

    def _c(self, base_url="https://proxy.local/v1/chat"):
        return _OpenAIClient(api_key="key", model="m", base_url=base_url)

    def _mock_response(self, body: str, content_type="application/json", status=200):
        resp = MagicMock()
        resp.text    = body
        resp.headers = {"content-type": content_type}
        resp.raise_for_status = MagicMock()
        return resp

    def test_json_response_extracted(self):
        import json
        body = json.dumps({
            "choices": [{"message": {"content": "direct answer"}}]
        })
        c = self._c()
        with patch("httpx.post", return_value=self._mock_response(body)):
            result = c._complete_direct(
                [{"role": "user", "content": "hi"}], "m", 100, 0
            )
        self.assertEqual(result, "direct answer")

    def test_sse_response_assembled(self):
        sse_body = (
            "data: {\"choices\": [{\"delta\": {\"content\": \"Hello \"}}]}\n"
            "data: {\"choices\": [{\"delta\": {\"content\": \"world\"}}]}\n"
            "data: [DONE]\n"
        )
        c = self._c()
        with patch("httpx.post",
                   return_value=self._mock_response(sse_body, "text/event-stream")):
            result = c._complete_direct(
                [{"role": "user", "content": "hi"}], "m", 100, 0
            )
        self.assertEqual(result, "Hello world")

    def test_sse_done_stops_at_done_marker(self):
        sse_body = (
            "data: {\"choices\": [{\"delta\": {\"content\": \"first\"}}]}\n"
            "data: [DONE]\n"
            "data: {\"choices\": [{\"delta\": {\"content\": \"ignored\"}}]}\n"
        )
        c = self._c()
        with patch("httpx.post",
                   return_value=self._mock_response(sse_body, "text/event-stream")):
            result = c._complete_direct([], "m", 100, 0)
        self.assertNotIn("ignored", result)

    def test_http_error_propagates(self):
        c = self._c()
        mock_resp = self._mock_response("")
        mock_resp.raise_for_status.side_effect = Exception("404 Not Found")
        with patch("httpx.post", return_value=mock_resp):
            with self.assertRaises(Exception):
                c._complete_direct([], "m", 100, 0)

    def test_model_sent_in_payload(self):
        import json
        body = json.dumps({"choices": [{"message": {"content": "ok"}}]})
        c = self._c()
        with patch("httpx.post", return_value=self._mock_response(body)) as mock_post:
            c._complete_direct([{"role": "user", "content": "x"}], "custom-model", 64, 0)
        call_kwargs = mock_post.call_args.kwargs
        self.assertEqual(call_kwargs["json"]["model"], "custom-model")

    def test_nonzero_temperature_included(self):
        import json
        body = json.dumps({"choices": [{"message": {"content": "ok"}}]})
        c = self._c()
        with patch("httpx.post", return_value=self._mock_response(body)) as mock_post:
            c._complete_direct([], "m", 64, temperature=0.9)
        self.assertEqual(mock_post.call_args.kwargs["json"]["temperature"], 0.9)

    def test_zero_temperature_not_included(self):
        import json
        body = json.dumps({"choices": [{"message": {"content": "ok"}}]})
        c = self._c()
        with patch("httpx.post", return_value=self._mock_response(body)) as mock_post:
            c._complete_direct([], "m", 64, temperature=0)
        self.assertNotIn("temperature", mock_post.call_args.kwargs["json"])


# ═══════════════════════════════════════════════════════════════════════
# Suite 6 — AIClient.from_env() factory
# ═══════════════════════════════════════════════════════════════════════

class TestFactoryFromEnv(unittest.TestCase):

    def _env(self, **kw):
        """Return os.environ patched with only the given vars."""
        clean = {k: "" for k in [
            "QAPAL_AI_PROVIDER", "QAPAL_AI_MODEL", "QAPAL_AI_BASE_URL",
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "GROK_API_KEY",
        ]}
        clean.update(kw)
        return patch.dict(os.environ, clean, clear=False)

    # ── Anthropic ──────────────────────────────────────────────────

    def test_anthropic_provider_returns_anthropic_client(self):
        with self._env(QAPAL_AI_PROVIDER="anthropic",
                       ANTHROPIC_API_KEY="test-key"):
            c = AIClient.from_env()
        self.assertIsInstance(c, _AnthropicClient)

    def test_anthropic_no_key_raises_environment_error(self):
        with self._env(QAPAL_AI_PROVIDER="anthropic", ANTHROPIC_API_KEY=""):
            with self.assertRaises(EnvironmentError) as ctx:
                AIClient.from_env()
        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))

    def test_anthropic_default_model(self):
        with self._env(QAPAL_AI_PROVIDER="anthropic",
                       ANTHROPIC_API_KEY="key", QAPAL_AI_MODEL=""):
            c = AIClient.from_env()
        self.assertIn("sonnet", c.model.lower())

    def test_anthropic_model_override_from_env(self):
        with self._env(QAPAL_AI_PROVIDER="anthropic",
                       ANTHROPIC_API_KEY="key",
                       QAPAL_AI_MODEL="claude-custom-model"):
            c = AIClient.from_env()
        self.assertEqual(c.model, "claude-custom-model")

    # ── OpenAI ────────────────────────────────────────────────────

    def test_openai_provider_returns_openai_client(self):
        with self._env(QAPAL_AI_PROVIDER="openai",
                       OPENAI_API_KEY="openai-key"):
            c = AIClient.from_env()
        self.assertIsInstance(c, _OpenAIClient)

    def test_openai_no_key_raises_environment_error(self):
        with self._env(QAPAL_AI_PROVIDER="openai",
                       OPENAI_API_KEY="", QAPAL_AI_BASE_URL=""):
            with self.assertRaises(EnvironmentError) as ctx:
                AIClient.from_env()
        self.assertIn("OPENAI_API_KEY", str(ctx.exception))

    def test_openai_default_model(self):
        with self._env(QAPAL_AI_PROVIDER="openai",
                       OPENAI_API_KEY="key", QAPAL_AI_MODEL=""):
            c = AIClient.from_env()
        self.assertIsNotNone(c.model)

    # ── Grok / xAI ───────────────────────────────────────────────

    def test_grok_provider_returns_openai_client_with_xai_url(self):
        with self._env(QAPAL_AI_PROVIDER="grok", XAI_API_KEY="xai-key"):
            c = AIClient.from_env()
        self.assertIsInstance(c, _OpenAIClient)
        self.assertIn("x.ai", c._base_url)

    def test_xai_provider_also_works(self):
        with self._env(QAPAL_AI_PROVIDER="xai", XAI_API_KEY="xai-key"):
            c = AIClient.from_env()
        self.assertIsInstance(c, _OpenAIClient)

    def test_grok_default_model(self):
        with self._env(QAPAL_AI_PROVIDER="grok", XAI_API_KEY="xai-key",
                       QAPAL_AI_MODEL=""):
            c = AIClient.from_env()
        self.assertIn("grok", c.model.lower())

    # ── Unknown provider ─────────────────────────────────────────

    def test_unknown_provider_raises_value_error(self):
        with self._env(QAPAL_AI_PROVIDER="beep_boop"):
            with self.assertRaises(ValueError) as ctx:
                AIClient.from_env()
        self.assertIn("beep_boop", str(ctx.exception))

    def test_unknown_provider_error_lists_valid_values(self):
        with self._env(QAPAL_AI_PROVIDER="magic"):
            with self.assertRaises(ValueError) as ctx:
                AIClient.from_env()
        self.assertIn("anthropic", str(ctx.exception).lower())

    # ── Custom base URL ──────────────────────────────────────────

    def test_base_url_applied_to_openai_client(self):
        with self._env(QAPAL_AI_PROVIDER="openai",
                       OPENAI_API_KEY="key",
                       QAPAL_AI_BASE_URL="https://my-proxy.com/v1"):
            c = AIClient.from_env()
        self.assertEqual(c._base_url, "https://my-proxy.com/v1")

    def test_openai_no_key_ok_with_base_url(self):
        """Custom endpoint with no API key is allowed (e.g. local server)."""
        with self._env(QAPAL_AI_PROVIDER="openai",
                       OPENAI_API_KEY="",
                       QAPAL_AI_BASE_URL="http://localhost:8080/v1"):
            c = AIClient.from_env()
        self.assertIsInstance(c, _OpenAIClient)


# ═══════════════════════════════════════════════════════════════════════
# Suite 7 — AIClient.small_from_env()
# ═══════════════════════════════════════════════════════════════════════

class TestFactorySmallFromEnv(unittest.TestCase):

    def _env(self, **kw):
        clean = {k: "" for k in [
            "QAPAL_AI_PROVIDER", "QAPAL_AI_MODEL", "QAPAL_AI_BASE_URL",
            "QAPAL_AI_SMALL_PROVIDER", "QAPAL_AI_SMALL_MODEL", "QAPAL_AI_SMALL_BASE_URL",
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY",
        ]}
        clean.update(kw)
        return patch.dict(os.environ, clean, clear=False)

    def test_small_from_env_returns_anthropic_by_default(self):
        with self._env(QAPAL_AI_PROVIDER="anthropic",
                       ANTHROPIC_API_KEY="key"):
            c = AIClient.small_from_env()
        self.assertIsInstance(c, _AnthropicClient)

    def test_small_from_env_uses_haiku_by_default_for_anthropic(self):
        with self._env(QAPAL_AI_PROVIDER="anthropic",
                       ANTHROPIC_API_KEY="key"):
            c = AIClient.small_from_env()
        self.assertIn("haiku", c.model.lower())

    def test_small_model_env_var_overrides_default(self):
        with self._env(QAPAL_AI_PROVIDER="anthropic",
                       ANTHROPIC_API_KEY="key",
                       QAPAL_AI_SMALL_MODEL="claude-custom-small"):
            c = AIClient.small_from_env()
        self.assertEqual(c.model, "claude-custom-small")

    def test_small_provider_can_differ_from_main(self):
        """QAPAL_AI_SMALL_PROVIDER can point to a different provider."""
        with self._env(QAPAL_AI_PROVIDER="anthropic",
                       ANTHROPIC_API_KEY="key",
                       QAPAL_AI_SMALL_PROVIDER="openai",
                       OPENAI_API_KEY="openai-key"):
            c = AIClient.small_from_env()
        self.assertIsInstance(c, _OpenAIClient)

    def test_small_from_env_falls_back_to_main_provider(self):
        """When QAPAL_AI_SMALL_PROVIDER is unset, main provider is used."""
        with self._env(QAPAL_AI_PROVIDER="anthropic",
                       ANTHROPIC_API_KEY="key",
                       QAPAL_AI_SMALL_PROVIDER=""):
            c = AIClient.small_from_env()
        self.assertIsInstance(c, _AnthropicClient)


# ═══════════════════════════════════════════════════════════════════════
# Suite 8 — model_override param
# ═══════════════════════════════════════════════════════════════════════

class TestModelOverride(unittest.TestCase):

    def test_anthropic_override_replaces_model(self):
        c, sdk = _make_anthropic_client()
        c.complete("x", model_override="claude-haiku-4")
        self.assertEqual(sdk.messages.create.call_args.kwargs["model"], "claude-haiku-4")

    def test_anthropic_no_override_uses_instance_model(self):
        c, sdk = _make_anthropic_client()
        c.complete("x")
        self.assertEqual(sdk.messages.create.call_args.kwargs["model"],
                         "claude-3-5-sonnet-20241022")

    def test_openai_override_replaces_model(self):
        c, sdk = _make_openai_client()
        c.complete("x", model_override="gpt-4-turbo")
        self.assertEqual(sdk.chat.completions.create.call_args.kwargs["model"],
                         "gpt-4-turbo")

    def test_openai_no_override_uses_instance_model(self):
        c, sdk = _make_openai_client()
        c.complete("x")
        self.assertEqual(sdk.chat.completions.create.call_args.kwargs["model"],
                         "gpt-4o-mini")

    def test_override_does_not_mutate_instance_model(self):
        c, _ = _make_anthropic_client()
        c.complete("x", model_override="claude-haiku-4")
        self.assertEqual(c.model, "claude-3-5-sonnet-20241022")


# ═══════════════════════════════════════════════════════════════════════
# Suite 9 — Token tracking
# ═══════════════════════════════════════════════════════════════════════

class TestTokenTracking(unittest.TestCase):

    def test_anthropic_records_tokens(self):
        c, sdk = _make_anthropic_client("response")
        sdk.messages.create.return_value = _make_anthropic_response(
            "response", in_tok=42, out_tok=17
        )
        mock_tracker = MagicMock()
        with patch("ai_client.get_token_tracker", return_value=mock_tracker):
            c.complete("x")
        mock_tracker.record.assert_called_once()
        kwargs = mock_tracker.record.call_args.kwargs
        self.assertEqual(kwargs["in_tok"], 42)
        self.assertEqual(kwargs["out_tok"], 17)

    def test_openai_records_tokens(self):
        c, sdk = _make_openai_client("response")
        sdk.chat.completions.create.return_value = _make_openai_response(
            "response", prompt_tok=30, completion_tok=12
        )
        mock_tracker = MagicMock()
        with patch("ai_client.get_token_tracker", return_value=mock_tracker):
            c.complete("x")
        mock_tracker.record.assert_called_once()
        kwargs = mock_tracker.record.call_args.kwargs
        self.assertEqual(kwargs["in_tok"], 30)
        self.assertEqual(kwargs["out_tok"], 12)

    def test_anthropic_no_usage_does_not_crash(self):
        c = _AnthropicClient(api_key="k", model="m")
        mock_sdk = MagicMock()
        resp = MagicMock()
        resp.content = [MagicMock(text="ok")]
        resp.usage   = None
        mock_sdk.messages.create.return_value = resp
        c._client = mock_sdk
        # Should not raise
        result = c.complete("x")
        self.assertEqual(result, "ok")

    def test_openai_no_usage_does_not_crash(self):
        c = _OpenAIClient(api_key="k", model="m")
        mock_sdk = MagicMock()
        resp = MagicMock()
        choice = MagicMock()
        choice.message.content = "ok"
        resp.choices = [choice]
        resp.usage   = None
        mock_sdk.chat.completions.create.return_value = resp
        c._client = mock_sdk
        result = c.complete("x")
        self.assertEqual(result, "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
