# AI 原生短剧 Agent 制片系统

当前最终产品形态和实施顺序见 [AI Agent 视频制片桌面产品 PRD 与里程碑](product-prd-and-roadmap.md)。本文件说明当前工程能力；旧 Phase 编号只保留为实现历史，不再定义后续产品路线。

本仓库已经以 LTX Desktop `v1.1.0` 建立 Phase 0 上游工程基线，完成 Phase 1–11 的供应商中立 Mock、MiniMax-H3 768P、GPT Image 2、通用 Director、从锁定剧本到单集成片、音频 QA 和可选角色声线输入。Phase 12–21 建立跨剧本资产、Production Skills、多分钟 PlanningRun、H3 长篇衔接、整集 Reviewer、Critic 路由、来源诊断与语义修改控制面；Phase 22–28 补齐分层 Tools、视频生成前质量工具和分层 Production Skill 注入。Phase 29 将最终控制面收敛为 `Production Manager + Markdown Skills + Typed Tool Registry + Dynamic AgentRuntime`。General、AI 短剧、数字人与动漫共享同一业务核心和 Tool 合同；类型 Skill 只描述目标、创作维度与角色判断，具体视觉和节奏由 Agent 写入项目 Bible Style，不由 Profile 或 Tool 硬编码。中文共享生产方法位于 `backend/short_drama/skills/workflows/end_to_end.md`。桌面端默认新建 GUIDED/AUTO_DRAFT 已转为 AgentRun bootstrap；已配置语言模型的长篇 `AUTO_DRAFT` 会由 `project.create` 建立同一项目 ID 的 `INPUT` scaffold 和 AgentRun 拥有的 Content Planning Job，再由 `planning.advance` 每个 Action 推进一步，完成后回到动态 Agent 制片。当前代码与 SQLite 继续使用兼容名 `PlanningRun`；独立 PlanningRun 与旧 AutonomousRun 的公开控制面已删除，只保留内容规划游标和恢复所需中间工件。ProjectDocument 是唯一项目内容真相，当前不抽象通用 `ChildJob`；Provider 对账由各自任务状态机负责。

当前方法层已分为 MiniMax Design 3.0.11 的 3 个项目 Workflow（`drama-series`、`ad-tvc`、`mv`）与 MiniMax-H3 的 8 个任务 Method。`AI_SHORT_DRAMA` 仍只是 schema/profile adapter，通用短剧/漫剧方法主骨架由 `drama-series` 提供。Design 的 Canvas/Hub/Stage Plan 只映射到现有 ProjectDocument/ProductionDocument、AgentRun/PlanRevision/WorkOrder 和派生 ProductionBoardView，不替换本地 Harness。Phase 30 进一步将分镜后的核心资产决策收敛为版本化 `AssetPlan`，并补齐 canonical master、补充参考与 H3 有序引用语义。真实 AgentRun 规划验证已以 `PASSED_PLAN_ONLY` 完成：约 6 分 10 秒内自主产出 9 个 `ANCHOR`、6 个 `TEXT_ONLY`、0 个 `REUSE_EXISTING` 和 11 个媒体工作项；本轮未配置图像 Provider，因此只证明了计划闭环，尚未证明真实媒体闭环。

## 结论先行

用户体验可以是“什么都不给、只给几张图、一句话或一份剧本，点一次就得到完整短剧”；但系统内部不应把它建模为一次长视频抽卡，而应自动运行一条可回溯的制片流水线：

`创意/剧本 → 剧本与风格约束 → 完整分镜与 ClipGroup → AssetPlan → 角色/场景/道具资产 → H3 生成单元 → 候选 Take 与定向修改 → 时间线合成 → 字幕/声音/成片检查`

GPT Image 2、MiniMax-H3 和 Azure Speech 是**可用能力池，不是形式上的必选清单**。系统不以“用上哪些模型”为成功标准，而以最终成片质量、跨镜连续性和可控性为标准。某项能力只要能提高质量，或至少不降低质量并提高一致性、可控性或稳定性，就可以进入默认链路；不要求先看到失败才启用。成本只做运营记录，不主导路线选择。

首版按**单用户、受信环境**设计，不做注册、登录、账号、角色权限、租户、团队协作和分享权限。内容状态中仍可记录剧本、资产、分镜或候选的批准结论，但运行是否停下服从用户意图：已明确值直接采用，明确无交互/交由 Agent 决定则继续并记录，明确要求确认才在对应语义停靠，其余只就实质信息缺口询问。这不是用户权限审批。模型供应商 API Key 仍需作为运行配置保存，因为它属于外部调用凭证，而不是产品登录系统。

即梦、可灵、LTX Studio 和纳逗 Pro 仅作为产品与制作方法的研究对象，不是可用执行器，最终方案不得依赖其产品、账号或项目文件。当前视频生成只接 MiniMax-H3，并验证其 Direct、Frame、Reference、原生人声、对白、音效与口型；Azure Speech 可在不降低音画质量且能增强文本、声线或时间控制时加入。GPT Image 2 若能让角色、场景、构图或镜头衔接更强，或质量持平但一致性更稳定，就直接使用；只有实测造成风格损失、表演受限或下游视频更差时才跳过。

