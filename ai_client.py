"""
ai_client.py — QAPal AI Client
================================
Single place for all AI provider access.

Supports:
  - Anthropic Claude   (QAPAL_AI_PROVIDER=anthropic)
  - OpenAI             (QAPAL_AI_PROVIDER=openai)
  - xAI / Grok        (QAPAL_AI_PROVIDER=grok)
  - Any OpenAI-compatible endpoint (set QAPAL_AI_BASE_URL)

All config from environment variables. No config files.

Usage:
    client = AIClient.from_env()
    response = client.complete("What is 2+2?")             # sync
    response = await client.acomplete("What is 2+2?")      # async

Install:
    pip install anthropic openai python-dotenv
"""

import asyncio
import os
from typing import Optional

from _log import get_logger
from _tokens import get_token_tracker

_log = get_logger("ai_client")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv optional — env vars may already be set


# ── Base ──────────────────────────────────────────────────────────────

class AIClient:
    """
    Unified AI client. Sync and async complete() on the same object.
    Always call from_env() to construct — never instantiate directly.
    """

    def __init__(self, provider: str, model: str):
        self.provider = provider
        self.model    = model

    # ── Sync ──────────────────────────────────────────────────────────

    # Small model used for cheap validation / recovery passes.
    # Subclasses may override; defaults to the main model.
    @property
    def small_model(self) -> str:
        return self.model

    def complete(
        self,
        prompt:         str,
        system_prompt:  Optional[str] = None,
        max_tokens:     int           = 4096,
        temperature:    float         = 0,
        model_override: Optional[str] = None,
    ) -> str:
        raise NotImplementedError

    # ── Async wrapper ─────────────────────────────────────────────────
    # Runs sync complete() in a thread so it never blocks the event loop.

    async def acomplete(
        self,
        prompt:         str,
        system_prompt:  Optional[str] = None,
        max_tokens:     int           = 4096,
        temperature:    float         = 0,
        model_override: Optional[str] = None,
    ) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.complete(prompt, system_prompt, max_tokens, temperature, model_override),
        )

    # ── Factory ───────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "AIClient":
        """
        Build an AIClient from environment variables.

        Required:
            QAPAL_AI_PROVIDER   — anthropic | openai | grok
            One of:
                ANTHROPIC_API_KEY
                OPENAI_API_KEY
                XAI_API_KEY

        Optional:
            QAPAL_AI_MODEL     — model name (provider default used if unset)
            QAPAL_AI_BASE_URL  — custom OpenAI-compatible endpoint
        """
        return cls._build(
            provider_var = "QAPAL_AI_PROVIDER",
            model_var    = "QAPAL_AI_MODEL",
            base_url_var = "QAPAL_AI_BASE_URL",
        )

    @classmethod
    def small_from_env(cls) -> "AIClient":
        """
        Build a cheap/fast AIClient from QAPAL_AI_SMALL_* env vars.
        Falls back to the main QAPAL_AI_* vars for any value not set.

        Optional:
            QAPAL_AI_SMALL_PROVIDER  — provider for the small model (defaults to QAPAL_AI_PROVIDER)
            QAPAL_AI_SMALL_MODEL     — model name (defaults to provider built-in small)
            QAPAL_AI_SMALL_BASE_URL  — base URL (defaults to QAPAL_AI_BASE_URL)
        """
        return cls._build(
            provider_var     = "QAPAL_AI_SMALL_PROVIDER",
            model_var        = "QAPAL_AI_SMALL_MODEL",
            base_url_var     = "QAPAL_AI_SMALL_BASE_URL",
            fallback_provider= os.getenv("QAPAL_AI_PROVIDER", "anthropic"),
            fallback_base_url= os.getenv("QAPAL_AI_BASE_URL", ""),
            use_small_default= True,
        )

    @classmethod
    def _build(
        cls,
        provider_var:      str,
        model_var:         str,
        base_url_var:      str,
        fallback_provider: str = "anthropic",
        fallback_base_url: str = "",
        use_small_default: bool = False,
    ) -> "AIClient":
        provider = (os.getenv(provider_var, "").strip() or fallback_provider).lower()
        model    = os.getenv(model_var, "").strip() or None
        base_url = (os.getenv(base_url_var, "").strip() or fallback_base_url) or None

        if provider == "anthropic":
            api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
            if not api_key:
                raise EnvironmentError(
                    "ANTHROPIC_API_KEY is not set. "
                    "Add it to your .env file or environment."
                )
            default = _AnthropicClient._SMALL_MODEL if use_small_default else "claude-sonnet-4-6"
            return _AnthropicClient(api_key=api_key, model=model or default)

        if provider in ("openai", "grok", "xai"):
            if provider in ("grok", "xai"):
                api_key  = (
                    os.getenv("XAI_API_KEY", "").strip()
                    or os.getenv("GROK_API_KEY", "").strip()
                )
                base_url = base_url or "https://api.x.ai/v1"
                default  = model or "grok-2-latest"
            else:
                api_key = os.getenv("OPENAI_API_KEY", "").strip()
                if use_small_default and not base_url:
                    default = model or _OpenAIClient._SMALL_MODEL_OPENAI
                else:
                    default = model or "gpt-4o-mini"

            if not api_key and not base_url:
                key_name = "XAI_API_KEY" if provider in ("grok", "xai") else "OPENAI_API_KEY"
                raise EnvironmentError(
                    f"{key_name} is not set. "
                    "Add it to your .env file or environment."
                )

            return _OpenAIClient(
                api_key  = api_key or "dummy",
                model    = default,
                base_url = base_url,
            )

        raise ValueError(
            f"Unknown provider: '{provider}'. "
            "Valid values: anthropic, openai, grok"
        )


