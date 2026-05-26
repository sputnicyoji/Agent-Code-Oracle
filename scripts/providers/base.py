"""LLM provider abstract base.

The round driver calls `provider.call(system, user, ...)` for Round 1,
Round 2, and Round 3. Each round's response is plain text (Round 1) or
a JSON array (Round 2/3). The provider's job is just to ferry one
request and return the response body. State, retries, and rate limits
are the provider's concern, not the driver's.

Concrete providers shipped today:

- DryRunProvider: prints the prompt + returns a deterministic stub.
  Lets users review the full driver flow without API access.

Provider implementations expected in future missions (Phase D full):

- ClaudeCodeProvider: delegates back to host Claude Code via a small
  JSON-RPC bridge (no API key needed; uses the user's existing session).
- AnthropicProvider, OpenAIProvider: standard SDK clients for CI use.
- OllamaProvider: local model for offline / cost-sensitive use.

The ABC is intentionally minimal -- one method, no constructor
contract. Providers add their own configuration in their `__init__`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal


ResponseFormat = Literal["text", "json"]


class LLMProvider(ABC):
    """Stateless interface for one-shot LLM dispatches.

    `call()` must be safe to invoke from multiple threads -- providers
    that need per-call state (retries, throttling) handle it internally.
    No implicit conversation: each call is independent.
    """

    @abstractmethod
    def call(
        self,
        system: str,
        user: str,
        response_format: ResponseFormat = "text",
        max_tokens: int = 8000,
        **kwargs,
    ) -> str:
        """Send one request, return the response body as a string.

        Args:
            system: System prompt. Provider may concatenate with user or
                handle separately depending on the underlying API.
            user: User message. Always required.
            response_format: "text" or "json". When "json", providers
                that support JSON mode should enable it; the returned
                string must still be a parseable JSON document.
            max_tokens: Output ceiling. Provider may interpret loosely
                (e.g. token vs character) but must respect order of
                magnitude.
            **kwargs: Provider-specific extras (model name, temperature,
                etc.). Unknown kwargs MUST be ignored, never raise.

        Returns:
            Response body as text. JSON mode returns the raw JSON string;
            caller is responsible for parsing.

        Raises:
            RuntimeError: when the provider fundamentally cannot fulfil
                the request (no credentials, network down, etc.). Bad
                model output (refusal, truncation) is the provider's
                business but should NOT raise -- return the body and
                let the driver decide.
        """
        ...
