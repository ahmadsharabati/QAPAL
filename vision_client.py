"""
vision_client.py — QAPal Vision-Language Model Client
======================================================
Wraps vision-capable AI models (Claude, GPT-4o, Gemini) for screenshot
analysis.  Sits alongside ai_client.py but specialises in multimodal
(image + text) queries.

The hybrid approach:
  1. Crawler extracts DOM/a11y data  (free, fast)
  2. Vision model evaluates layout, visual quality, UX  (targeted, expensive)
  → 5-10 vision calls per session instead of 50+

Usage:
    vc = VisionClient.from_env()
    findings = vc.analyze_screenshot(screenshot_bytes, prompt)
    findings = await vc.aanalyze_screenshot(screenshot_bytes, prompt)

Env vars:
    QAPAL_VISION_PROVIDER  — anthropic | openai  (default: inherits QAPAL_AI_PROVIDER)
    QAPAL_VISION_MODEL     — model override       (default: provider best-vision model)
"""

import asyncio
import base64
import os
from typing import Optional

from _log import get_logger
from _tokens import get_token_tracker

_log = get_logger("vision_client")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class VisionClient:
    """
    Multimodal AI client for screenshot analysis.
    Supports Anthropic Claude (vision) and OpenAI GPT-4o.
    """

    def __init__(self, provider: str, model: str):
        self.provider = provider
        self.model    = model

    # ── Sync ──────────────────────────────────────────────────────────

    def analyze_screenshot(
        self,
        screenshot:     bytes,
        prompt:         str,
        system_prompt:  Optional[str] = None,
        max_tokens:     int           = 4096,
        temperature:    float         = 0,
    ) -> str:
        """Analyze a single screenshot with a text prompt. Returns raw text."""
        raise NotImplementedError(
            f"{type(self).__name__}.analyze_screenshot() not implemented. "
            "Use VisionClient.from_env() to get a provider-specific client."
        )

    def analyze_multi(
        self,
        screenshots:    list[bytes],
        prompt:         str,
        system_prompt:  Optional[str] = None,
        max_tokens:     int           = 4096,
        temperature:    float         = 0,
    ) -> str:
        """Analyze multiple screenshots in a single call (e.g. before/after)."""
        raise NotImplementedError(
            f"{type(self).__name__}.analyze_multi() not implemented. "
            "Use VisionClient.from_env() to get a provider-specific client."
        )

    # ── Async wrapper ─────────────────────────────────────────────────

    async def aanalyze_screenshot(
        self,
        screenshot:     bytes,
        prompt:         str,
        system_prompt:  Optional[str] = None,
        max_tokens:     int           = 4096,
        temperature:    float         = 0,
    ) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.analyze_screenshot(
                screenshot, prompt, system_prompt, max_tokens, temperature
            ),
        )

    async def aanalyze_multi(
        self,
        screenshots:    list[bytes],
        prompt:         str,
        system_prompt:  Optional[str] = None,
        max_tokens:     int           = 4096,
        temperature:    float         = 0,
    ) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.analyze_multi(
                screenshots, prompt, system_prompt, max_tokens, temperature
            ),
        )

    # ── Factory ──────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "VisionClient":
        provider = (
            os.getenv("QAPAL_VISION_PROVIDER", "").strip()
            or os.getenv("QAPAL_AI_PROVIDER", "anthropic")
        ).lower()
        model = os.getenv("QAPAL_VISION_MODEL", "").strip() or None

        if provider == "anthropic":
            api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
            if not api_key:
                raise EnvironmentError("ANTHROPIC_API_KEY is not set.")
            return _AnthropicVision(api_key=api_key, model=model or "claude-sonnet-4-6")

        if provider in ("openai", "grok", "xai"):
            if provider in ("grok", "xai"):
                api_key  = os.getenv("XAI_API_KEY", "").strip() or os.getenv("GROK_API_KEY", "").strip()
                base_url = os.getenv("QAPAL_AI_BASE_URL", "").strip() or "https://api.x.ai/v1"
                default  = model or "grok-2-vision-latest"
            else:
                api_key  = os.getenv("OPENAI_API_KEY", "").strip()
                base_url = os.getenv("QAPAL_AI_BASE_URL", "").strip() or None
                default  = model or "gpt-4o"
            if not api_key:
                key_name = "XAI_API_KEY" if provider in ("grok", "xai") else "OPENAI_API_KEY"
                raise EnvironmentError(f"{key_name} is not set.")
            return _OpenAIVision(api_key=api_key, model=default, base_url=base_url)

        raise ValueError(f"Unknown vision provider: '{provider}'. Valid: anthropic, openai")