# ── Anthropic ─────────────────────────────────────────────────────────

class _AnthropicClient(AIClient):

    _SMALL_MODEL = "claude-haiku-4-5-20251001"

    def __init__(self, api_key: str, model: str):
        super().__init__("anthropic", model)
        self._api_key = api_key
        self._client  = None

    @property
    def small_model(self) -> str:
        return self._SMALL_MODEL

    def _get(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def complete(
        self,
        prompt:         str,
        system_prompt:  Optional[str] = None,
        max_tokens:     int           = 4096,
        temperature:    float         = 0,
        model_override: Optional[str] = None,
    ) -> str:
        client = self._get()
        kwargs = {
            "model":       model_override or self.model,
            "max_tokens":  max_tokens,
            "messages":    [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        response = client.messages.create(**kwargs)
        if not response.content:
            return ""
        # Record token usage
        usage = getattr(response, "usage", None)
        if usage:
            get_token_tracker().record(
                in_tok     = getattr(usage, "input_tokens", 0),
                out_tok    = getattr(usage, "output_tokens", 0),
                cache_read = getattr(usage, "cache_read_input_tokens", 0),
                model      = kwargs.get("model", self.model),
                phase      = "ai",
            )
        return response.content[0].text


# ── OpenAI-compatible ─────────────────────────────────────────────────

class _OpenAIClient(AIClient):

    # Small model for cheap validation / recovery — override per-call via model_override.
    # For Groq/OpenAI-compat endpoints the same model is used (already lightweight).
    _SMALL_MODEL_OPENAI = "gpt-4o-mini"

    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        super().__init__("openai", model)
        self._api_key  = api_key
        self._base_url = base_url
        self._client   = None

    @property
    def small_model(self) -> str:
        # If using a custom endpoint (Groq, xAI, etc.) keep the same model — it's
        # already a small/fast model chosen by the user.
        if self._base_url:
            return self.model
        return self._SMALL_MODEL_OPENAI

    def _get(self):
        if self._client is None:
            from openai import OpenAI
            kwargs = {"api_key": self._api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def _is_nonstandard_endpoint(self) -> bool:
        """Returns True when base_url points to a custom path (e.g. /v1/chat)
        that doesn't follow the standard /v1/chat/completions convention."""
        if not self._base_url:
            return False
        from urllib.parse import urlparse
        path = urlparse(self._base_url).path.rstrip("/")
        # Standard OpenAI-compat base paths end with /v1 or just the host root
        return path not in ("", "/v1", "/v1/") and not path.endswith("/v1")

    def _complete_direct(
        self,
        messages:  list,
        model:     str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Direct HTTP POST for non-standard endpoints (e.g. Hypereal /v1/chat).
        Handles both JSON and SSE streaming responses transparently."""
        import httpx, json as _json
        payload: dict = {"messages": messages, "max_tokens": max_tokens}
        if model:
            payload["model"] = model
        if temperature != 0:
            payload["temperature"] = temperature
        resp = httpx.post(
            self._base_url,  # type: ignore[arg-type]
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        raw = resp.text.strip()

        # SSE streaming response (lines starting with "data:")
        if "text/event-stream" in content_type or raw.startswith("data:"):
            chunks = []
            for line in raw.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload_str = line[len("data:"):].strip()
                if payload_str == "[DONE]":
                    break
                try:
                    chunk = _json.loads(payload_str)
                    # delta (streaming) or message (non-streaming) format
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    msg   = chunk.get("choices", [{}])[0].get("message", {})
                    chunks.append(delta.get("content") or msg.get("content") or "")
                except Exception:
                    pass
            return "".join(chunks)

        # Standard JSON response
        try:
            data = _json.loads(raw)
            return data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        except Exception:
            return raw

    def complete(
        self,
        prompt:         str,
        system_prompt:  Optional[str] = None,
        max_tokens:     int           = 4096,
        temperature:    float         = 0,
        model_override: Optional[str] = None,
    ) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        model = model_override or self.model

        # Non-standard endpoint (e.g. Hypereal /v1/chat) — bypass OpenAI SDK
        if self._is_nonstandard_endpoint():
            return self._complete_direct(messages, model, max_tokens, temperature)

        client = self._get()
        kwargs: dict = dict(
            model       = model,
            max_tokens  = max_tokens,
            messages    = messages,
            temperature = temperature,
        )

        response = client.chat.completions.create(**kwargs)
        if not response.choices:
            return ""
        # Record token usage
        usage = getattr(response, "usage", None)
        if usage:
            get_token_tracker().record(
                in_tok  = getattr(usage, "prompt_tokens", 0),
                out_tok = getattr(usage, "completion_tokens", 0),
                model   = model,
                phase   = "ai",
            )
        return response.choices[0].message.content or ""