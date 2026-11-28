"""ADR 0030's publisher rules, written out for the tests (#173).

The evidence publisher (#175) validates the downloaded bundle against these rules. ``problems``
applies them to a bundle the runner wrote, so a drift between the runner and the publisher shows
up here. The numbers and patterns are copied from the ADR on purpose: a test that imported the
runner's constants would agree with itself.
"""

import json
import re
from pathlib import Path
from typing import Any

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
PNG_FILE = re.compile(r"^([0-9]{2}-[a-z_]+(--AC-[0-9]+)?|final-full)\.png$")
KIND = re.compile(r"^[a-z_]+$")
CRITERION = re.compile(r"^AC-[0-9]+$")
VIEWPORT = re.compile(r"^[0-9]{2,4}x[0-9]{2,4}$")
CHECK_NAME = re.compile(r"^[a-z0-9_]{1,64}$")
STATUS = ("passed", "failed")
LIMITS = ("time", "screenshots", "bytes")
PNG_SIGNATURE = bytes.fromhex("89504e470d0a1a0a")
MAX_PNG_BYTES = 1_048_576
MAX_PNGS = 200
MAX_TOTAL_BYTES = 31_457_280


def png_path_problem(path: str) -> str | None:
    """None when ``path`` is ``<flow>/<file>`` or ``walk/<feature>/<file>`` with valid names."""
    parts = path.split("/")
    if len(parts) == 2:
        folder, file = parts
        if not NAME.fullmatch(folder) or folder == "walk":
            return f"{path}: bad flow name"
    elif len(parts) == 3 and parts[0] == "walk":
        folder, file = parts[1], parts[2]
        if not NAME.fullmatch(folder):
            return f"{path}: bad feature name"
    else:
        return f"{path}: not <flow>/<file> or walk/<feature>/<file>"
    if not PNG_FILE.fullmatch(file):
        return f"{path}: bad file name"
    return None


def _pngs(bundle: Path) -> list[Path]:
    return sorted(p for p in bundle.rglob("*") if p.is_file() and p.suffix == ".png")


def _summary_problems(summary: dict[str, Any], pngs: set[str]) -> list[str]:
    out: list[str] = []

    def need(ok: bool, what: str) -> None:
        if not ok:
            out.append(what)

    def shot(value: Any, where: str) -> None:
        need(value is None or value in pngs, f"{where}: screenshot {value!r} is not in the bundle")

    def status(value: Any, where: str) -> None:
        need(value in STATUS, f"{where}: status {value!r}")

    need(bool(VIEWPORT.fullmatch(str(summary.get("viewport")))), "viewport")
    entries = [("flows", f) for f in summary["flows"]] + [("walk", w) for w in summary["walk"]]
    for section, entry in entries:
        key = "name" if section == "flows" else "feature"
        where = f"{section}.{entry[key]}"
        need(bool(NAME.fullmatch(entry[key])), f"{where}: name")
        status(entry["status"], where)
        need(bool(VIEWPORT.fullmatch(str(entry["viewport"]))), f"{where}: viewport")
        for item in entry.get("proves", []):
            need(isinstance(item["issue"], int) and item["issue"] >= 0, f"{where}: proves.issue")
            for criterion in item["criteria"]:
                need(bool(CRITERION.fullmatch(criterion)), f"{where}: proves {criterion!r}")
        for number, step in enumerate(entry["steps"], 1):
            at = f"{where}.steps[{number}]"
            need(bool(KIND.fullmatch(step["kind"])), f"{at}: kind")
            status(step["status"], at)
            need(
                step["shows"] is None or bool(CRITERION.fullmatch(step["shows"])),
                f"{at}: shows",
            )
            shot(step["screenshot"], at)
        for page in entry["checks"]:
            status(page["status"], f"{where}.checks")
            shot(page["screenshot"], f"{where}.checks")
            for check in page["checks"]:
                need(bool(CHECK_NAME.fullmatch(check["check"])), f"{where}: check name")
                status(check["status"], f"{where}.checks.{check['check']}")
    for item in summary["oversized"]:
        problem = png_path_problem(item["path"])
        need(problem is None, problem or "")
        need(isinstance(item["bytes"], int) and item["bytes"] > 0, "oversized.bytes")
    cut = summary.get("cut_off")
    if cut is not None:
        need(cut["limit"] in LIMITS, f"cut_off.limit {cut['limit']!r}")
        need(isinstance(cut["after_flows"], int) and cut["after_flows"] >= 0, "cut_off.after_flows")
    return out


def problems(bundle: Path) -> list[str]:
    """Every way ``bundle`` breaks the publisher's rules (an empty list means it is accepted)."""
    out: list[str] = []
    pngs = _pngs(bundle)
    total = 0
    names: set[str] = set()
    for path in pngs:
        rel = path.relative_to(bundle).as_posix()
        names.add(rel)
        problem = png_path_problem(rel)
        if problem:
            out.append(problem)
        data = path.read_bytes()
        total += len(data)
        if not data.startswith(PNG_SIGNATURE):
            out.append(f"{rel}: not a PNG")
        if len(data) > MAX_PNG_BYTES:
            out.append(f"{rel}: {len(data)} bytes, over 1 MB")
    if len(pngs) > MAX_PNGS:
        out.append(f"{len(pngs)} PNGs, over {MAX_PNGS}")
    if total > MAX_TOTAL_BYTES:
        out.append(f"{total} PNG bytes, over 30 MB")
    summary_file = bundle / "summary.json"
    if not summary_file.is_file():
        return [*out, "no summary.json"]
    out.extend(_summary_problems(json.loads(summary_file.read_text()), names))
    return out
