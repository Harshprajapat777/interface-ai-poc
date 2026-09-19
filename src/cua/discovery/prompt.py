"""What the model is told, and what it is allowed to do.

Two choices shape this prompt.

The model never sees a secret. It asks to fill a named credential and the
engine substitutes the value locally, so a password reaches the browser without
ever reaching the API. In production that is the difference between a credential
broker and a leak, and it costs one extra tool to arrange.

Every action must come with the text the model expects to see afterwards. That
serves three purposes at once: it makes the model commit to a prediction, it
lets the loop tell it immediately when it was wrong so it can correct itself,
and it produces the checkpoints the artifact needs without a second pass.
"""

SYSTEM = """You are operating a legacy bank back-office application through a
text description of what is on screen. You are discovering how to accomplish a
goal so the flow can be recorded and replayed later without you.

The screen is described to you as:
  TEXT      - the visible text, with table rows as tab-separated cells
  CONTROLS  - the controls you can act on, each with a [ref] you use to act

Rules:
- Act on one control at a time, using its [ref] from the CURRENT screen. Refs
  change after every action, so never reuse an old one.
- Some controls have no name. Use the label shown next to them to tell them
  apart, and read the TEXT to understand the layout.
- You are never given passwords. To sign in, use fill_secret with the name of
  the credential; the value is filled in for you.
- With every action, state the text you expect to appear on the next screen.
  You will be told whether it appeared. If it did not, look at the new screen
  and call revise_checkpoint with text that IS there, so the step can still be
  verified on later runs. A wrong prediction does not mean the action failed,
  so never repeat an action just because your prediction was wrong.
  Choose text that was NOT on the previous screen and IS on this one, so it
  proves the step worked. Not a banner or menu item that is always there.
  It must also be the same for any member, so never a name or an amount.
- When you can see a value the goal asked for, call record_output before
  finishing, so it can be extracted on every future run.
- Call finish as soon as the goal is met. Do not explore further.
- In any text you write, describe the step, never the record. Do not quote
  a member's name, account number or balance back to us.

Prefer identifying controls by their name or the label beside them. Keep going
until the goal is met or you are certain it cannot be."""

TOOLS: list[dict[str, object]] = [
    {
        "name": "navigate",
        "description": "Open a URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "why": {"type": "string", "description": "Why this step is needed."},
                "expect_text": {
                    "type": "string",
                    "description": "Text you expect on the resulting screen.",
                },
            },
            "required": ["url", "why", "expect_text"],
        },
    },
    {
        "name": "click",
        "description": "Click a control from the current screen.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "why": {"type": "string"},
                "expect_text": {"type": "string"},
            },
            "required": ["ref", "why", "expect_text"],
        },
    },
    {
        "name": "fill",
        "description": "Type a value into a control from the current screen.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "text": {"type": "string"},
                "why": {"type": "string"},
            },
            "required": ["ref", "text", "why"],
        },
    },
    {
        "name": "fill_secret",
        "description": "Type a credential you are not shown, by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "secret_name": {"type": "string"},
                "why": {"type": "string"},
            },
            "required": ["ref", "secret_name", "why"],
        },
    },
    {
        "name": "record_output",
        "description": "Declare a value on screen that this capability should return.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "e.g. savings_balance"},
                "description": {"type": "string"},
                "row_contains": {
                    "type": "string",
                    "description": "Text identifying the row the value sits in.",
                },
                "cell": {
                    "type": "integer",
                    "description": "Zero-based cell within that row.",
                },
            },
            "required": ["name", "description", "row_contains", "cell"],
        },
    },
    {
        "name": "revise_checkpoint",
        "description": "Correct the expected text for the action you just took.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expect_text": {
                    "type": "string",
                    "description": "Text on the current screen that confirms the step worked.",
                }
            },
            "required": ["expect_text"],
        },
    },
    {
        "name": "finish",
        "description": "The goal has been met.",
        "input_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    },
]


def opening_message(goal: str, values: dict[str, str], secrets: list[str], screen: str) -> str:
    """The first message: the goal, the values to use, and the starting screen."""
    lines = [f"GOAL: {goal}", ""]
    if values:
        lines.append("Use these values where the flow asks for them:")
        lines.extend(f"  {name} = {value}" for name, value in values.items())
        lines.append("")
    if secrets:
        lines.append("Credentials available to fill_secret (values withheld from you):")
        lines.extend(f"  {name}" for name in secrets)
        lines.append("")
    lines.append(screen)
    return "\n".join(lines)
