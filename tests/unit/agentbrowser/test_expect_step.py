"""The `expect` step kind (#154, ADR 0029): a locator that must be visible."""

from typing import Any

import pytest
from pydantic import ValidationError

from agentbrowser.flow import Flow, Step


def _step(**data: Any) -> Step:
    return Step.model_validate(data)


def test_expect_takes_a_role_locator() -> None:
    step = _step(expect={"role": "heading", "name": "Your profile"})
    assert step.kind == "expect"
    assert step.expect is not None and step.expect.role == "heading"


def test_expect_takes_label_and_text_locators() -> None:
    assert _step(expect={"label": "Servings"}).kind == "expect"
    assert _step(expect={"text": "Hello"}).kind == "expect"


def test_expect_rejects_a_css_locator() -> None:
    with pytest.raises(ValidationError):
        _step(expect={"css": "h1"})


def test_expect_rejects_a_value() -> None:
    with pytest.raises(ValidationError, match="expect takes no value"):
        _step(expect={"label": "Servings", "value": "2"})


def test_expect_rejects_a_half_locator() -> None:
    with pytest.raises(ValidationError):
        _step(expect={"role": "heading"})


def test_expect_and_another_kind_in_one_step_fails() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        _step(expect={"text": "a"}, expect_text="a")


def test_an_unknown_step_kind_still_fails() -> None:
    with pytest.raises(ValidationError):
        _step(hover={"text": "a"})


def test_a_flow_may_use_expect() -> None:
    flow = Flow.model_validate({"steps": [{"goto": "/"}, {"expect": {"text": "Weekly"}}]})
    assert [s.kind for s in flow.steps] == ["goto", "expect"]
