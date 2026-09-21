"""
OpenAI API client wrapper.
Handles both new SDK (>=1.0) and legacy SDK.
"""
from typing import Any

from src.core.logger import get_logger

logger = get_logger(__name__)


class OpenAIClient:
    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self.model = model
        self._client: Any = None
        self._mode: str = "unavailable"

        if not api_key:
            logger.warning("No OPENAI_API_KEY — AI enrichment disabled")
            return

        try:
            from openai import OpenAI
            self._client = OpenAI(api_key=api_key)
            self._mode = "new"
            logger.info(f"OpenAI initialized (new SDK) model={model}")
        except ImportError:
            try:
                import openai
                openai.api_key = api_key
                self._client = openai
                self._mode = "old"
                logger.info(f"OpenAI initialized (old SDK) model={model}")
            except ImportError:
                logger.warning("openai package not installed — AI enrichment disabled")

    @property
    def available(self) -> bool:
        return self._mode != "unavailable"

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.7,
        json_mode: bool = False,
    ) -> str:
        if not self.available:
            return ""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        import time as _t
        for attempt in range(5):
            try:
                if self._mode == "new":
                    kwargs: dict = dict(
                        model=self.model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                    if json_mode:
                        kwargs["response_format"] = {"type": "json_object"}
                    resp = self._client.chat.completions.create(**kwargs)
                    return resp.choices[0].message.content.strip()
                else:
                    resp = self._client.ChatCompletion.create(
                        model=self.model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                    return resp["choices"][0]["message"]["content"].strip()
            except Exception as exc:
                msg = str(exc).lower()
                transient = ("rate" in msg or "429" in msg or "timeout" in msg
                             or "overload" in msg or "503" in msg or "502" in msg)
                if transient and attempt < 4:
                    _t.sleep(5 * (attempt + 1))   # back off on rate limits
                    continue
                logger.error(f"OpenAI error: {exc}")
                return ""

    def chat_messages(
        self,
        messages: list,
        max_tokens: int = 700,
        temperature: float = 0.6,
        json_mode: bool = False,
    ) -> str:
        """Multi-turn chat: accepts a full messages list (system + history).

        Used by the customer-facing designer bot, which needs conversation
        memory rather than the single system+user shot that chat() gives.
        """
        if not self.available:
            return ""
        try:
            if self._mode == "new":
                kwargs: dict = dict(
                    model=self.model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                resp = self._client.chat.completions.create(**kwargs)
                return resp.choices[0].message.content.strip()
            else:
                resp = self._client.ChatCompletion.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                return resp["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.error(f"OpenAI chat_messages error: {exc}")
            return ""
