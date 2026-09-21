"""Operator surfaces: how a person is actually told, and how they actually act.

A real co-browsing console is out of scope, and the brief says so. What is not
out of scope is the mechanism underneath one, so the console here is deliberately
thin but genuinely real: it prints the request, the automation stops, and the
person drives the live browser window with their own mouse until they say they
are done.

Two implementations, and the difference between them is the whole point of the
Operator protocol. Swapping the console for a web console, a queue worker or a
paging system changes this file and nothing else.
"""

from collections.abc import Callable

from cua.escalation.broker import Intervention, Resolution
from cua.surface.base import Surface

RULE = "=" * 72


def _render(intervention: Intervention, lines: int = 12) -> str:
    """The request as an operator sees it: enough context to act without asking."""
    screen = "\n".join(f"    {line}" for line in intervention.screen[:lines])
    return f"""{RULE}
OPERATOR INTERVENTION REQUIRED          {intervention.id}
{RULE}
Capability : {intervention.capability}
Goal       : {intervention.goal}
Stopped at : step {intervention.step_id} ({intervention.kind})
Reason     : {intervention.reason}
URL        : {intervention.url}
Screenshot : {intervention.screenshot or "(none)"}

On screen now:
{screen}
{RULE}
The browser window is yours. Do what is needed, then come back here.
{RULE}"""


class ConsoleOperator:
    """Hands the live browser to whoever is sitting at the terminal.

    The automation is blocked on input() for as long as the person is working,
    which is exactly the guarantee we want: nothing else touches the session
    until they hand it back.
    """

    def __init__(self, prompt: Callable[[str], str] = input) -> None:
        self._prompt = prompt

    def take_over(self, intervention: Intervention, surface: Surface) -> Resolution:
        """Shows the request, waits for the person, and reports what they chose."""
        print(_render(intervention))
        answer = self._prompt("Type 'done' to resume, or 'abandon' to stop: ").strip().lower()
        note = self._prompt("What did you do? ").strip()
        outcome = "abandoned" if answer.startswith("a") else "resumed"
        return Resolution(outcome=outcome, note=note)  # type: ignore[arg-type]


class ScriptedOperator:
    """An operator whose actions are a function. Used by tests and demos.

    This is the seam standing in for a real console: it receives the same
    request and drives the same live session, it just does not need a human.
    """

    def __init__(
        self,
        act: Callable[[Surface], None],
        note: str = "scripted operator",
        outcome: str = "resumed",
    ) -> None:
        self._act = act
        self._note = note
        self._outcome = outcome

    def take_over(self, intervention: Intervention, surface: Surface) -> Resolution:
        """Performs the scripted actions against the live session."""
        self._act(surface)
        return Resolution(outcome=self._outcome, note=self._note)  # type: ignore[arg-type]
