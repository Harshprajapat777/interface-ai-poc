"""Pointing a recorded capability at a different instance of the same app.

This is the smallest useful piece of the multi-tenant story. Hundreds of
institutions run the same vendor build at different addresses, and the flow
through the screens is identical - only the host differs. Re-recording per
tenant would be absurd, so an artifact is rebased instead.

It is deliberately only the address. Anything else that differs between two
tenants - a renamed button, an extra confirmation step - is a real difference
in the flow and belongs in an override that a person reviewed, not in a string
replacement that happens to work.
"""

from cua.artifact.schema import Capability


def rebase(capability: Capability, base_url: str, tenant: str = "") -> Capability:
    """Returns the capability with every recorded address moved to a new instance."""
    recorded = capability.app.base_url.rstrip("/")
    steps = [
        step.model_copy(update={"value": step.value.replace(recorded, base_url)})
        if step.value and recorded and recorded in step.value
        else step
        for step in capability.steps
    ]
    app = capability.app.model_copy(
        update={"base_url": base_url, "tenant": tenant or capability.app.tenant}
    )
    return capability.model_copy(update={"steps": steps, "app": app})
