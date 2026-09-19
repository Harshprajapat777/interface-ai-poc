"""The discovery loop: observe, decide, act, until the goal is met.

This is the only place a model is involved. It runs once per capability, and
what it produces is an artifact that never needs it again.

The loop is bounded three ways - a step cap, the policy allowlist, and the fact
that the model can only act on controls the current screen actually offers. A
confused model can waste a few cents; it cannot wander off the application or
loop forever.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from anthropic import Anthropic
from anthropic.types import MessageParam, ToolResultBlockParam

from cua.discovery.prompt import SYSTEM, TOOLS, opening_message
from cua.policy.allowlist import Policy
from cua.surface.base import Control, Surface
from cua.surface.snapshot import to_prompt

DEFAULT_MODEL = "claude-sonnet-5"
MAX_STEPS = 25
MAX_TOKENS = 1024


@dataclass(slots=True)
class RecordedAction:
    """One action the model took, in a form the recorder can turn into a step."""

    action: str
    why: str
    expect_text: str = ""
    control: Control | None = None
    # The literal typed, or the name of the secret used.
    value: str = ""
    secret_name: str = ""
    checkpoint_held: bool = True
    # What was on screen before the action, so a checkpoint can be required to
    # be text that actually appeared rather than text that was already there.
    before: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RecordedOutput:
    """A value the model declared this capability should return."""

    name: str
    description: str
    row_contains: str
    cell: int


@dataclass(slots=True)
class DiscoveryRun:
    """Everything one discovery run produced."""

    goal: str
    model: str
    actions: list[RecordedAction] = field(default_factory=list)
    outputs: list[RecordedOutput] = field(default_factory=list)
    summary: str = ""
    succeeded: bool = False
    llm_turns: int = 0


class DiscoveryAgent:
    """Drives a surface with a model until the goal is met or the budget runs out."""

    def __init__(
        self,
        surface: Surface,
        policy: Policy,
        client: Anthropic | None = None,
        model: str = DEFAULT_MODEL,
        max_steps: int = MAX_STEPS,
    ) -> None:
        self.surface = surface
        self.policy = policy
        self.client = client or Anthropic()
        self.model = model
        self.max_steps = max_steps

    def run(self, goal: str, values: dict[str, str], secrets: dict[str, str]) -> DiscoveryRun:
        """Pursues the goal, recording each successful action."""
        run = DiscoveryRun(goal=goal, model=self.model)
        opening = opening_message(goal, values, sorted(secrets), self._screen())
        messages: list[MessageParam] = [{"role": "user", "content": opening}]

        for _ in range(self.max_steps):
            reply = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM,
                tools=TOOLS,  # type: ignore[arg-type]
                messages=messages,
            )
            run.llm_turns += 1
            messages.append({"role": "assistant", "content": reply.content})

            calls = [block for block in reply.content if block.type == "tool_use"]
            if not calls:
                messages.append({"role": "user", "content": "Use a tool, or call finish."})
                continue

            results: list[ToolResultBlockParam] = []
            for call in calls:
                text = self._perform(str(call.name), dict(call.input), run, secrets)
                results.append(
                    {"type": "tool_result", "tool_use_id": call.id, "content": text}
                )
                if run.succeeded:
                    return run
            messages.append({"role": "user", "content": results})

        return run

    def _screen(self) -> str:
        """The current screen, as the model reads it."""
        return to_prompt(self.surface.observe())

    def _perform(
        self,
        name: str,
        args: dict[str, Any],
        run: DiscoveryRun,
        secrets: dict[str, str],
    ) -> str:
        """Carries out one tool call and reports back to the model."""
        if name == "finish":
            run.succeeded = True
            run.summary = str(args.get("summary", ""))
            return "Finished."
        if name == "record_output":
            return self._record_output(args, run)
        if name == "revise_checkpoint":
            return self._revise(args, run)
        if name == "navigate":
            return self._navigate(args, run)
        return self._act_on_control(name, args, run, secrets)

    def _record_output(self, args: dict[str, Any], run: DiscoveryRun) -> str:
        """Notes a value the capability should return on every future run."""
        run.outputs.append(
            RecordedOutput(
                name=str(args["name"]),
                description=str(args["description"]),
                row_contains=str(args["row_contains"]),
                cell=int(args["cell"]),
            )
        )
        return f"Recorded output {args['name']}."

    def _revise(self, args: dict[str, Any], run: DiscoveryRun) -> str:
        """Lets the model correct an expectation it got wrong, having seen the screen.

        A wrong guess would otherwise leave the step with no checkpoint at all,
        which is the one outcome worse than a wrong one: replay would carry on
        without ever verifying that the step worked.
        """
        if not run.actions:
            return "There is no previous action to revise."
        text = str(args["expect_text"])
        action = run.actions[-1]
        problem = self._checkpoint_problem(text, action.before, self.surface.observe().texts)
        if problem:
            return problem
        action.expect_text = text
        action.checkpoint_held = True
        return f"Checkpoint for the previous step set to {text!r}."

    def _checkpoint_problem(self, text: str, before: list[str], after: list[str]) -> str:
        """Why a proposed checkpoint is no good, or empty if it is fine."""
        if not any(text in line for line in after):
            return f"{text!r} is not on this screen either. Pick text you can actually see."
        if any(text in line for line in before):
            return (
                f"{text!r} was already on screen before the action, so it proves nothing. "
                f"Pick text that only appears now that the step has worked."
            )
        return ""

    def _navigate(self, args: dict[str, Any], run: DiscoveryRun) -> str:
        """Opens a URL, refusing anything outside the allowlist."""
        url = str(args["url"])
        denial = self.policy.check_url(url)
        if denial is not None:
            return f"Refused: {denial}. Stay within the application."
        before = self.surface.observe().texts
        failed = self._try(lambda: self.surface.open(url))
        if failed:
            return f"Could not open {url}: {failed}"
        action = RecordedAction(
            "navigate", str(args["why"]), str(args["expect_text"]), value=url, before=before
        )
        return self._after(action, run)

    def _act_on_control(
        self,
        name: str,
        args: dict[str, Any],
        run: DiscoveryRun,
        secrets: dict[str, str],
    ) -> str:
        """Clicks or types into a control the model picked by ref."""
        control = self._control(str(args["ref"]))
        if control is None:
            return f"No control {args['ref']!r} on this screen. Here it is again:\n{self._screen()}"

        if name != "click" and control.role != "textbox":
            return (
                f"[{control.ref}] is a {control.role}, not a text field, so it cannot be "
                f"typed into. Here is the current screen:\n{self._screen()}"
            )

        before = self.surface.observe().texts
        if name == "click":
            action = RecordedAction(
                "click", str(args["why"]), str(args["expect_text"]), control, before=before
            )
            failed = self._try(lambda: self.surface.click(control))
        elif name == "fill":
            text = str(args["text"])
            action = RecordedAction(
                "fill", str(args["why"]), control=control, value=text, before=before
            )
            failed = self._try(lambda: self.surface.fill(control, text))
        else:
            secret_name = str(args["secret_name"])
            if secret_name not in secrets:
                return f"No credential named {secret_name!r}."
            action = RecordedAction(
                "fill", str(args["why"]), control=control, secret_name=secret_name, before=before
            )
            failed = self._try(lambda: self.surface.fill(control, secrets[secret_name]))

        if failed:
            return f"That did not work: {failed}\nHere is the screen now:\n{self._screen()}"
        return self._after(action, run)

    def _try(self, action: Callable[[], None]) -> str:
        """Runs an action, returning the problem as text instead of raising.

        A model that picks the wrong control should be told so and given the
        screen again. Crashing the run over a recoverable mistake would throw
        away every correct step it had already found.
        """
        try:
            action()
        except Exception as problem:  # noqa: BLE001 - the model gets to see anything that failed
            return str(problem).splitlines()[0]
        return ""

    def _control(self, ref: str) -> Control | None:
        """Finds a control by the ref the model was shown."""
        for control in self.surface.observe().controls:
            if control.ref == ref:
                return control
        return None

    def _after(self, action: RecordedAction, run: DiscoveryRun) -> str:
        """Records the action and tells the model what the screen looks like now."""
        observation = self.surface.observe()
        screen = to_prompt(observation)
        problem = ""
        if action.expect_text:
            problem = self._checkpoint_problem(
                action.expect_text, action.before, observation.texts
            )
            action.checkpoint_held = not problem
        run.actions.append(action)
        if not action.checkpoint_held:
            return f"You expected {action.expect_text!r} but it is not there.\n{screen}"
        return screen
