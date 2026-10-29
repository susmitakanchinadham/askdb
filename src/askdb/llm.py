"""LLM client boundary (SPEC 10). The client only ever sees text: no result rows can reach it.

`FakeLLMClient` serves the offline tests; `AnthropicClient` is the opt-in live client. The API key
comes from the environment and is never read from files.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Protocol

DEFAULT_MODEL = "claude-sonnet-5-5"  # pinned (SPEC 10); change through the evaluation process
API_URL = "https://api.anthropic.com/v1/messages"
REQUEST_TIMEOUT_SECONDS = 60


class LLMUnavailable(Exception):
    """Provider outage, rate limit, bad credentials or an unreadable reply. Never retried."""


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class FakeLLMClient:
    """Returns queued replies (or calls a function) and records every prompt it was given."""

    def __init__(self, replies: list[str] | Callable[[str, str], str]):
        self._replies = replies
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if callable(self._replies):
            return self._replies(system, user)
        if not self._replies:
            raise LLMUnavailable("fake client has no more replies")
        return self._replies.pop(0)


class AnthropicClient:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model or os.environ.get("ASKDB_MODEL", DEFAULT_MODEL)

    def complete(self, system: str, user: str) -> str:
        if not self.api_key:
            raise LLMUnavailable("no API key configured")
        body = json.dumps(
            {
                "model": self.model,
                "max_tokens": 1024,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }
        ).encode()
        request = urllib.request.Request(
            API_URL,
            data=body,
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.load(response)
            return "".join(b["text"] for b in payload["content"] if b.get("type") == "text")
        except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as exc:
            raise LLMUnavailable(type(exc).__name__) from exc