成熟开源项目优先直接复用，闭源部分只学习产品方法：

- LTX Desktop 已确定为首选代码底座与未来 fork 来源，重点保留桌面应用壳、多 Take 交互、短剧生产时间线、媒体管理与 FFmpeg 导出；不复用其模型驱动生成后端；
- Jellyfish 只参考项目、资产、分镜准备度和持久化任务语义，并作为 LTX 结构性失败时的回退；
- Wind Comic 最适合参考竖屏成片、字幕、FFmpeg 合成、质量回路和成本记录；
- LumenX 只参考模型目录、生成工作台和候选管理，不进入底座实现；
- Toonflow 最适合复用短剧编剧与分镜 Skill；
- ViMax 最适合复用首尾帧拆解和机位连续性思路；
- MiniMax Design 的开源 ComfyUI Plugin 可在几分钟成片 PoC 后直接移植为媒体执行层，但不接管项目主控；外层 Design 语义画布未确认开源，应基于现有工件和派生关系实现等价体验。

首版采用“LTX Desktop 应用与编辑底座 + 自建 Agent 制片核心”的本地桌面方案。几分钟成片 PoC 与最终视频生成主链都使用 H3；最终产品复用 LTX 时间线、媒体管理和导出能力，但不启用、不集成、不分发 LTX 模型权重。需要新媒体的局部重做由 Agent 映射到 H3 路线。

## 文档导航

1. [调研总览与证据规则](README.md)
2. [目标、方法与成功标准](01-method-and-success-criteria.md)
3. [可用能力池与供应商基线](02-provider-capability-baseline.md)
4. [闭源平台拆解](03-closed-platforms.md)
5. [开源源码审查与复用建议](04-open-source-code-review.md)
6. [固定输入实验与验收方案](05-experiment-plan.md)
7. [当前架构与技术设计](06-candidate-architecture-hypotheses.md)
8. [当前结论与待讨论决策](07-decisions-and-open-questions.md)
9. [逐项确认决策账本](08-decision-register.md)
10. [来源与源码快照](sources.md)
11. [Phase 0：LTX Desktop 上游基线](09-phase0-upstream-baseline.md)
12. [Phase 1：供应商中立 Mock 垂直切片](10-phase1-mock-vertical-slice.md)
13. [Phase 2：MiniMax-H3 768P Provider 与真实证据门](11-phase2-minimax-h3-provider.md)
14. [Phase 3：来源中立资产工作台与 GPT Image 2](12-phase3-asset-workbench.md)
15. [Phase 4：H3 视觉路线质量实验](13-phase4-h3-visual-route-experiment.md)
16. [成熟 AI 短剧制作法与参考资产包](14-mature-production-workflow-and-reference-packs.md)
17. [Phase 5：无人值守运行器与视频观察工具](15-phase5-autonomous-runner-and-video-observation.md)
18. [Phase 6：真实 H3 制片路线与整集复用闭环](16-phase6-real-route-planner.md)
19. [Phase 7：从零真人 AUTO_DRAFT](17-phase7-real-autonomous-draft.md)
20. [Phase 8：首次真实无人值守 AUTO_DRAFT](18-phase8-real-autonomous-validation.md)
21. [Phase 9：自动音频、对白与声线 QA](19-phase9-audio-dialogue-qa.md)
22. [Phase 10：独立重复、连续性收敛与三版盲评](20-phase10-independent-repeats-and-blind-review.md)
23. [Phase 11：可选角色声线输入](21-phase11-optional-character-voice-input.md)
24. [Phase 12：跨剧本资产 Agent 与同源场景视图](22-phase12-cross-script-asset-agent.md)
25. [Phase 13：通用视频与抖音短剧制作策略](23-phase13-production-profiles.md)
26. [Phase 14：多分钟短剧的分层 Multi-Agent 规划](24-phase14-long-form-multi-agent-planning.md)
27. [Phase 15：可恢复的长篇剧本规划](25-phase15-recoverable-long-form-planning.md)
28. [Phase 16：跨剧本审稿历史与修订效果](26-phase16-script-quality-history.md)
29. [Phase 17：独立 Revision Judge 与版本建议](27-phase17-revision-judge.md)
30. [Phase 18：H3 连续性视频参考与机位预演](28-phase18-h3-video-reference-previs.md)
31. [Phase 19：多分钟无人值守制片衔接](29-phase19-long-form-autodraft.md)
32. [Phase 20：整集 Reviewer 与 Director 闭环](30-phase20-episode-review-agent.md)
33. [Phase 21：三分钟真人短剧最终剧本](31-phase21-real-three-minute-episode.md)
34. [Phase 21：Skill 驱动审阅路由与用户语义修改控制面](32-phase21-skill-review-and-change-control.md)
35. [Phase 22：统一分层剧本 Tools 与局部 Patch](33-phase22-hierarchical-script-tools.md)
36. [Phase 23：显式 Production Skills 与建议式 Playbook](34-phase23-explicit-production-skills.md)
37. [Phase 24：视频生成前质量优化调研](35-phase24-pre-h3-quality-research.md)
38. [Phase 25：H3 Prompt IR、顺序候选与相对选片](36-phase25-h3-prompt-and-adaptive-takes.md)
39. [Phase 26：成熟平台、前沿论文、开源实现与 H3 低重试策略](37-phase26-platform-paper-open-source-low-retry-research.md)
40. [Phase 27：生成前视觉输入质量 Tools](38-phase27-visual-input-quality-tools.md)
41. [Phase 28：Production Skill 分层与统一角色注入](39-phase28-production-skill-layering-and-injection.md)
42. [Phase 29：Agent / Skill / Tool 运行时迁移与最终架构](40-agent-skill-tool-runtime-migration.md)
43. [制片 Skill 研究、深化与 Provider 分层](41-production-skill-research-and-enrichment.md)
44. [AI 原生短剧的创作形态、观众行为与 Skill 落地](42-ai-short-drama-forms-behavior-and-rhythm.md)
45. [Agent Tool / Artifact 就绪度核对](44-agent-tool-artifact-readiness-audit.md)
46. [从完整分镜到 H3 生成单元的后端闭环](45-storyboard-generation-unit-backend-closure.md)
47. [Phase 30：核心资产计划与多参考闭环](46-phase30-core-asset-plan-and-reference-pipeline.md)

