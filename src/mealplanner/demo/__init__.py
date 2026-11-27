"""Demo mode: the real app with canned model answers, seeded fixture data and a fixed clock.

``python -m mealplanner.demo --port N --data-dir DIR --scenario NAME [--lifeline-stdin]`` runs
``create_app`` with a ``DemoLLMClient`` (never the bridge), the scenario's data and a fixed clock,
so every page works with no Claude CLI, no key and no outbound call (ADR 0028). It is an entry
point of its own, not a setting: no variable can switch a real run into demo mode or back.

Rule for later features: a feature that adds a page adds that page's scenario data
(``scenarios/*.json``, seeded through the feature package's own functions in ``build.py``) and the
canned answers for any model call it makes (``llm/<OutputModelClassName>.json``) in the same PR.
Everything here is fixture data only: no real names, logins or secrets.
"""

from datetime import UTC, datetime

DEMO_OWNER = "demo-owner@example.test"
# Monday 2026-01-05 12:00 UTC, timezone-aware.
FIXED_NOW = datetime(2026, 1, 5, 12, 0, 0, tzinfo=UTC)


class DemoError(ValueError):
    """Demo mode was asked for something it cannot do, for example an unknown scenario."""
