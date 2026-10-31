# ADR 0022: A loopback-only CLI bridge as a second backend behind the LLM client
- Status: Accepted
- Date: 2026-10-31
- Amends: ADR 0008 (adds a second implementation of the LLM client), ADR 0012 (adds a second loopback service)

## Context
Issues #112 (spec) and #113 (architecture). ADR 0008 makes the Anthropic SDK, called from the app's server process with `ANTHROPIC_API_KEY`, the only way the app reaches Claude. The owner wants a second way to make the same calls: through the locally installed Claude CLI, using the account it is already signed in with.

Running a CLI with text supplied by a caller is a powerful capability. Doing it inside the web process, which also serves pages to the phone over Tailscale (ADR 0012), would put process execution and the CLI's credentials next to the code that handles requests. A web page open in the owner's browser can also send requests to any `localhost` port, so binding to loopback alone is not protection.

Constitution principle 1 allows one outbound destination, the Claude API. The CLI's model calls go to that destination, but the CLI can also make other calls of its own, such as telemetry, error reporting, feature-flag fetching and update checks. So the principle holds only if the bridge switches that traffic off and a test keeps it off (see Decision, "No other outbound traffic"). With that condition this ADR does not amend the constitution; if some non-API traffic turns out to be unavoidable, that needs its own ADR amending principle 1 before the bridge ships.

## Decision
**A separate bridge process, same repository.**
- A new package, `bridge/`, with its own entry point, runs a small FastAPI/uvicorn server. It shares the virtualenv and adds no new third-party dependency (P8).
- `make run` starts the bridge, waits for its health check, then starts the app, and stops both if either exits or on interrupt. The supervisor is a small script in the repo; the plan defines it. A crash leaves no orphan.
- The port and the shared secret are chosen per run and passed to both processes through the environment (or a private file readable only by the owner; the plan chooses and shows it stays out of process listings and logs). They are never stored in the repository or in `.env.example` with values.

