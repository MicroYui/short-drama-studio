# Phase 19：多分钟短剧的无人值守制片衔接

## 目标与边界

本阶段把已经可恢复的长篇剧本规划接到现有真人 `AUTO_DRAFT`，验证 2–5 分钟项目是否能在用户不操作中间节点的情况下继续完成：

`PlanningRun → ProjectDocument → Asset Agent → H3 continuity waves → 视频 QA / Director 修复 → TimelineManifest → 可选导出`

这里没有新建第二套长剧运行时，也没有把“剧本完成”变成一次人工批准。`PlanningRun.FINALIZE` 仍只负责把最终大纲和逐场计划建立为正式项目；同一次请求若为 `AUTO_DRAFT`，会同时建立原有 `AutonomousRun`，从 `PREPARE_ASSETS` 继续。`GUIDED` 仍允许用户停在剧本页修改。

本阶段只验证编排、恢复、局部返工、上下文和规模边界。没有提交 2–5 分钟真实付费 H3 长片，因为已有真实单集与逐镜 H3 证据，而一部长片主要增加费用和等待时间，不能替代状态机测试；多分钟最终画面稳定率仍需以后用正式验收剧本验证。

## 实际控制面

系统仍由四层普通产品工程组成：

- **Agent Harness**：组装当前项目、场次、镜头、资产、Take、QA 和历史修复证据，并提供资产搜索/生成、关键帧、H3 路线、视频抽帧、边界检查和时间线 Tools；
- **Prompt Policy**：为 Story Architect、Script Critic、Scene Director、Asset Agent、Route Agent、Reviewer 和 Director 注入各自职责，以及通用视频/抖音短剧的不同创作目标；
- **持久化 Orchestrator**：用 `PlanningRun`、`AutonomousRun`、任务、Take 和路线计划保存当前步骤，进行有限推进、同场连续性 wave、跨场并行、重试、恢复和 Provider 对账；
- **确定性执行检查**：只检查媒体存在、输入组合、Provider 合同、任务状态、版本新鲜度和时间线可执行性。

创作内容是否好、是否补资产、采用哪条 H3 路线、是否拆镜或换机位仍由 Agent 推荐。没有固定镜头数、创意审批门、剧本分数门、文档 SHA 或素材 hash 冻结。普通 ID、任务 fingerprint 和缓存键只用于查找、复用与避免重复提交。

## 多分钟生产链

1. `PlanningRun` 每次只运行一个语言 Agent 节点或一个 Scene Director 场次；Scene Director 只接收整集资产契约、当前场次、当前场对白、上一场实际 compact handoff 和下一场入口，不重复注入全部历史镜头。
2. `FINALIZE` 建立正式 `ProjectDocument`；`AUTO_DRAFT` 同时建立原有 `AutonomousRun`，不会复制 Project、Take、QA 或 Timeline schema。
3. Asset Agent 搜索当前/跨项目资产，复用已批准版本，或按实际覆盖缺口调用图像工具；缺少三视图或局部场景视图只是 Agent 建议，不是统一前置条件。
4. H3 Route Planner 每个 continuity wave 为每个场景选择最早尚未覆盖的一个镜头。同一场后镜等待前镜有可用 Take，以便使用边界状态或可选视频 guide；不同场可在同一 wave 并行。
5. H3 候选完成后执行视频抽帧、镜内与镜间观察、候选选择和 Director 修复。修改只使受影响镜头及依赖失效，已通过的其他镜头继续保留。
6. 全部镜头有可用 Take 后重建唯一 `TimelineManifest`，按请求决定是否执行 FFmpeg 导出。

前端把两段状态机表现为同一条用户流程：规划时显示 Agent 节点和已完成场次；正式项目建立后显示当前制片步骤、入选镜头数、任务终态数、累计自动返工轮次和单镜自动尝试上限。它不是节点连线画布。

## 修复一个长篇规模错误

原实现把 `AutonomousRun.max_repair_rounds = 3` 解释为整集共享额度。短片中通常看不出问题，但长片前面三个无关镜头各返工一次后，后面的第四个镜头会被误判为“重试耗尽”，甚至直接进入 best-effort。

现在语义改为：

- `repair_round` 只记录全剧累计发生过多少次自动返工 wave，不再有任意的 10 次上限；
- `max_repair_rounds` 表示**同一个镜头**最多自动应用多少次有来源 Take 的 Director 修复；
- 前期制片决策、继承的修复和没有来源 Take 的建议不消耗运行时额度；
- 一个镜头耗尽不会减少其他镜头的尝试次数；
- Director 没有给某个镜头可执行修复时，系统明确报告该镜头，而不是因为同 wave 中另一个镜头修复成功就继续循环；
- 现有 bounded best-effort 若被使用，拒绝状态、未解决问题、尝试次数和告警仍保留可见，不伪装成 QA 通过。

这是执行成本与防止无限循环所需的普通每镜重试边界，不是创作内容门控。

## 离线规模验证

验证使用真实 SQLite repository、`PlanningRun` / `AutonomousRun` 状态机、H3 Route Plan、媒体文件、视频检查、Take、TimelineManifest 和 FFmpeg 导出，只把外部语言、图像与 H3 Provider 换成可注入 Fake。它验证工程闭环，不代表 Fake 视频具有真实画面质量。

| 场景 | 规模与扰动 | 结果 |
| --- | --- | --- |
| 2 分钟完整交付 | 2 场、10 镜；镜头 1–4 分布在不同 wave 中各触发一次通用构图失败；首个部分 Take 产生后重建 Service | 从持久化状态继续；4 个镜头各自局部修复一次，其余镜头 revision 不变；入选镜头进度单调到 10/10；生成 120 秒 Manifest 并完成技术导出 |
| 5 分钟规模 | 4 场、20 镜、每镜 2 个 H3 Fake 候选，共 40 个请求；不执行最终导出 | 同一运行时完成 20/20 入选 Take 和 300 秒 Manifest；4 次 Scene Director 调用都只收到当前场上下文、compact handoff 与下一场入口 |
| 单镜持续失败 | 同一个镜头在每次重生成后继续报告通用构图问题 | 恰好应用 3 次 Director 修复；之后沿用现有可见 best-effort/告警语义，没有触发第 4 次自动修复，也不影响其他镜头额度 |

回归入口位于 `backend/tests/test_real_autonomous_draft.py`。其中长篇测试直接从 `PlanningRunCreateRequest` 开始，不跳过 FINALIZE→AUTO_DRAFT 衔接。

## 结论与后续

Phase 19 证明的是：多分钟剧本已经能在同一持久化 Orchestrator 中自动进入现有资产、H3、QA、局部返工、时间线和导出闭环；2–5 分钟不需要第二套 runtime、长 HTTP 请求或人工逐镜操作。

仍然未知的是多分钟真人成片的真实观看质量、Provider 长时间限流、20+ 镜身份/声线漂移累积，以及不同题材下 Route Agent 的默认稳定率。后续正式质量实验应选择一份真正适合 2–5 分钟验收的剧本，生成一版完整 H3 成片并整片观看；在此之前不为证明状态机而盲目消耗一部长片额度。
