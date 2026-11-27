"""The feature map (ADR 0029): models, loader, route list, neighbours and the walk's flow.

Plain Python on purpose, like ``flow``: no Playwright import, so ``make check`` validates the map
and an invalid map is rejected before any browser starts. Steps and locators are ADR 0028's own
types, so there is one parser for flows, the route check and the walk.
"""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from starlette.routing import Mount

from agentbrowser.flow import Flow, Locator, Proves, Step
from mealplanner.demo.build import scenario_names

REPO_ROOT = Path(__file__).resolve().parents[2]
FLOWS_DIR = REPO_ROOT / "tests" / "flows"
WALK_VIEWPORT = "360x640"

KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ROUTE = re.compile(r"^[A-Z]+ /\S*$")
SHARED_LAYOUT = {"base.html", "app.css"}
NAVIGATION = ("click", "fill", "select", "press")


class FeatureMapError(ValueError):
    """The map is missing, not JSON, or breaks a rule; ``errors`` lists every problem found."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = errors


class UnknownRouteError(AssertionError):
    """The app has a kind of route the route check does not know how to name."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InternalRoute(_Strict):
    route: str
    why: str = Field(min_length=1)

    @field_validator("route")
    @classmethod
    def _route_shape(cls, value: str) -> str:
        if not ROUTE.match(value):
            raise ValueError("route must look like 'METHOD /path'")
        return value


class Action(_Strict):
    control: Locator
    result: str = Field(min_length=1)

    @model_validator(mode="after")
    def _no_value(self) -> Self:
        if self.control.value is not None:
            raise ValueError("a control locator takes no value")
        return self


class Feature(_Strict):
    name: str
    routes: list[str] = Field(min_length=1)
    scenario: str = Field(min_length=1)
    reach: list[Step] = Field(min_length=1)
    landmarks: list[Locator] = Field(min_length=1)
    actions: list[Action]
    implements: list[Proves]
    flows: list[str]
    sources: list[str] = Field(min_length=1)
    phone_notes: str

    @field_validator("name")
    @classmethod
    def _kebab(cls, value: str) -> str:
        if not KEBAB.match(value):
            raise ValueError("name must be kebab-case")
        return value

    @field_validator("routes")
    @classmethod
    def _route_shapes(cls, value: list[str]) -> list[str]:
        bad = [r for r in value if not ROUTE.match(r)]
        if bad:
            raise ValueError(f"routes must look like 'METHOD /path', got {bad}")
        return value

    @model_validator(mode="after")
    def _rules(self) -> Self:
        problems: list[str] = []
        if not any(r.startswith("GET ") for r in self.routes):
            problems.append("routes needs at least one GET route (the page)")
        first = self.reach[0]
        if first.kind != "goto" or first.goto != "/":
            problems.append('reach must start with {"goto": "/"}')
        for i, step in enumerate(self.reach[1:], 1):
            if step.kind == "goto":
                problems.append(f"reach[{i}]: no other goto is allowed in reach")
            elif step.kind not in NAVIGATION:
                problems.append(
                    f"reach[{i}]: reach steps must be {', '.join(NAVIGATION)}, not {step.kind}"
                )
        for i, loc in enumerate(self.landmarks):
            if loc.value is not None:
                problems.append(f"landmarks[{i}]: a landmark takes no value")
        for src in self.sources:
            if Path(src).name in SHARED_LAYOUT:
                problems.append(f"sources must not list the shared layout file {src}")
        if problems:
            raise ValueError("; ".join(problems))
        return self


class FeatureMap(_Strict):
    internal: list[InternalRoute]
    features: list[Feature]


# --- loading -------------------------------------------------------------------------------


def _place(loc: tuple[int | str, ...]) -> str:
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


def _format(exc: ValidationError, data: Any) -> list[str]:
    lines: list[str] = []
    for err in exc.errors():
        loc = err["loc"]
        msg = err["msg"].removeprefix("Value error, ")
        given = err.get("input")
        if (
            err["type"] != "missing"
            and isinstance(given, str | int)
            and not isinstance(given, bool)
        ):
            msg = f"{msg} (got {given!r})"
        if len(loc) >= 2 and loc[0] == "features" and isinstance(loc[1], int):
            try:
                name = data["features"][loc[1]]["name"]
            except (KeyError, IndexError, TypeError):
                name = "<unnamed>"
            where = _place(loc[2:])
            lines.append(f"{name}: {where + ': ' if where else ''}{msg}")
        else:
            lines.append(f"map: {_place(loc) + ': ' if loc else ''}{msg}")
    return lines


