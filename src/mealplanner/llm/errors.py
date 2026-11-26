"""Errors raised by the LLM boundary. Messages never contain the bridge token."""


class LLMError(Exception):
    """Base class for LLM boundary errors."""


class LLMOutputError(LLMError):
    """The model never produced valid output within the attempt bound."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors: list[str] = list(errors or [])


class LLMUnavailableError(LLMError):
    """The bridge or the model could not be reached or is overloaded."""


class LLMConfigError(LLMError):
    """The client is not configured, for example the bridge port or token is missing."""