**Strictly local access.**
- The bridge binds to loopback only; `bridge` configuration rejects any other address at startup, with a test, as ADR 0012 does for the app.
- Every request needs `Authorization: Bearer <secret>`, compared in constant time. A missing or wrong secret gets 401.
- Host allowlist (`127.0.0.1`, `localhost`, the bridge's own port) against DNS rebinding. Requests carrying an `Origin` header get 403. No CORS headers. The body must be `application/json`, which forces a browser preflight that the bridge never answers.
- The bridge is never published with `tailscale serve` or `funnel`.
- Trusted: the host machine and any process running as the same OS user, as in ADR 0012. A local process of the same user can read the environment of the app, and this is accepted. Not defended against: compromise of the host account.

**Callers send data, never commands.**
- `POST /v1/run` takes `{ "command": "<name>", "input": "<text>" }`. The name must exist in a registry checked into the repository (`bridge/commands.toml`). An unknown name gets 404.
- Each registry entry fixes the absolute program path, the argument list, the timeout, the input and output byte caps, and the environment variables that may be passed through.
- The program is started with an argument list and no shell. The input goes on standard input, so it cannot be read as a flag or a program name. The one registered command is the Claude CLI, run non-interactively.

**Contained execution.**
- Timeout, then the whole process group is killed.
- Input and output size caps; output over the cap is an error, not a silent cut.
- A concurrency limit with a semaphore; over the limit gets 429.
- An empty scratch directory per run as the working directory, deleted afterwards.
- An explicit environment containing only what the CLI needs to be signed in. The bridge does not pass its own secret or the app's other settings to the child.
- **No other outbound traffic.** The registered command's environment turns off the CLI's non-API traffic: telemetry, error reporting, feature-flag fetching, and automatic updates. The plan verifies the exact variables and flags against the installed CLI's documentation. A test asserts that the registry's environment sets them, and fails if they are removed. If any of that traffic cannot be disabled, or the CLI cannot work with it off, the bridge does not ship until the owner accepts it in an ADR that amends principle 1.
- The registered CLI command disables the model's tools and file and shell access, so the output is text only. This matches ADR 0008 ("the model has no tools with side effects"). The exact flags are verified against the installed CLI's documentation in the plan, and a test fails if they are removed.

**The app side: one more implementation of the same protocol.**
- `CLIBridgeLLMClient` implements `LLMClient` (ADR 0008). It sends the system prompt, messages and the output JSON schema to the bridge, receives text, parses JSON, and validates with `output.model_validate`.
- **This reverses one rejection in ADR 0008.** ADR 0008 rejected "free-text JSON in the reply, parsed afterwards" because it causes more parse failures and gives the model no schema guidance. That holds for the API client, which keeps forced tool use. A text CLI has no forced tool use, so for this backend only, the schema is included in the prompt and the reply is parsed and validated in code. This is acceptable because the same validation and retry bound apply and invalid output is never rendered, and the cost is a possible extra CLI run.
- It reuses `run_attempts`: schema failures are fed back as an error message and retried, at most 3 attempts in total, then `LLMOutputError`. Forced tool use, which is how `AnthropicLLMClient` gets structure, is not available over a text CLI, so validation in code carries the guarantee. Principle 5 is unchanged: invalid output is retried or rejected, never rendered.
- Selection is `MEALPLANNER_LLM_BACKEND=api|bridge`, default `api`. The app factory injects the chosen client; feature code is unchanged. With `bridge` selected and the bridge unreachable, the app starts and reports the problem when a call is attempted, as ADR 0008 does for a missing key.
- Error mapping: timeout, refusal, 429, connection failure and non-zero exit become `LLMUnavailableError`; a missing or rejected secret, a CLI that is not installed or not signed in, become `LLMConfigError`. No error message, log line or exception text contains the secret or the input; tests assert it for every mapped error.
- `FakeLLMClient` remains the test double for feature code.

**Audit and privacy.** The bridge logs the command name, the caller, input size and SHA-256, exit status and duration. The caller is whichever token authenticated the request; with one shared secret there is one caller, recorded as a fixed label, so the field is ready for more. It never logs the input, the output or the secret.

**Tests (ADR 0010).** The bridge's tests use a stub program in `tmp_path` in place of the CLI and never start the real one. They cover: the loopback-only refusal, wrong token, bad Host, `Origin` rejection, unknown command, hostile input passed through unchanged (shell metacharacters, a leading `--flag`, embedded newlines), timeout with a child process killed, output cap, concurrency limit, minimal environment, and the client's retry and error mapping against a stub bridge.

## Alternatives considered
- **The app spawns the CLI directly, with no second process.** The simplest option, and enough for one user. Rejected because it puts process execution and the CLI's credentials in the process that serves requests from the phone, and gives no single place to enforce limits. The cost of the bridge is one more process to supervise; the owner chose that trade-off.
- **A Unix domain socket instead of loopback TCP.** Browsers cannot reach it and file permissions replace the token, which is stronger. Not available on Windows, and a little less convenient to test. Left to the plan to confirm for the operating systems in use; the token and checks above are the baseline either way.
- **A general "run any command" endpoint.** Rejected: the registry keeps callers to data.
- **Tool use or a CLI JSON-schema option for structure.** If the installed CLI offers a schema option, the plan may use it behind the same client; validation in code stays either way.
- **Replacing the API client.** Rejected: both stay, with `api` the default.

## Consequences
- The web process gains no ability to run programs and holds no CLI credentials. A bug in the bridge's request handling is the main new risk, and the tests above target it.
- One more process in `make run`, with a supervisor to maintain and test.
- Structure over the CLI is enforced by validation and retry rather than by forced tool use, so a model call may cost up to three CLI runs.
- Whether driving the CLI this way is allowed by the owner's Claude plan is an owner decision recorded on #112, not a technical one.
- The bridge has its own paths that should be protected (the registry decides what can be executed). The plan decides whether `.github/risk-paths` and CODEOWNERS list them.

## Constitution check
- P1 Local first: no outbound destination beyond Claude, provided the CLI's telemetry, error reporting, feature-flag fetching and update checks are switched off and a test keeps them off; the bridge listens on loopback only and is never published.
- P5 Structured outputs: schema validation with bounded retry; invalid output is never rendered.
- P7 Tests first: stub program and stub bridge; tests stay offline.
- P8 Simple over clever: reuses FastAPI and uvicorn; one registered command; no queue or storage.
- P9 Safe by default: allergy and exclusion enforcement stays in domain code (ADR 0008), not in the bridge.
- P10 One user first: one shared secret; no accounts.
