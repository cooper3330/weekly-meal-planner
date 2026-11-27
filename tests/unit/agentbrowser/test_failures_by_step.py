from agentbrowser.driver import _Recorder


def _request(url: str, *, external: bool = False, status: int | None = 200) -> dict[str, object]:
    return {
        "url": url,
        "method": "GET",
        "status": status,
        "failed": False,
        "external": external,
    }


def test_console_and_request_failures_name_the_step_they_arrived_in() -> None:
    rec = _Recorder("http://127.0.0.1:8000")
    starts = [(0, 0, "step 1 (goto): ")]
    rec.console.append({"type": "error", "text": "boom", "url": ""})
    starts.append((len(rec.console), len(rec.requests), "step 2 (click): "))
    rec.requests.append(_request("http://evil.test/", external=True, status=None))
    rec.requests.append(_request("http://127.0.0.1:8000/x", status=500))
    assert rec.failures_by_step(starts) == [
        "step 1 (goto): console error: boom",
        "step 2 (click): external request (aborted): GET http://evil.test/",
        "step 2 (click): request failed: GET http://127.0.0.1:8000/x -> 500",
    ]


def test_entries_arriving_late_belong_to_the_last_step() -> None:
    rec = _Recorder("http://127.0.0.1:8000")
    starts = [(0, 0, "step 1 (goto): "), (0, 0, "step 2 (click): ")]
    rec.console.append({"type": "pageerror", "text": "late", "url": ""})
    assert rec.failures_by_step(starts) == ["step 2 (click): uncaught exception: late"]


def test_no_steps_and_no_entries_gives_no_failures() -> None:
    assert _Recorder("http://h").failures_by_step([]) == []
