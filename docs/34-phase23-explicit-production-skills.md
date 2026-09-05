# Phase 23：显式 Production Skills 与建议式 Playbook（SUPERSEDED）

> 本文只保留 Phase 23 的历史实现记录，不再描述当前活动 Skill Catalog 或 Agent Team 链路。现行边界见 [Agent Skill + Tool Runtime 最终架构](40-agent-skill-tool-runtime-migration.md) 和 [MiniMax Design、Agent Team 与 H3 Skills 蓝图](43-minimax-design-agent-team-product-blueprint.md)。

## 现行边界

- `ProductionProfile` 仅保留通用视频、AI 短剧、数字人和动漫的本地 schema/profile 适配；`AI_SHORT_DRAMA` 不再携带自创的爽剧、节奏、Critic 或 Repair 方法。
- 活动 Production Method Catalog 包含 MiniMax Design 3.0.11 的 3 个项目 Workflow adapter 与 MiniMax-H3 的 8 个任务 Method adapter。Design 的 80 个 active Workflow 文件从本机安装包原样保存并排除 `_disabled/`；H3 的 23 个 Markdown/TXT 和 8 个 `meta.yaml` 文件按 pinned commit `d21241f0a4b3acbb34c97dae47fa417b7065e438` 原样 vendored。adapter 只按角色加载上游原文或忠实中译的精确 section。
- 原有 7 个 `PROJECT_SYNTHESIS` Method 和 3 个自创 Narrative Skill 已退出活动 Catalog，当前 Narrative Catalog 为空。`drama-series` 已提供通用短剧/漫剧主骨架，不再把通用真人短剧、动态漫或爽剧整体记为能力缺口。
- 现行责任拓扑是 `Leader → Worker → Verifier`。剧本作业中由 Production Manager/AgentRun 调度 Story Architect/Writer Worker，再由 Verifier 验收；`SCRIPT_CRITIC`、`STORY_REPAIR`、`REVIEW_ROUTER`、`REVISION_VERIFIER` 和 `FINAL_COLD_READ` 仅为历史持久化状态兼容名，不再各自建立活动 Agent/Skill。
- 上游源文中的 Hub/Canvas 交互、强制确认、固定时长和固定重试次数保留为来源内容，不进入当前 Harness 执行合同。

## Phase 23 当时的结论

Phase 23 将创作能力表达为 `Agent Harness + Production Skills + Tools`，不改造成要求用户逐节点操作的 workflow 产品。

当时 `ProductionProfile` 同时承担持久化 Skill ID，静态 Catalog 返回显式 `ProductionSkill`。Skill 包含品类 constitution、角色策略、经验和可覆盖的建议式 playbook，但不拥有 Provider 调用、任务状态、版本依赖或时间线副作用，也不限制全局 Tool Catalog。

## Phase 23 当时的分层

| 层 | 当时职责 |
| --- | --- |
| Production Skill | 内容品类的创作目标、角色策略、上下文偏好、评审归属和建议 Tool 组合 |
| Agent Tool | 剧本检索/局部 Patch、资产检索与派生、图像和视频生成、观察、影响分析、时间线更新 |
| Agent Harness | 根据用户目标、当前工件和 Tool 结果选择、跳过、重复或组合能力 |
| PlanningRun / AutonomousRun | 默认无人值守推进、进度保存、恢复和 Provider 对账 |
| 确定性代码 | Schema、真实 ID、依赖闭包、H3 4–15 秒请求边界和时间线可执行性 |

当时的建议 playbook 包含“来源合同 → 全局 Blueprint → 按需逐场写作/修改 → Critic/修订路由 → 场景导演 → 按镜取用资产 → 视频生成/观察 → 时间线复核”。这些名称只用于说明历史链路；当时 playbook 也不是可执行 DSL、Tool allowlist 或审批门。

## Phase 23 当时的实现

- `ProductionProfilePolicy` 当时收敛为显式 `ProductionSkill`；
- `production_skill(profile)` 是当时的运行时选择入口；
- `production_skill_catalog()` 暴露当时的内置静态目录；
- 通用视频与抖音短剧共享建议 playbook 和 Tool Catalog，但使用品类特化的 Concept、Writer、Planner、Critic、Review Router、Director、Asset Reviewer、H3 Route、H3 Prompt Compiler 和 Episode Reviewer 角色策略；
- 抖音短剧 Skill 当时将高密度解释为单位屏幕时间内持续发生可见事件、动作、反应、选择和局势变化；
- Scene Writer 当时允许真人口语中的主语、代词、语气词、打断、情绪性重复和未完成句；
- Critic 当时分开评价视听节奏和对白自然度，不形成硬门禁；
- `revise_script_lines` 支持 Agent 或用户一次提交多句局部对白修改，并使旧 H3 路由失效；
- `user_overridable=true` 表达建议不能阻止用户继续原方案。

## Phase 23 当时的范围与验证

- PoC 当时不建设动态 Skill Registry、插件发现、Skill 服务或通用 workflow 引擎，以 Python 静态 Catalog 验证分层；
- PlanningRun 以普通代码保存默认恢复步骤，不把 `recommended_playbook` 解释为硬状态迁移；
- 当时静态 Catalog 同时提供 `GENERAL_VIDEO` 与 `AI_SHORT_DRAMA`，两者允许用户覆盖并共享可组合能力，而非 Tool allowlist；
- 当时定向回归 55 项、后端全量回归 750 项、Pyright 0 error，TypeScript strict typecheck 与前端/Electron/preload 生产构建通过。这些只是 Phase 23 时点的工程记录，不用于证明当前 Catalog 或成片质量。
