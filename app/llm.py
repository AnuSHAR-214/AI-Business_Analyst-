"""
Pluggable LLM layer.

Three providers, one interface:
  * anthropic  - Claude, via the `anthropic` SDK
  * openai     - GPT, via the `openai` SDK
  * offline    - no network, no key: deterministic template engine

The offline provider is not a toy. It is what makes this project demoable on a
laptop with no API key and what makes the test suite deterministic. Everything
downstream (analysis, charting, insights) is provider-agnostic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from .config import settings


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str


class LLMBackend(Protocol):
    name: str
    model: str

    def complete(self, system: str, user: str, max_tokens: int = 1200) -> str: ...


# --------------------------------------------------------------------------- #
class AnthropicBackend:
    name = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        import anthropic  # imported lazily so the package stays optional

        self.model = model
        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(self, system: str, user: str, max_tokens: int = 1200) -> str:
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if b.type == "text").strip()


class OpenAIBackend:
    name = "openai"

    def __init__(self, api_key: str, model: str) -> None:
        from openai import OpenAI

        self.model = model
        self._client = OpenAI(api_key=api_key)

    def complete(self, system: str, user: str, max_tokens: int = 1200) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return (resp.choices[0].message.content or "").strip()


class OfflineBackend:
    """
    Deterministic stand-in.

    It never sees this system prompt as "instructions" - the caller decides what
    to do when `provider == 'offline'`. `complete()` exists only so callers that
    genuinely just want prose have something to call; SQL generation and insight
    writing both have dedicated offline paths (see sql_agent.py / insights.py).
    """

    name = "offline"
    model = "rule-based"

    def complete(self, system: str, user: str, max_tokens: int = 1200) -> str:
        raise NotImplementedError(
            "Offline mode has no free-form completion; callers must branch on "
            "llm.provider == 'offline'."
        )


# --------------------------------------------------------------------------- #
PREFIXES = {"openai": "sk-", "anthropic": "sk-ant-"}


def key_problem(provider: str, key: str) -> str | None:
    """
    Catch a malformed key before we spend a network round-trip on it.

    This exists because the most common setup mistake isn't a *wrong* key, it's
    something that was never a key at all - a shell command, a quoted string, or
    the placeholder text - pasted into .env. Those produce a confusing 401 much
    later; caught here they produce a sentence that says what to fix.
    """
    k = key.strip()

    if " " in k:
        return (f"the value looks like a command or sentence, not a key "
                f"(it contains spaces): {k[:40]!r}. Put ONLY the key after "
                f"'=' in .env - shell commands belong in a terminal.")
    if k[:1] in {'"', "'"} or k[-1:] in {'"', "'"}:
        return "remove the surrounding quotes from the key in .env"
    if k.lower().startswith(("your", "paste", "<", "xxx", "sk-proj-xxx")):
        return "the placeholder text is still there - paste your real key"

    prefix = PREFIXES.get(provider)
    if prefix and not k.startswith(prefix):
        return (f"a {provider} key should start with {prefix!r}, but this "
                f"starts with {k[:8]!r}")
    if len(k) < 20:
        return f"the key looks truncated ({len(k)} characters)"
    return None


class LLM:
    """Facade used by the rest of the app."""

    def __init__(self, provider: str | None = None) -> None:
        # `reasons` explains every backend that was tried and why it was not
        # used. Without this, a missing package or a bad key looks identical to
        # "no key configured", which is a miserable thing to debug.
        self.reasons: list[str] = []
        self.backend = self._select(provider or settings.llm_provider)

    def _select(self, provider: str) -> LLMBackend:
        provider = (provider or "auto").lower()

        def attempt(name: str, key: str, factory) -> LLMBackend | None:
            if not key:
                self.reasons.append(f"{name}: no API key set")
                return None
            problem = key_problem(name, key)
            if problem:
                self.reasons.append(f"{name}: {problem}")
                return None
            try:
                backend = factory()
                self.reasons.append(f"{name}: client created")
                return backend
            except ImportError as exc:
                self.reasons.append(
                    f"{name}: package not installed or unusable - {exc}")
            except Exception as exc:
                self.reasons.append(f"{name}: {type(exc).__name__}: {exc}")
            return None

        def try_anthropic():
            key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            return attempt("anthropic", key,
                           lambda: AnthropicBackend(key, settings.anthropic_model))

        def try_openai():
            key = settings.openai_api_key or os.environ.get("OPENAI_API_KEY", "")
            return attempt("openai", key,
                           lambda: OpenAIBackend(key, settings.openai_model))

        if provider == "anthropic":
            chosen = try_anthropic()
        elif provider == "openai":
            chosen = try_openai()
        elif provider == "offline":
            self.reasons.append("offline: requested explicitly")
            return OfflineBackend()
        else:
            chosen = try_anthropic() or try_openai()

        if chosen is None:
            self.reasons.append("falling back to the offline rule engine")
            return OfflineBackend()
        return chosen

    @property
    def why(self) -> str:
        """Human-readable explanation of the provider selection."""
        return "; ".join(self.reasons)

    # -- passthrough -------------------------------------------------------
    @property
    def provider(self) -> str:
        return self.backend.name

    @property
    def model(self) -> str:
        return self.backend.model

    @property
    def is_offline(self) -> bool:
        return self.backend.name == "offline"

    def complete(self, system: str, user: str, max_tokens: int = 1200) -> LLMResponse:
        text = self.backend.complete(system, user, max_tokens)
        return LLMResponse(text=text, provider=self.provider, model=self.model)
