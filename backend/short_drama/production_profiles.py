"""Static, composable Production Skills for the Agent harness.

A Production Skill has three deliberately separate layers:

1. a small, stable constitution for the content type;
2. role-specific rubrics injected only into Agents that need them;
3. retrieved production experience that is added only when the current context
   matches the experience.

The skill never owns orchestration, provider calls, task state, or approval.
Tools remain independently callable and every creative recommendation remains
user-overridable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from short_drama.models import ProductionProfile


class ProductionSkillRole(StrEnum):
    CONCEPT = "CONCEPT"
    WRITER = "WRITER"
    PLANNER = "PLANNER"
    CRITIC = "CRITIC"
    REVIEW_ROUTER = "REVIEW_ROUTER"
    DIRECTOR = "DIRECTOR"
    ASSET_REVIEWER = "ASSET_REVIEWER"
    ROUTE_PLANNER = "ROUTE_PLANNER"
    VIDEO_PROMPT_COMPILER = "VIDEO_PROMPT_COMPILER"
    EPISODE_REVIEWER = "EPISODE_REVIEWER"


@dataclass(frozen=True)
class ProductionSkill:
    profile: ProductionProfile
    label: str
    medium: str
    constitution: str
    concept: str
    writer: str
    planner: str
    critic: str
    review_router: str
    director: str
    asset_reviewer: str
    route_planner: str
    video_prompt_compiler: str
    episode_reviewer: str
    recommended_playbook: tuple[str, ...]
    user_overridable: bool = True


@dataclass(frozen=True)
class ProductionExperience:
    id: str
    profiles: tuple[ProductionProfile, ...]
    roles: tuple[ProductionSkillRole, ...]
    strong_trigger_terms: tuple[str, ...]
    guidance: str
    supporting_trigger_terms: tuple[str, ...] = ()
    minimum_supporting_matches: int = 2


@dataclass(frozen=True)
class CompiledProductionPrompt:
    profile: ProductionProfile
    roles: tuple[ProductionSkillRole, ...]
    experience_ids: tuple[str, ...]
    text: str


_DEFAULT_CREATIVE_PLAYBOOK = (
    "compile the user's source contract when source material is supplied",
    "create a compact global story blueprint",
    "write or revise only the scenes needed for the current task",
    "review the script and route findings to the smallest capable owner",
    "repair story-owned findings locally when possible",
    "direct scenes into executable shots",
    "retrieve, derive, or generate only the assets required by those shots",
    "generate and inspect video takes, then assemble and review the timeline",
)


_SKILLS = {
    ProductionProfile.GENERAL_VIDEO: ProductionSkill(
        profile=ProductionProfile.GENERAL_VIDEO,
        label="general live-action vertical video",
        medium="真人竖屏视频",
        constitution=(
            "Follow the user's intended experience. Plot density, conflict, dialogue, novelty, and a "
            "twist are optional. Atmosphere, silence, observation, repetition, and long takes are "
            "valid when they carry narrative, performance, mood, rhythm, or spatial meaning. Treat "
            "all creative guidance as advice rather than a quota or approval gate."
        ),
        concept=(
            "When no concept is supplied, explore materially different forms and choose the one "
            "whose intended audience experience is clearest. Preserve a supplied idea rather than "
            "replacing it with a more conventional dramatic formula."
        ),
        writer=(
            "Let the dramatic form follow the supplied material. Do not invent conflict, dialogue, "
            "or a twist merely to imitate a short-drama formula. Dialogue and action should remain "
            "specific to the people, situation, and intended tone."
        ),
        planner=(
            "Choose the smallest coherent set of shots and let pacing follow the intended experience. "
            "A shot may earn its duration through plot, performance, mood, rhythm, or spatial "
            "comprehension. Do not add cuts, dialogue, or beats solely to make the work denser."
        ),
        critic=(
            "Judge the work against its stated experience. Dialogue duration and non-dialogue time "
            "are descriptive evidence only; quiet material is not defective merely because it is quiet. "
            "Report concrete contradictions or unearned duration without imposing a genre formula."
        ),
        review_router=(
            "Revise the story only when intent, causality, motivation, setup/payoff, scene purpose, or "
            "duration allocation must change. Route staging, assets, generation behavior, and editing "
            "to the smallest downstream owner."
        ),
        director=(
            "Judge staging and pacing against the intended experience. Recommend regeneration, a "
            "keyframe change, a shot split, or coverage change only when observed evidence shows that "
            "the current result misses its narrative, performance, mood, rhythm, or spatial purpose."
        ),
        asset_reviewer=(
            "Judge an asset by visible fidelity to its declared identity, topology, state, and intended "
            "production use. Neutral reference views and quiet imagery are valid; do not demand conflict "
            "or spectacle from an asset that is meant to provide reusable control."
        ),
        route_planner=(
            "Choose the generation route that best preserves declared intent, continuity, and the "
            "available approved controls. Do not prefer more cuts or stronger motion merely to make a "
            "general video feel busier."
        ),
        video_prompt_compiler=(
            "Translate the locked shot and approved media bindings into the provider's prompt format "
            "without intensifying pace, motion, conflict, or dialogue. Preserve intentional quiet, "
            "atmosphere, and long-take behavior when they are part of the declared shot."
        ),
        episode_reviewer=(
            "Review the finished edit against its intended story, performance, mood, rhythm, and spatial "
            "purpose. Quiet pacing and long takes remain valid when the edited evidence shows useful "
            "progression; do not apply short-drama density expectations."
        ),
        recommended_playbook=_DEFAULT_CREATIVE_PLAYBOOK,
    ),
    ProductionProfile.DOUYIN_SHORT_DRAMA: ProductionSkill(
        profile=ProductionProfile.DOUYIN_SHORT_DRAMA,
        label="live-action Douyin vertical short drama",
        medium="真人抖音竖屏短剧",
        constitution=(
            "Create an immediately legible live-action vertical drama with a specific audience promise, "
            "concrete stakes, and sustained audiovisual progression. Fast pacing comes from changes in "
            "what viewers see, expect, want, fear, or watch a character decide—not from compressed, "
            "unnatural dialogue. Preserve the user's premise and treat all creative guidance as advice "
            "rather than a fixed beat quota or approval gate."
        ),
        concept=(
            "When the system owns concept selection, compare several materially different premises and "
            "return only the strongest. Prefer a specific unusual situation, concrete desire or threat, "
            "visible contrast, emotionally satisfying reversal, and an engine that could sustain later "
            "episodes. The title should advertise the particular plot promise. When the user supplies an "
            "idea, preserve and amplify its signature contrast instead of replacing it with a safer genre."
        ),
        writer=(
            "Front-load a legible curiosity gap, conflict, unusual fact, or relationship tension. Treat "
            "each scene as an audiovisual dramatic unit whose meaningful beats change story state; repeated "
            "proof of one conclusion is reinforcement rather than a new turn. Compress empty connective "
            "action without manufacturing unrelated shocks or exposition. Create speed through visible "
            "action, reaction, blocking, reversal, concurrent activity, shot progression, and editing "
            "opportunities. Dialogue must sound like particular Mandarin-speaking people under the current "
            "pressure, using ordinary syntax, pronouns, particles, interruption, motivated repetition, and "
            "unfinished phrases when natural. Not every line must add a fact. Never verbalize scene metadata, "
            "continuity rules, relationship labels, or production instructions as terse dialogue."
        ),
        planner=(
            "Turn the story into an executable visual progression for fast vertical viewing. Every shot "
            "should earn its duration through a reveal, reaction, decision, relationship shift, visual clue, "
            "purposeful suspense, or another observable development. A long take is valid when it carries "
            "several connected changes. Never add shots, dialogue, or beats mechanically to increase density."
        ),
        critic=(
            "Audit whether scenes earn their duration through visible changes in information, risk, goal, "
            "relationship, choice, or consequence. Treat pacing evidence as descriptive rather than a numeric "
            "gate. Separate audiovisual pace from dialogue naturalness and apply a read-aloud test: flag "
            "metadata-like, slogan-like, or telegraphic speech, but allow brisk natural delivery, overlap, "
            "particles, interruption, repetition, and unfinished phrases. Recommend causal events, choices, "
            "setbacks, consequences, payoffs, or honest shortening instead of padding pauses, exposition, cuts, "
            "or dialogue. Review the actual work rather than an answer key."
        ),
        review_router=(
            "Send an issue to Story Architect only when the episode promise, causal chain, motive, payoff, "
            "scene contract, or playable duration allocation must change. Route blocking and coverage to "
            "Scene Director, asset gaps to Asset Agent, generation behavior to Video Director, and edit or "
            "subtitle issues to Timeline Agent. Choose the owner from the remedy, not severity."
        ),
        director=(
            "Assess whether the shot earns its duration for a fast vertical drama while distinguishing "
            "purposeful suspense, performance, and visual discovery from empty holding or transit. Choose "
            "among regeneration, keyframe change, shot split, and coverage change from actual evidence rather "
            "than a fixed timing or beat-count rule."
        ),
        asset_reviewer=(
            "Prioritize reusable identity, wardrobe, location topology, prop construction, state clarity, and "
            "shot-relevant vertical composition. An asset supports a fast drama by being controllable and "
            "consistent; it does not need to contain a hook, conflict, or dramatic action by itself."
        ),
        route_planner=(
            "Prefer the ready route whose approved inputs best support the shot's declared state change and "
            "cross-shot continuity. Treat complex contact, large orientation changes, and incompatible start "
            "or end states as evidence for better controls, a split, or different coverage—not as a reason to "
            "repeat the same ineffective prompt."
        ),
        video_prompt_compiler=(
            "Preserve the selected shot's legible state change, performance, and fast vertical readability "
            "when translating it to the provider format. Do not add hooks, actions, cuts, or compressed "
            "dialogue at this final boundary, and never alter locked facts or approved media bindings."
        ),
        episode_reviewer=(
            "Assess whether the edited episode keeps its promise legible and sustains causal audiovisual "
            "progression. Flag specific empty holding, repeated proof, missing choice, broken payoff, or state "
            "discontinuity, while judging dialogue naturalness separately. Do not require a new fact every N "
            "seconds or convert optional polish into a failure."
        ),
        recommended_playbook=_DEFAULT_CREATIVE_PLAYBOOK,
    ),
}


_EXPERIENCES = (
    ProductionExperience(
        id="distinctive-mystery-promise",
        profiles=(ProductionProfile.DOUYIN_SHORT_DRAMA,),
        roles=(
            ProductionSkillRole.CONCEPT,
            ProductionSkillRole.WRITER,
            ProductionSkillRole.CRITIC,
        ),
        strong_trigger_terms=(
            "悬疑",
            "秘密",
            "说谎",
            "不相信",
            "mystery",
            "secret",
            "lying",
        ),
        supporting_trigger_terms=(
            "录音",
            "文件",
            "记忆",
            "办公室",
            "recording",
            "document",
            "memory",
            "office",
        ),
        guidance=(
            "When using a mystery premise, make the opening anomaly, visible payoff, emotional fantasy, "
            "and episode engine specific enough that the hook could not describe many interchangeable "
            "office, document, recording, memory, or relationship-suspicion stories."
        ),
    ),
    ProductionExperience(
        id="grounded-institutional-causality",
        profiles=(ProductionProfile.DOUYIN_SHORT_DRAMA,),
        roles=(
            ProductionSkillRole.CONCEPT,
            ProductionSkillRole.WRITER,
            ProductionSkillRole.CRITIC,
        ),
        strong_trigger_terms=(
            "合同",
            "签名",
            "签约",
            "授权",
            "审批",
            "担保",
            "责任",
            "法律",
            "协议",
            "contract",
            "signature",
            "authorization",
            "approval",
            "guarantee",
            "liability",
            "legal",
        ),
        guidance=(
            "For an institutional or legal mechanism, prefer an observable permission, action, and human "
            "consequence that ordinary viewers can understand. Do not manufacture causality through borrowed "
            "signatures, doubtful liability, or invisible specialist rules unless the source explicitly needs them."
        ),
    ),
    ProductionExperience(
        id="simplify-procedural-overload",
        profiles=(ProductionProfile.DOUYIN_SHORT_DRAMA,),
        roles=(ProductionSkillRole.WRITER, ProductionSkillRole.CRITIC),
        strong_trigger_terms=(
            "阈值",
            "付款",
            "支付",
            "结算",
            "账户",
            "流程",
            "文件链",
            "threshold",
            "payment",
            "settlement",
            "account",
            "procedure",
            "waterfall",
        ),
        guidance=(
            "If the plot depends on several thresholds, documents, accounts, payment steps, or procedural "
            "clauses, collapse them to the smallest audience-legible mechanism and dramatize the choice and "
            "visible consequence instead of adding more explanation."
        ),
    ),
    ProductionExperience(
        id="separate-consequence-domains",
        profiles=(ProductionProfile.DOUYIN_SHORT_DRAMA,),
        roles=(ProductionSkillRole.WRITER, ProductionSkillRole.CRITIC),
        strong_trigger_terms=(
            "融资",
            "投资",
            "赔偿",
            "还款",
            "financing",
            "investment",
            "repayment",
        ),
        supporting_trigger_terms=(
            "合作",
            "信任",
            "关系",
            "partnership",
            "trust",
            "relationship",
        ),
        guidance=(
            "Keep direct harm, financing or partnership, professional trust, governance, and private "
            "relationship outcomes causally separate. Repairing or repaying one harm must not automatically "
            "restore every other outcome."
        ),
    ),
)


def production_skill(profile: ProductionProfile) -> ProductionSkill:
    """Return the selected static skill without restricting the global Tool catalog."""

    return _SKILLS[profile]


def production_skill_catalog() -> tuple[ProductionSkill, ...]:
    """Expose the small built-in catalog to the Agent harness and tests."""

    return tuple(_SKILLS.values())


def production_experience_catalog() -> tuple[ProductionExperience, ...]:
    """Expose the bounded experience library for inspection and regression tests."""

    return _EXPERIENCES


def retrieve_production_experiences(
    profile: ProductionProfile,
    *,
    roles: tuple[ProductionSkillRole, ...],
    context: str,
) -> tuple[ProductionExperience, ...]:
    """Retrieve only experiences lexically supported by the current production context."""

    normalized = context.casefold()
    if not normalized.strip():
        return ()
    requested_roles = set(roles)
    ranked: list[tuple[int, ProductionExperience]] = []
    for experience in _EXPERIENCES:
        if profile not in experience.profiles or not requested_roles.intersection(experience.roles):
            continue
        strong_matches = sum(
            1
            for term in experience.strong_trigger_terms
            if term.casefold() in normalized
        )
        supporting_matches = sum(
            1
            for term in experience.supporting_trigger_terms
            if term.casefold() in normalized
        )
        if strong_matches or supporting_matches >= experience.minimum_supporting_matches:
            ranked.append((strong_matches * 10 + supporting_matches, experience))
    ranked.sort(key=lambda item: (-item[0], item[1].id))
    return tuple(experience for _matches, experience in ranked)


def compile_production_prompt(
    profile: ProductionProfile,
    *,
    roles: tuple[ProductionSkillRole, ...],
    context: str = "",
    include_constitution: bool = True,
) -> CompiledProductionPrompt:
    """Compile the stable skill layers needed by one Agent call."""

    skill = production_skill(profile)
    resolved_roles = tuple(dict.fromkeys(roles))
    policy_by_role = {
        ProductionSkillRole.CONCEPT: skill.concept,
        ProductionSkillRole.WRITER: skill.writer,
        ProductionSkillRole.PLANNER: skill.planner,
        ProductionSkillRole.CRITIC: skill.critic,
        ProductionSkillRole.REVIEW_ROUTER: skill.review_router,
        ProductionSkillRole.DIRECTOR: skill.director,
        ProductionSkillRole.ASSET_REVIEWER: skill.asset_reviewer,
        ProductionSkillRole.ROUTE_PLANNER: skill.route_planner,
        ProductionSkillRole.VIDEO_PROMPT_COMPILER: skill.video_prompt_compiler,
        ProductionSkillRole.EPISODE_REVIEWER: skill.episode_reviewer,
    }
    parts = [f"Active Production Skill: {skill.label}."]
    if include_constitution:
        parts.append(f"Production constitution: {skill.constitution}")
    parts.extend(
        f"{role.value.replace('_', ' ').title()} rubric: {policy_by_role[role]}"
        for role in resolved_roles
    )
    experiences = retrieve_production_experiences(
        profile,
        roles=resolved_roles,
        context=context,
    )
    if experiences:
        parts.append(
            "Relevant retrieved production experience follows. Apply it only where the supplied "
            "context provides evidence; it is advisory and never overrides the user's source: "
            + " ".join(f"[{item.id}] {item.guidance}" for item in experiences)
        )
    return CompiledProductionPrompt(
        profile=profile,
        roles=resolved_roles,
        experience_ids=tuple(item.id for item in experiences),
        text=" ".join(parts),
    )
