"""Errors raised by the LLM boundary. Messages never contain the API key."""


class LLMError(Exception):
    """Base class for LLM boundary errors."""


class LLMOutputError(LLMError):
    """The model never produced valid output within the attempt bound."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors: list[str] = list(errors or [])


class LLMUnavailableError(LLMError):
    """The Claude API could not be reached or is overloaded (after SDK retries)."""


class LLMConfigError(LLMError):
    """The client is not configured, for example ANTHROPIC_API_KEY is missing."""
