"""Turns a live page into an Observation, and an Observation into text for the model.

We compute role and accessible name ourselves rather than reading the browser's
accessibility tree directly, for one reason: we also need the *fallback* signals
(the nearest visible text, the position among peers) and the tree does not carry
them. The shape is the same as the tree's - role, name, children - so a desktop
implementation reading an OS accessibility API produces the same Observation.

Scanning tags each element with a data-cua-ref attribute. That attribute is
scratch, valid only for the snapshot that wrote it; nothing durable is ever
recorded against it.
"""

from typing import TypedDict

from cua.surface.base import Control, Observation


class ScanResult(TypedDict):
    """What the scan script returns for one frame."""

    controls: list[dict[str, str]]
    texts: list[str]


# Runs inside one frame. Returns the controls it can see plus the visible text.
SCAN_JS = """
(prefix) => {
  const roleOf = (el) => {
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      if (type === 'submit' || type === 'button' || type === 'reset') return 'button';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      return 'textbox';
    }
    return 'button';
  };

  const nameOf = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria.trim();
    const title = el.getAttribute('title');
    if (title) return title.trim();
    if (el.tagName.toLowerCase() === 'input') {
      return roleOf(el) === 'button' ? (el.value || '').trim() : '';
    }
    return (el.innerText || '').trim().slice(0, 80);
  };

  const labelOf = (el) => {
    const cell = el.closest('td');
    if (cell && cell.previousElementSibling) {
      const text = (cell.previousElementSibling.innerText || '').trim();
      if (text) return text.slice(0, 80);
    }
    if (el.previousElementSibling) {
      const text = (el.previousElementSibling.innerText || '').trim();
      if (text) return text.slice(0, 80);
    }
    return '';
  };

  const isVisible = (el) => {
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  };

  const controls = [];
  let counter = 0;
  const selector = 'input, button, a, select, textarea, [onclick]';
  for (const el of document.querySelectorAll(selector)) {
    if (el.type === 'hidden' || !isVisible(el)) continue;
    const ref = prefix + '-' + (++counter);
    el.setAttribute('data-cua-ref', ref);
    controls.push({ref: ref, role: roleOf(el), name: nameOf(el), label: labelOf(el)});
  }

  const texts = (document.body.innerText || '')
    .split('\\n').map(line => line.trim()).filter(Boolean);

  return {controls: controls, texts: texts};
}
"""


def build_controls(raw: list[dict[str, str]], frame: str) -> list[Control]:
    """Turns one frame's scan results into Controls, numbering peers by role."""
    seen_per_role: dict[str, int] = {}
    controls = []
    for item in raw:
        role = item["role"]
        index = seen_per_role.get(role, 0)
        seen_per_role[role] = index + 1
        controls.append(
            Control(
                ref=item["ref"],
                role=role,
                name=item["name"],
                label=item["label"],
                frame=frame,
                index=index,
            )
        )
    return controls


def _describe(control: Control) -> str:
    """One line describing a control, the way the model will see it."""
    parts = [f"[{control.ref}] {control.role}"]
    if control.name:
        parts.append(f'name="{control.name}"')
    if control.label:
        parts.append(f'label="{control.label}"')
    if control.frame:
        parts.append(f"frame={control.frame}")
    return " ".join(parts)


def to_prompt(observation: Observation, max_texts: int = 40) -> str:
    """Renders an Observation as the compact text block the model reads."""
    lines = [f"URL: {observation.url}", f"TITLE: {observation.title}", "", "TEXT:"]
    lines.extend(f"  {text}" for text in observation.texts[:max_texts])
    lines.extend(["", "CONTROLS:"])
    lines.extend(f"  {_describe(control)}" for control in observation.controls)
    return "\n".join(lines)