## 当前边界

- 已做：官方资料与开源源码审查、固定样片、统一 SQLite/API 核心、可恢复 Mock/H3 Worker、多 Take、局部返工、TimelineManifest、字幕/音频与 9:16 导出；资产工作台、Azure GPT Image 2、本地 GPT-5.6-sol；四种输入归一化、通用 Director/Route Planner、长镜头与镜间观察、自动选 Take 和直接拼接导出；可选角色声线输入；跨项目资产搜索/导入、母版与同源派生视图、覆盖检查和逐镜 `ReferencePlan`；通用视频/AI 短剧 Production Skills 与项目级自由 Style；任意目标时长、整集大纲、场次契约、逐场 Director、连续性 handoff、由 AgentRun 总调度的可恢复 Content Planning Job（当前兼容实现名 `PlanningRun`）、每轮大纲/审稿工件、跨剧本修订轨迹和条件触发的 Revision Judge；H3 前序 Take/尾窗/机位预演 Tool、同场 continuity wave 和真实 768P 对照；长篇 FINALIZE→AUTO_DRAFT 同一运行时衔接、每镜返工额度、2 分钟重启导出和 5 分钟 20 镜离线规模验证；分层 Scene/Episode Reviewer、关联 Director 局部返工、重启恢复和不阻断导出语义；三次同剧本真实 `AUTO_DRAFT` 均完成 9 镜、54.31 秒成片，新剧本《明天别来了》完成 8 镜、58.00 秒真实跨剧本成片；另已完成默认 AgentRun bootstrap、统一 Tool Registry、四类 Markdown Skills、带 `goal_complete` 反馈的动态 PlanGraph、跨 Run 决策召回与经验沉淀/去重、字符预算 ContextPack、WorkOrder/AgentResult 合同校验、可调用共享 Tools 的专业 Agent、本地 Planner fallback 与语义变更局部 apply；Phase 30 的 AgentRun 资产规划实跑已通过 `PASSED_PLAN_ONLY`，并修复 Specialist 尾随 `}` 窄恢复及复杂 AssetPlan schema 跨字段说明不足的问题。ProjectDocument 及其版本化工件仍是唯一项目内容真相；当前不抽象通用 `ChildJob`。
- 不做：把闭源平台接入或作为生产执行器；不做注册、登录、RBAC、多租户、团队权限、抖音账号接入、自动发布和运营管理；不增加创意或 Tool 门禁、冻结层、SHA256 锁定、对抗评测或密码学证据系统。尚未完成：复杂多人对白、参考声线、数字人专用口型/主播和动漫专用生成路线的跨项目真实质量验证。
- 下一步：先让 Agent 按已补充的多页文件、精确文字图版与 Shot/ClipGroup 索引语义局部修订 AssetPlan v1；完成确定性文字排版/合成执行器。然后在用户明确选择并授权图像执行方式后，继续验证 Phase 30 的图片生成、审看、ReferencePack 与逐镜绑定媒体闭环。随后在新 AgentRuntime 上累积真实用户任务的委派粒度、重新规划和局部重算样本，并以真实任务验证三个 MiniMax Design Workflow 与八个 MiniMax-H3 Task Method 的 Agent-native 选择和组合。H3 尚未暴露的模式、数字人/动漫专用 Provider 能力仅在有明确质量目标和用户授权时进一步接入；通用短剧已由 `drama-series` 提供创作主干，后续只把两层成熟方法与现有本地能力都未覆盖的具体需求记录为缺口。
