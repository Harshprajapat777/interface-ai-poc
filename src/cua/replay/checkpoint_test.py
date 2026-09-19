from cua.artifact.schema import Checkpoint
from cua.replay.checkpoint import is_satisfied, wait_until
from cua.surface.base import Observation


def screen(*texts: str, url: str = "http://app/console") -> Observation:
    return Observation(url=url, title="t", controls=[], texts=list(texts))


def test_text_present_holds_when_the_text_is_on_screen() -> None:
    check = Checkpoint(kind="text_present", value="record found")
    assert is_satisfied(check, screen("1 record found."))


def test_text_absent_holds_when_the_text_is_gone() -> None:
    assert is_satisfied(Checkpoint(kind="text_absent", value="error"), screen("all good"))


def test_url_contains_checks_the_address() -> None:
    assert is_satisfied(Checkpoint(kind="url_contains", value="/console"), screen())


def test_waiting_succeeds_once_the_screen_catches_up() -> None:
    """A slow load is absorbed by polling rather than by padding every step."""
    screens = [screen("Loading..."), screen("Loading..."), screen("Current Balance")]
    calls = iter(screens)
    clock = iter([0.0, 0.1, 0.2, 0.3])

    held, last = wait_until(
        Checkpoint(kind="text_present", value="Current Balance", timeout_ms=5000),
        observe=lambda: next(calls),
        now=lambda: next(clock),
        sleep=lambda _: None,
    )
    assert held
    assert last.texts == ["Current Balance"]


def test_waiting_gives_up_and_reports_what_it_saw() -> None:
    ticks = iter([0.0, 9.9, 99.0])

    held, last = wait_until(
        Checkpoint(kind="text_present", value="Current Balance", timeout_ms=1000),
        observe=lambda: screen("Still the search page"),
        now=lambda: next(ticks),
        sleep=lambda _: None,
    )
    assert not held
    assert last.texts == ["Still the search page"]
