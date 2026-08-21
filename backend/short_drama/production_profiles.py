"""Prompt policies for the supported production profiles.

Profiles tune creative judgement inside the existing Agent runtime. They do not
create a second workflow or introduce storyboard acceptance gates.
"""

from __future__ import annotations

from dataclasses import dataclass

from short_drama.models import ProductionProfile


@dataclass(frozen=True)
class ProductionProfilePolicy:
    label: str
    writer: str
    planner: str
    director: str


_POLICIES = {
    ProductionProfile.GENERAL_VIDEO: ProductionProfilePolicy(
        label="general live-action vertical video",
        writer=(
            "Let the dramatic form follow the supplied material. Atmosphere, silence, "
            "observation, repetition, and uninterrupted performance are valid when they carry "
            "mood, character, anticipation, rhythm, or spatial meaning. Do not invent conflict, "
            "dialogue, or a twist merely to imitate a short-drama formula."
        ),
        planner=(
            "Choose the smallest coherent set of shots and let pacing follow the intended "
            "experience. A shot may earn its duration through plot, performance, mood, rhythm, "
            "or spatial comprehension. Do not add cuts, dialogue, or story beats solely to make "
            "the video denser. Long takes are welcome when their internal progression is clear."
        ),
        director=(
            "Judge pacing against the intended experience. Do not treat quiet passages or long "
            "takes as defects based on duration alone; recommend a change only when the observed "
            "result fails its stated narrative, performance, mood, rhythm, or spatial purpose."
        ),
    ),
    ProductionProfile.DOUYIN_SHORT_DRAMA: ProductionProfilePolicy(
        label="live-action Douyin vertical short drama",
        writer=(
            "Front-load a legible curiosity gap, conflict, unusual fact, or relationship tension. "
            "Maintain a causal curiosity chain in which meaningful beats change what the viewer "
            "knows, expects, wants, or fears. Seed and pay off a memorable reveal or reversal; "
            "compress empty connective action without manufacturing random shocks or exposition."
        ),
        planner=(
            "Turn the story into an executable visual progression suited to fast vertical "
            "viewing. Every shot should have a concrete reason to occupy its duration, such as a "
            "reveal, reaction, decision, relationship shift, visual clue, or purposeful suspense. "
            "A long take is valid when it carries several connected changes. Never add shots, "
            "dialogue, or beats mechanically just to increase density."
        ),
        director=(
            "Assess whether the shot earns its duration for a fast vertical short drama. "
            "Distinguish purposeful suspense, performance, or visual discovery from empty holding "
            "or transit. When improvement is useful, choose among regeneration, keyframe change, "
            "shot split, or coverage change from the actual evidence rather than a fixed timing "
            "or beat-count rule."
        ),
    ),
}


def production_profile_policy(
    profile: ProductionProfile,
) -> ProductionProfilePolicy:
    return _POLICIES[profile]
