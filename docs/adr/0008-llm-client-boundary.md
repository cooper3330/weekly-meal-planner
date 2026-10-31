# ADR 0008: Claude via forced tool use, pydantic validation, bounded retry, one mockable client
- Status: Accepted
- Date: 2026-10-24
- Amended by: ADR 0022 (adds a second `LLMClient` implementation that calls the Claude CLI through a local bridge; the API client remains the default; for that backend only, it reverses the rejection of free-text JSON parsed afterwards)

## Context
Issue #5, decision 5: how the local app calls the Claude API. Constitution principle 5 requires schema-validated output that is retried or rejected and never rendered when invalid. Principle 7 requires model calls to be mockable so tests never touch the network. The API key comes only from the environment.

## Decision
- **Where calls happen:** only in the server process, using the official `anthropic` Python SDK. The browser never sees the key or calls the API.
- **Configuration (environment only):**
  - `ANTHROPIC_API_KEY`: required to make a real call. A missing key is reported as a clear error when a call is attempted, not at import, so the app and tests start without it.
  - `MEALPLANNER_MODEL`: model id, with a default in `config.py`.
  - Locally, `.env` (gitignored) is loaded with `uv run --env-file .env`. `.env.example` lists the variables with no values.
- **One boundary, one method:**
  ```python
  class LLMClient(Protocol):
      def generate(self, *, system: str, messages: list[Message],
                   output: type[T], max_tokens: int) -> T: ...
  ```
  `T` is a pydantic model with `extra="forbid"`. Feature code depends only on this protocol, and the app factory injects the implementation.
- **Real implementation (`AnthropicLLMClient`):**
  - Defines a single tool whose `input_schema` is `output.model_json_schema()` and forces it with `tool_choice`.
  - Validates the `tool_use` input with `output.model_validate`.
  - If the tool call is missing or validation fails, it retries with the validation errors returned as an error `tool_result`, so the model can correct itself. At most **3 attempts in total**, after which it raises `LLMOutputError`. No partial output is returned.
  - Transport errors use the SDK's built-in retries (2); after that it raises `LLMUnavailableError`, which the UI shows as a friendly "try again" message.
  - Large, stable prompt parts (for example the ingredient catalog id list, ADR 0007) are marked for prompt caching.
- **Fake implementation (`FakeLLMClient`):** constructed with a queue of scripted responses (dicts to validate, or exceptions to raise). It records every call and runs the same validation path as the real client, so retry and rejection logic is tested offline. It lives in the package so tests and local demo runs can use it.
- **Two validation layers:**
  1. Schema validation in the client (shape, types, and closed vocabularies such as catalog ingredient ids, ADR 0007). A failure here is retried, so the model can correct it.
  2. Domain validation in feature code: catalog resolution, nutrition computation and checks (ADR 0007), and exclusion filtering.
  
  Domain failures never reach the UI. Each feature plan defines its own bounded replacement policy.
- **Prompts:** live as text files next to the feature that uses them, assembled in code from explicit, user-viewable data (profile, learned preferences, ADR 0009). The model has no tools with side effects.

## Alternatives considered
- **Free-text JSON in the reply, parsed afterwards.** More parse failures and no schema guidance to the model. Rejected.
- **The API's JSON-schema structured-output mode instead of a forced tool.** A reasonable equivalent. Forced tool use is well established and works with the same pydantic schema; switching later is an implementation detail behind the protocol, since validation in code stays either way.
- **Recorded HTTP cassettes (VCR-style) for tests.** Adds a dependency and couples tests to wire format. A fake at our own protocol is simpler.
- **Calling the API from the browser.** Exposes the key. Rejected.

## Consequences
- Every model call is typed, validated, bounded, and replaceable in tests by one constructor argument.
- A failed generation costs at most three calls per request before the user sees an error.
- API spend is the user's; setting a monthly spend limit in the Anthropic console is recommended (raised on #5).

## Constitution check
- P1 Local first: the Claude API is the only outbound call, made from the local server.
- P5 Structured outputs: forced tool use, pydantic validation, bounded retry, reject on failure.
- P7 Tests first: the fake client makes every model interaction testable offline.
- P2/P9: numbers and exclusions are enforced in the domain layer, not trusted from the model.
