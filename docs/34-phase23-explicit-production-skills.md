# Phase 23：显式 Production Skills 与建议式 Playbook

> 后续状态：Phase 28 已把单体 Production Skill Prompt 拆成稳定 constitution、角色级 rubric 和按当前上下文召回的经验层，并补齐资产、H3 与整集 Review 的角色注入。本文保留 Phase 23 建立显式 Skill Catalog 时的实现记录。

## 结论

创作能力继续采用 `Agent Harness + Production Skills + Tools`，不改造成要求用户逐节点操作的 workflow 产品。

`ProductionProfile` 仍是项目中持久化的 Skill ID；运行时通过静态 Catalog 得到显式 `ProductionSkill`。Phase 28 后，Skill 包含稳定品类 constitution、按职责选择的角色策略、按当前上下文召回的经验，以及一份可由 Agent 或用户覆盖的建议式 playbook。它不拥有 Provider 调用、任务状态、版本依赖或时间线副作用，也不限制全局 Tool Catalog。

## 分层边界

| 层 | 职责 |
| --- | --- |
| Production Skill | 内容品类的创作目标、角色策略、上下文偏好、评审归属和建议 Tool 组合 |
| Agent Tool | 剧本检索/局部 Patch、资产检索与派生、图像和视频生成、观察、影响分析、时间线更新等可独立调用能力 |
| Agent Harness | 根据用户目标、当前工件和 Tool 结果选择、跳过、重复或组合能力 |
| PlanningRun / AutonomousRun | 默认无人值守推进、进度保存、重试、恢复和 Provider 对账 |
| 确定性代码 | Schema、真实 ID、依赖闭包、H3 4–15 秒请求边界和时间线可执行性 |

Skill 中的 playbook 只是默认制片建议，例如：来源合同 → 全局 Blueprint → 按需逐场写作/修改 → Critic 与最小责任方路由 → 场景导演 → 按镜取用资产 → 视频生成/观察 → 时间线复核。它不是可执行 DSL，也不作为允许列表或审批门。用户直接指定场次、镜头、资产或修改范围时，Agent 可以跳过前序建议并直接调用对应 Tool。

## 实现

- 原 `ProductionProfilePolicy` 已收敛为显式 `ProductionSkill`；
- `production_skill(profile)` 是运行时选择入口；
- `production_skill_catalog()` 暴露当前内置静态目录；
- 通用视频与抖音短剧共享同一建议 playbook 和同一 Tool Catalog，但 Concept、Writer、Planner、Critic、Review Router、Director、Asset Reviewer、H3 Route、H3 Prompt Compiler 和 Episode Reviewer 使用各自品类的角色策略；
- 抖音短剧 Skill 将高密度定义为单位屏幕时间内持续发生可见事件、动作、反应、选择和局势变化，不把它解释成每句对白的词汇密度；节奏优先由表演调度、动作并行、镜头推进和剪辑机会承担；
- Scene Writer 允许真人口语中的主语、代词、语气词、打断、情绪性重复和未完成句，不得把场次契约、连续性规则、关系状态或制片说明直接压缩成角色口令；
- Critic 分开评价视听节奏和对白自然度，并采用建议式朗读检查识别电报式口令、书面标语和结构化状态泄漏，不形成硬门禁；
- `revise_script_lines` 支持 Agent 或用户一次提交多句局部对白修改，仅生成一个新的 ScriptVersion；若尚有活动中的 H3 自动运行，会使旧路由失效并回到 `PLAN_ROUTES`，避免使用旧台词生成视频；
- 剧本规划、逐场写作、语义修改和 Director 修复等现有调用均从选中的 Skill 获取策略；
- `user_overridable=true` 是 Skill 的固定语义，建议不能阻止用户继续原方案。

## 当前范围

- PoC 不建设动态 Skill Registry、插件发现、Skill 服务或通用 workflow 引擎；静态 Catalog 足以验证架构；
- 数字人口播等后续品类新增独立 Skill，不复制项目、资产、Take、任务或时间线核心；
- 当前 PlanningRun 仍以普通代码保存默认恢复步骤。它消费各创作 Tool 中注入的 Skill 策略，但不会把 `recommended_playbook` 解释为硬状态迁移；
- 若未来需要 Agent 自主重新规划整条制片路径，应让 Harness 记录实际 Tool Call 与结果，而不是不断扩张 `PlanningRunStep` 或为每种修改增加专用 workflow。

## 验证

- 静态 Catalog 同时提供 `GENERAL_VIDEO` 与 `DOUYIN_SHORT_DRAMA`；
- 两个 Skill 都明确允许用户覆盖，并共享可组合能力而非 Tool allowlist；
- 两种 Skill 的 Writer 等领域策略保持不同；
- 既有 Planner、分层剧本、Reference Planner 和语义修改回归保持通过。
- 定向回归 55 项通过，后端全量回归 750 项通过，Pyright 0 error；TypeScript strict typecheck 与前端/Electron/preload 生产构建通过。
