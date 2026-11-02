"""Profile pages: view, edit form, and save (POST-redirect-GET)."""

import sqlite3
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from mealplanner.nutrition.exclusions import ALLERGENS, DIET_TERMS
from mealplanner.profile.forms import SKILLS, FormErrors, parse_profile_form
from mealplanner.profile.models import DEFAULT_PROFILE, PREFERENCES_MAX_CHARS, Profile
from mealplanner.profile.store import (
    get_current_profile,
    get_profile_version,
    list_profile_versions,
    save_profile,
)
from mealplanner.web.auth import current_user_id
from mealplanner.web.deps import get_db, get_now
from mealplanner.web.routes import templates

router = APIRouter()

MAX_VERSION = 2**62

UserId = Annotated[int, Depends(current_user_id)]
Conn = Annotated[sqlite3.Connection, Depends(get_db)]
Now = Annotated[datetime, Depends(get_now)]


def _values(profile: Profile) -> dict[str, Any]:
    return {
        "servings": str(profile.servings),
        "max_cook_minutes": str(profile.max_cook_minutes),
        "skill": profile.skill,
        "repeat_after_weeks": str(profile.repeat_after_weeks),
        "preferences": profile.preferences,
        "exclusions": {t.value for t in profile.exclusions},
    }


def _submitted_values(form: dict[str, list[str]]) -> dict[str, Any]:
    def first(name: str) -> str:
        values = form.get(name)
        return values[0] if values else ""

    return {
        "servings": first("servings"),
        "max_cook_minutes": first("max_cook_minutes"),
        "skill": first("skill"),
        "repeat_after_weeks": first("repeat_after_weeks"),
        "preferences": first("preferences"),
        "exclusions": set(form.get("exclusions", [])),
    }


def _render_form(
    request: Request, values: dict[str, Any], errors: dict[str, str], status_code: int = 200
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "profile_edit.html",
        {
            "values": values,
            "errors": errors,
            "allergens": ALLERGENS,
            "diet_terms": DIET_TERMS,
            "skills": SKILLS,
            "preferences_max": PREFERENCES_MAX_CHARS,
        },
        status_code=status_code,
    )


def _human_time(stamp: str) -> str:
    parsed = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def _split_exclusions(profile: Profile | None) -> dict[str, Any]:
    return {
        "allergies": [t for t in ALLERGENS if profile and t in profile.exclusions],
        "other_exclusions": [t for t in DIET_TERMS if profile and t in profile.exclusions],
    }


@router.get("/profile", response_class=HTMLResponse)
async def view_profile(request: Request, user_id: UserId, conn: Conn) -> HTMLResponse:
    current = get_current_profile(conn, user_id)
    profile = current.profile if current is not None else None
    return templates.TemplateResponse(
        request, "profile.html", {"current": current, **_split_exclusions(profile)}
    )


@router.get("/profile/history", response_class=HTMLResponse)
async def profile_history(request: Request, user_id: UserId, conn: Conn) -> HTMLResponse:
    versions = list_profile_versions(conn, user_id)
    entries = [
        {"version": v.version, "saved": _human_time(v.created_at), "is_current": i == 0}
        for i, v in enumerate(versions)
    ]
    return templates.TemplateResponse(request, "profile_history.html", {"entries": entries})


@router.get("/profile/history/{version}", response_class=HTMLResponse)
async def profile_version(
    request: Request, version: int, user_id: UserId, conn: Conn
) -> HTMLResponse:
    # SQLite integers are 64-bit; a larger value cannot exist and must not reach the query.
    found = None
    if 1 <= version <= MAX_VERSION:
        found = get_profile_version(conn, user_id, version)
    if found is None:
        raise HTTPException(status_code=404, detail="Not found")
    current = get_current_profile(conn, user_id)
    current_version = current.version if current is not None else found.version
    return templates.TemplateResponse(
        request,
        "profile_version.html",
        {
            "version": found,
            "saved": _human_time(found.created_at),
            "is_current": found.version == current_version,
            "current_version": current_version,
            **_split_exclusions(found.profile),
        },
    )


@router.get("/profile/edit", response_class=HTMLResponse)
async def edit_profile(request: Request, user_id: UserId, conn: Conn) -> HTMLResponse:
    current = get_current_profile(conn, user_id)
    profile = current.profile if current is not None else DEFAULT_PROFILE
    return _render_form(request, _values(profile), {})


@router.post("/profile", response_model=None)
async def save_profile_route(request: Request, user_id: UserId, conn: Conn, now: Now) -> Response:
    posted = await request.form()
    form: dict[str, list[str]] = {}
    for key, value in posted.multi_items():
        if isinstance(value, str):
            form.setdefault(key, []).append(value)
    result = parse_profile_form(form)
    if isinstance(result, FormErrors):
        return _render_form(request, _submitted_values(form), result.messages, status_code=400)
    save_profile(conn, user_id, result, now=now)
    return RedirectResponse("/profile", status_code=303)
