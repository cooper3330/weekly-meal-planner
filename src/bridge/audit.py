"""Content-free audit log: one JSON line per run, one per early refusal.

Only the fields below are ever logged. The input appears as its byte length and SHA-256; the
output, stderr, header values and the token never appear.
"""

import hashlib
import json
import logging

logger = logging.getLogger("bridge.audit")

CALLER = "app"


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
