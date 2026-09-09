"""One language configuration shared by planning, workers and media reasoning."""

from typing import Final, Literal

type ReasoningEffort = Literal["low", "medium", "high", "xhigh", "max", "ultra"]

LANGUAGE_MODEL: Final = "gpt-6-astra"
LANGUAGE_REASONING_EFFORT: Final[ReasoningEffort] = "xhigh"


def language_model_name(client: object) -> str:
    """Use the actual configured client, including explicitly supplied clients."""
    model = getattr(client, "model", None)
    return model if isinstance(model, str) and model else LANGUAGE_MODEL