# ── Anthropic Vision ─────────────────────────────────────────────────

class _AnthropicVision(VisionClient):

    def __init__(self, api_key: str, model: str):
        super().__init__("anthropic", model)
        self._api_key = api_key
        self._client  = None

    def _get(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def _build_image_block(self, screenshot: bytes) -> dict:
        return {
            "type": "image",
            "source": {
                "type":       "base64",
                "media_type": "image/png",
                "data":       base64.b64encode(screenshot).decode("ascii"),
            },
        }

    def analyze_screenshot(
        self,
        screenshot:     bytes,
        prompt:         str,
        system_prompt:  Optional[str] = None,
        max_tokens:     int           = 4096,
        temperature:    float         = 0,
    ) -> str:
        return self.analyze_multi([screenshot], prompt, system_prompt, max_tokens, temperature)

    def analyze_multi(
        self,
        screenshots:    list[bytes],
        prompt:         str,
        system_prompt:  Optional[str] = None,
        max_tokens:     int           = 4096,
        temperature:    float         = 0,
    ) -> str:
        client = self._get()
        content = [self._build_image_block(s) for s in screenshots]
        content.append({"type": "text", "text": prompt})

        kwargs = {
            "model":       self.model,
            "max_tokens":  max_tokens,
            "messages":    [{"role": "user", "content": content}],
            "temperature": temperature,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        response = client.messages.create(**kwargs)
        if not response.content:
            return ""
        usage = getattr(response, "usage", None)
        if usage:
            get_token_tracker().record(
                in_tok     = getattr(usage, "input_tokens", 0),
                out_tok    = getattr(usage, "output_tokens", 0),
                cache_read = getattr(usage, "cache_read_input_tokens", 0),
                model      = kwargs.get("model", self.model),
                phase      = "vision",
            )
        return response.content[0].text


# ── OpenAI Vision ────────────────────────────────────────────────────

class _OpenAIVision(VisionClient):

    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        super().__init__("openai", model)
        self._api_key  = api_key
        self._base_url = base_url
        self._client   = None

    def _get(self):
        if self._client is None:
            from openai import OpenAI
            kwargs = {"api_key": self._api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def _build_image_content(self, screenshot: bytes) -> dict:
        b64 = base64.b64encode(screenshot).decode("ascii")
        return {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"},
        }

    def analyze_screenshot(
        self,
        screenshot:     bytes,
        prompt:         str,
        system_prompt:  Optional[str] = None,
        max_tokens:     int           = 4096,
        temperature:    float         = 0,
    ) -> str:
        return self.analyze_multi([screenshot], prompt, system_prompt, max_tokens, temperature)

    def analyze_multi(
        self,
        screenshots:    list[bytes],
        prompt:         str,
        system_prompt:  Optional[str] = None,
        max_tokens:     int           = 4096,
        temperature:    float         = 0,
    ) -> str:
        client = self._get()
        content = [self._build_image_content(s) for s in screenshots]
        content.append({"type": "text", "text": prompt})

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content})

        response = client.chat.completions.create(
            model       = self.model,
            max_tokens  = max_tokens,
            messages    = messages,
            temperature = temperature,
        )
        if not response.choices:
            return ""
        usage = getattr(response, "usage", None)
        if usage:
            get_token_tracker().record(
                in_tok  = getattr(usage, "prompt_tokens", 0),
                out_tok = getattr(usage, "completion_tokens", 0),
                model   = self.model,
                phase   = "vision",
            )
        return response.choices[0].message.content or ""