def parse(
    data: Any,
    *,
    root: Path = REPO_ROOT,
    flows_dir: Path = FLOWS_DIR,
    scenarios: Iterable[str] | None = None,
) -> FeatureMap:
    """Validate map data against every ADR 0029 rule that needs no running app.

    ``root`` is where ``sources`` are looked up, ``flows_dir`` where ``flows`` are, ``scenarios``
    the demo scenario names (default: the real ones). Raises ``FeatureMapError`` with every
    problem found.
    """
    if not isinstance(data, dict):
        raise FeatureMapError(["map: must be an object"])
    try:
        fmap = FeatureMap.model_validate(data)
    except ValidationError as exc:
        raise FeatureMapError(_format(exc, data)) from exc

    known = set(scenario_names() if scenarios is None else scenarios)
    errors: list[str] = []
    seen: set[str] = set()
    for feature in fmap.features:
        name = feature.name
        if name in seen:
            errors.append(f"{name}: duplicate name")
        seen.add(name)
        if feature.scenario not in known:
            errors.append(f"{name}: scenario {feature.scenario!r} does not exist")
        for flow in feature.flows:
            if not (flows_dir / f"{flow}.json").is_file():
                errors.append(f"{name}: flows: no file tests/flows/{flow}.json")
        for src in feature.sources:
            if not (root / src).is_file():
                errors.append(f"{name}: sources: {src} does not exist")
    if errors:
        raise FeatureMapError(errors)
    return fmap


def load(path: Path, **context: Any) -> FeatureMap:
    """Read and validate a map file (see ``parse`` for ``context``)."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise FeatureMapError([f"{path}: cannot read feature map: {exc.strerror or exc}"]) from exc
    except ValueError as exc:
        raise FeatureMapError([f"{path}: not valid JSON: {exc}"]) from exc
    try:
        return parse(raw, **context)
    except FeatureMapError as exc:
        raise FeatureMapError([f"{path}: invalid feature map", *exc.errors]) from exc


# --- the app's routes ----------------------------------------------------------------------


def app_routes(app: FastAPI) -> list[str]:
    """``"METHOD path"`` per API route method (HEAD ignored) and ``"MOUNT path"`` per mount."""
    # FastAPI keeps included routers lazy in app.routes; iter_route_contexts flattens them and
    # gives each route's full path.
    out: list[str] = []
    for ctx in iter_route_contexts(app.routes):
        route = ctx.original_route
        if isinstance(route, APIRoute):
            out.extend(f"{m} {ctx.path}" for m in sorted(route.methods or ()) if m != "HEAD")
        elif isinstance(route, Mount):
            out.append(f"MOUNT {ctx.path}")
        else:
            raise UnknownRouteError(f"Unknown route type {type(route).__name__}: {route!r}")
    return out


def route_errors(fmap: FeatureMap, routes: list[str]) -> list[str]:
    """Each app route is in exactly one entry or in ``internal``; each listed route exists."""
    owners: dict[str, list[str]] = {}
    for item in fmap.internal:
        owners.setdefault(item.route, []).append("internal")
    for feature in fmap.features:
        for route in feature.routes:
            owners.setdefault(route, []).append(feature.name)
    have = set(routes)
    errors = [f"route {r} is in no entry and not in internal" for r in sorted(have - set(owners))]
    for route, who in sorted(owners.items()):
        if route not in have:
            errors.append(f"{who[0]}: route {route} does not exist in the app")
        if len(who) > 1:
            errors.append(f"route {route} is listed more than once: {', '.join(who)}")
    return errors


# --- neighbours ----------------------------------------------------------------------------


def _key(feature: Feature) -> list[str]:
    """The reach steps as comparable values: equal steps are equal, whatever their label."""
    return [
        json.dumps(s.model_dump(exclude_none=True, exclude={"shows"}), sort_keys=True)
        for s in feature.reach
    ]


def _parent(fmap: FeatureMap, feature: Feature) -> Feature | None:
    mine = _key(feature)
    best: Feature | None = None
    for other in sorted(fmap.features, key=lambda f: f.name):
        theirs = _key(other)
        if len(theirs) < len(mine) and mine[: len(theirs)] == theirs:
            if best is None or len(theirs) > len(best.reach):
                best = other
    return best


def neighbours(fmap: FeatureMap, name: str) -> list[str]:
    """The parent, then the children: entries whose longest proper reach prefix is this one."""
    me = next((f for f in fmap.features if f.name == name), None)
    if me is None:
        raise KeyError(name)
    parent = _parent(fmap, me)
    children = sorted(
        f.name for f in fmap.features if (p := _parent(fmap, f)) is not None and p.name == name
    )
    return ([parent.name] if parent else []) + children


# --- the walk ------------------------------------------------------------------------------


@dataclass(frozen=True)
class PathCheck:
    """After step ``after_step`` (1-based) the page path must match one of ``templates``."""

    after_step: int
    templates: list[str]


def path_matches(path: str, templates: list[str]) -> bool:
    """A template segment ``{param}`` matches exactly one non-empty path segment."""
    parts = path.split("/")
    for template in templates:
        want = template.split("/")
        if len(want) != len(parts):
            continue
        if all(
            (w.startswith("{") and w.endswith("}") and bool(p)) or w == p
            for w, p in zip(want, parts, strict=True)
        ):
            return True
    return False


def path_check(feature: Feature) -> PathCheck:
    templates = [r.split(" ", 1)[1] for r in feature.routes if r.startswith("GET ")]
    return PathCheck(after_step=len(feature.reach), templates=templates)


def to_walk_flow(feature: Feature) -> Flow:
    """Demo mode, the entry's scenario, a phone viewport, its reach steps, then its landmarks."""
    return Flow(
        mode="demo",
        scenario=feature.scenario,
        viewport=WALK_VIEWPORT,
        steps=[*feature.reach, *(Step(expect=loc) for loc in feature.landmarks)],
    )
