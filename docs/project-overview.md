# AI 抖音短剧工作流调研

本仓库已经以 LTX Desktop `v1.1.0` 建立 Phase 0 上游工程基线，完成 Phase 1–11 的供应商中立 Mock、MiniMax-H3 768P、GPT Image 2、通用 Director、从锁定剧本到单集成片、音频 QA 和可选角色声线输入。Phase 12 已实现跨项目资产目录、同源派生视图与 Asset Agent Tools；Phase 13 加入通用视频和抖音短剧两套 Prompt Policy；Phase 14 允许用户填写任意目标时长，并以 Story Architect、Script Critic、逐场 Scene Director 和精简连续性 handoff 规划多分钟短剧；Phase 15 将长篇规划拆成 SQLite 持久化的 `PlanningRun`，按 Agent 阶段和场次逐步推进、失败局部重试并支持应用重启恢复；Phase 16 保留每轮大纲与审稿、提供跨剧本检索和修订轨迹；Phase 17 在最新轮次非单调变差时，由独立 Revision Judge 比较最近两版并持久化可忽略建议，不自动替用户换稿。连续性、资产、H3、QA、时间线和导出仍共享同一流程，没有增加样片专用字段或信息密度硬门控。

## 结论先行

用户体验可以是“什么都不给、只给几张图、一句话或一份剧本，点一次就得到完整短剧”；但系统内部不应把它建模为一次长视频抽卡，而应自动运行一条可回溯的制片流水线：

`创意/剧本 → 剧本与风格约束 → 角色/场景/道具资产 → 分镜与连续性计划 → 单镜头多候选生成 → 质量复核与定向重试 → 时间线合成 → 字幕/声音/成片验收`

GPT Image 2、MiniMax-H3 和 Azure Speech 是**可用能力池，不是形式上的必选清单**。系统不以“用上哪些模型”为成功标准，而以最终成片质量、跨镜连续性和可控性为标准。某项能力只要能提高质量，或至少不降低质量并提高一致性、可控性或稳定性，就可以进入默认链路；不要求先看到失败才启用。成本只做运营记录，不主导路线选择。

首版按**单用户、受信环境**设计，不做注册、登录、账号、角色权限、租户、团队协作和分享权限。内容确认仍然保留，但它表示剧本、资产、分镜或候选是否获准进入下一生产阶段，不是用户权限审批。模型供应商 API Key 仍需作为运行配置保存，因为它属于外部调用凭证，而不是产品登录系统。

即梦、可灵、LTX Studio 和纳逗 Pro 仅作为产品与制作方法的研究对象，不是可用执行器，最终方案不得依赖其产品、账号或项目文件。当前视频生成只接 MiniMax-H3，并验证其 Direct、Frame、Reference、原生人声、对白、音效与口型；Azure Speech 可在不降低音画质量且能增强文本、声线或时间控制时加入。GPT Image 2 若能让角色、场景、构图或镜头衔接更强，或质量持平但一致性更稳定，就直接使用；只有实测造成风格损失、表演受限或下游视频更差时才跳过。

开源项目不建议原样照搬：

- LTX Desktop 已确定为首选代码底座与未来 fork 来源，重点保留桌面应用壳、生成/多 Take/Retake/Gap Fill、短剧生产时间线、模型管理框架与 FFmpeg 导出；
- Jellyfish 只参考项目、资产、分镜准备度和持久化任务语义，并作为 LTX 结构性失败时的回退；
- Wind Comic 最适合参考竖屏成片、字幕、FFmpeg 合成、质量回路和成本记录；
- LumenX 只参考模型目录、生成工作台和候选管理，不进入底座实现；
- Toonflow 最适合复用短剧编剧与分镜 Skill；
- ViMax 最适合复用首尾帧拆解和机位连续性思路；
- ComfyUI 适合作为后期修复/本地模型的可选侧车，不适合作为项目主控层。

首版已确认采用“LTX Desktop 壳 + 自建短剧/任务核心”的本地桌面方案；Jellyfish 只作为领域/任务语义参考及结构性失败回退。LTX 模型权重不进入当前产品，视频生成固定只接 H3；H3 内部媒体执行路线仍由固定输入质量实验决定。

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

## 当前边界

- 已做：官方资料与开源源码审查、固定样片、统一 SQLite/API 核心、可恢复 Mock/H3 Worker、多 Take、局部返工、TimelineManifest、字幕/音频与 9:16 导出；资产工作台、Azure GPT Image 2、本地 GPT-5.6-sol；四种输入归一化、通用 Director/Route Planner、长镜头与镜间观察、自动选 Take 和直接拼接导出；可选角色声线输入；跨项目资产搜索/导入、母版与同源派生视图、覆盖检查和逐镜 `ReferencePlan`；通用视频/抖音短剧 Prompt Policy；任意目标时长、整集大纲、场次契约、逐场 Director、连续性 handoff、可恢复 `PlanningRun`、每轮大纲/审稿工件、跨剧本修订轨迹和条件触发的 Revision Judge；三次同剧本真实 `AUTO_DRAFT` 均完成 9 镜、54.31 秒成片，新剧本《明天别来了》完成 8 镜、58.00 秒真实跨剧本成片。
- 不做：把闭源平台接入或作为生产执行器；不做注册、登录、RBAC、多租户、团队权限、抖音账号接入、自动发布和运营管理。本阶段不新增声音 QA。尚未完成：跨项目默认路线样本门槛；复杂多人对白、参考声线的真实稳定性，以及声音来源语义的自动验证。
- 下一步：验证 H3 `reference_video` 对连续场景、强依赖镜头和长镜头续接的真实增益，并比较“前序 Take、尾部运动窗口、低保真走位/运镜预演”三类视频参考。它们只进入 Director/Route Agent 的可覆盖建议，不成为所有镜头的固定步骤。
