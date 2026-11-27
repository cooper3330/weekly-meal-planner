"""Flow files for the agent browser (ADR 0028): pydantic models and a loader.

Plain Python on purpose: no Playwright import here, so flows are validated in ``make check`` and
an invalid flow is rejected before any browser starts.
"""

import json
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator

MAX_STEPS = 40
CRITERION = r"^AC-[0-9]+$"
VIEWPORT = r"^[1-9][0-9]*x[1-9][0-9]*$"
STEP_KINDS = ("goto", "click", "fill", "select", "press", "expect_text", "expect_no_text")


class FlowError(ValueError):
    """A flow file is missing, not JSON, or does not match the schema."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Proves(_Strict):
    issue: StrictInt = Field(gt=0)
    criteria: list[Annotated[str, Field(pattern=CRITERION)]] = Field(min_length=1)


class Locator(_Strict):
    """An element by accessible role and name, by label, or by visible text; never CSS."""

    role: str | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, min_length=1)
    label: str | None = Field(default=None, min_length=1)
    text: str | None = Field(default=None, min_length=1)
    value: str | None = None  # fill and select only; checked by Step

    @model_validator(mode="after")
    def _one_form(self) -> Self:
        by_role = self.role is not None or self.name is not None
        forms = [by_role, self.label is not None, self.text is not None]
        if sum(forms) != 1:
            raise ValueError("a locator is exactly one of {role, name}, {label} or {text}")
        if by_role and (self.role is None or self.name is None):
            raise ValueError("a role locator needs both role and name")
        return self

    def describe(self) -> str:
        if self.role is not None:
            return f"{self.role} named {self.name!r}"
        if self.label is not None:
            return f"field labelled {self.label!r}"
        return f"text {self.text!r}"


class Step(_Strict):
    goto: str | None = None
    click: Locator | None = None
    fill: Locator | None = None
    select: Locator | None = None
    press: str | None = Field(default=None, min_length=1)
    expect_text: str | None = Field(default=None, min_length=1)
    expect_no_text: str | None = Field(default=None, min_length=1)
    shows: str | None = Field(default=None, pattern=CRITERION)

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        kinds = [k for k in STEP_KINDS if getattr(self, k) is not None]
        if len(kinds) != 1:
            raise ValueError(f"a step has exactly one of {', '.join(STEP_KINDS)}; got {kinds}")
        kind = kinds[0]
        if kind == "goto":
            path = self.goto or ""
            if not path.startswith("/") or path.startswith("//") or "\\" in path:
                raise ValueError(f"goto must be a path starting with a single '/', got {path!r}")
        if kind in ("fill", "select"):
            locator = getattr(self, kind)
            if locator.value is None:
                raise ValueError(f"{kind} needs a value")
        elif kind == "click" and self.click is not None and self.click.value is not None:
            raise ValueError("click takes no value")
        return self

    @property
    def kind(self) -> str:
        return next(k for k in STEP_KINDS if getattr(self, k) is not None)


class Flow(_Strict):
    proves: list[Proves] = Field(default_factory=list)
    mode: Literal["demo", "wired"] = "demo"
    scenario: str = Field(default="profile", min_length=1)
    viewport: str = Field(default="360x640", pattern=VIEWPORT)
    steps: list[Step] = Field(min_length=1, max_length=MAX_STEPS)

    @property
    def width(self) -> int:
        return int(self.viewport.split("x")[0])

    @property
    def height(self) -> int:
        return int(self.viewport.split("x")[1])


def _format(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"]) or "(flow)"
        lines.append(f"{loc}: {err['msg']}")
    return "\n".join(lines)


def load(path: Path, strict: bool = False) -> Flow:
    """Read and validate a flow file. ``strict`` (tests/flows/) requires ``proves``."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise FlowError(f"{path}: cannot read flow file: {exc.strerror or exc}") from exc
    except ValueError as exc:
        raise FlowError(f"{path}: not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise FlowError(f"{path}: a flow must be a JSON object")
    data: dict[str, Any] = raw
    try:
        flow = Flow.model_validate(data)
    except ValidationError as exc:
        raise FlowError(f"{path}: invalid flow\n{_format(exc)}") from exc
    if strict and not flow.proves:
        raise FlowError(f"{path}: invalid flow\nproves: required for flows in tests/flows/")
    return flow
