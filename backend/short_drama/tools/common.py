"""Small shared models for concrete tool adapters."""

from short_drama.tools.contracts import ToolModel


class EmptyToolInput(ToolModel):
    """Input model for tools that require no arguments."""
