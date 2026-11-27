"""Content-free audit log: one JSON line per run, one per early refusal.

Only the fields below are ever logged. The input appears as its byte length and SHA-256; the
output, stderr, header values and the token never appear.
"""

import hashlib
import json
import logging
import sys

logger = logging.getLogger("bridge.audit")

CALLER = "app"


def configure_audit_logging() -> None:
    """Send `bridge.audit` INFO lines to stderr, bare JSON. Idempotent; called by the entry point.

    uvicorn's default log config touches only its own loggers, so without this the audit
    logger would inherit root's WARNING level and drop every line.
    """
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(getattr(h, "_bridge_audit", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler._bridge_audit = True  # type: ignore[attr-defined]
        logger.addHandler(handler)


def log_run(
    *, command: str, input_text: str, outcome: str, exit_status: int | None, duration_ms: int
) -> None:
    try:
        data = input_text.encode("utf-8")
    except UnicodeEncodeError:
        data = input_text.encode("utf-8", errors="surrogatepass")
    logger.info(
        json.dumps(
            {
                "event": "run",
                "command": command,
                "caller": CALLER,
                "input_bytes": len(data),
                "input_sha256": hashlib.sha256(data).hexdigest(),
                "outcome": outcome,
                "exit_status": exit_status,
                "duration_ms": duration_ms,
            }
        )
    )


def log_rejected(reason: str) -> None:
    logger.info(json.dumps({"event": "rejected", "reason": reason}))
