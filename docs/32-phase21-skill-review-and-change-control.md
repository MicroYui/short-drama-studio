# Phase 21：Skill 驱动审阅、来源诊断与 AI-native 修改控制面

## 目标

本轮解决四个通用问题：

1. Script Critic 只提供观察，不通过一个布尔值直接控制改稿；当前 Production Skill 决定审阅重点和最小修复归属。
2. 不把 Critic 当答案。系统在改稿前独立判断问题来自用户原始概念、当前大纲，还是 Critic 越界。
3. Writer 不能为了降低制作复杂度，静默删除用户明确要求的人物、事件、冲突规模或结果。
4. 无人值守生成与用户随时修改共用 Agent Tools、版本和依赖语义，不为剧本、资产、镜头或“全片换衣服”各建一条固定 workflow。

本轮真实验证只使用语言模型，没有调用 GPT Image 2 或 MiniMax-H3。

## 剧本生成与审阅闭环

```text
用户输入
  → Story Architect：生成当前 EpisodeOutline
  → Script Critic：报告文本中可定位的观察
  → Review Router Agent：按当前 Production Skill 路由到最小修复层
      → STORY_ARCHITECT
      → SCENE_DIRECTOR / ASSET_AGENT / VIDEO_DIRECTOR / TIMELINE_AGENT
      → USER_OPTIONAL
  → Source Contract Agent：独立提取显式承诺、明确禁区和开放选择
  → Story Repair Planner：逐项诊断问题来源并给出最小修复计划
      → SOURCE_CONCEPT_GAP
      → OUTLINE_DEFECT
      → CRITIC_OVERREACH（不得触发自动改稿）
  → Story Architect Writer：只执行 should_revise=true 的动作
  → Story Revision Verifier：只检查计划执行、来源承诺和新增回退
  → Script Critic 复审
  → 继续、用户修改或把建议下放后续 Agent
```

这个闭环不是强门禁：`StorySourceContract`、`StoryRepairPlan`、`StoryRevisionVerification` 和 `ScriptReviewDisposition` 均明确 `user_overridable=true`。用户可以采用、修改或忽略建议。

### Critic 与 Router

`ScriptReviewReport.requires_revision` 仅为兼容旧快照保留，编排不再读取它。Router 输出：

- `REVISE_STORY`：确实需要改变因果、动机、场次契约或有效时长分配；
- `CONTINUE_WITH_HANDOFF`：剧本可继续，问题交给后续制作层；
- `CONTINUE`：没有可执行问题。

严重度描述 Critic 的判断，不是执行门槛。HIGH 机位问题也不能靠 Writer 加对白解决；MEDIUM 因果缺口仍可能需要 Story Architect。Router 不可用时，Harness 把观察降级为 `USER_OPTIONAL`，不会沿用 Critic 的旧布尔值擅自重写。

Critic 输出若只是 JSON 尾逗号或 schema 结构错误，Harness 会进行一次只修结构的重试，保留原审阅语义；不会因为序列化错误丢掉整份审阅。

### Source Contract

Source Contract Agent 不写故事，只区分三类信息：

- `explicit_commitments`：用户明确要求发生的参与者、事件、因果顺序、冲突规模、结果、题材和时长承诺；
- `explicit_prohibitions`：用户明确禁止的内容；
- `open_creative_choices`：Agent 可以自行决定的实现细节。
- `authoritative_user_overrides`：用户后续直接改稿或明确发给 Agent 的修改指令；与更早承诺冲突时以后者为准。

修订 Writer 可以简化一个承诺的拍法，但不能静默删除承诺本身。例如“闺蜜带着被骗买家闯入并争抢冷气”不能为了减少群演而改成只有一对父女；可以改为少量功能明确的买家，仍保留群体压力。

### Repair Planner 与 Revision Verifier

Repair Planner 读取当前大纲、Router 指定的剧本问题、Source Contract，以及最近三轮修订历史。它必须对每项问题诊断一次，并明确：

- 是否真的应改；
- 应删、替换、简化或重分配什么；
- 已经成立的内容和来源承诺必须保留什么；
- 修订后应回答哪些验证问题。

Writer 只接收 Repair Plan 中 `should_revise=true` 的动作。Revision Verifier 不是第二个开放式 Critic，只检查：

- Writer 是否落实了 Repair Plan；
- 是否保留 Source Contract 与计划中的 invariants；
- 是否新引入因果、资源状态、时序、动机或信息密度回退。

若失败，最多进行两次针对具体 violation 的定向修复；仍失败则保留原大纲并交给用户决定，不无限重写。

## 这次实验究竟是谁的问题

对三分钟“极端热浪与假极寒舱”剧本的真实修订记录给出了清楚答案：

| 现象 | 责任判断 | 系统改进 |
|---|---|---|
| 一版把“多名被骗买家”缩成单一父女 | Repair Planner 与 Writer 为降低制作复杂度造成的来源漂移，不是 Critic 过严 | 增加独立 Source Contract，并在 Repair Planner、Writer、Verifier 中持续传递 |
| Writer 一度写出供电状态前后冲突 | Writer 执行修复时引入的新回退 | 增加窄范围 Revision Verifier 和有界定向修复 |
| Critic 返回正确内容但 JSON 多一个尾逗号 | 输出结构故障，不代表 Critic 语义不可用 | 增加一次保语义的 Critic JSON repair |
| 门、接头、人物空间位置等问题被要求改剧本 | 修复层级错误，容易靠解释性对白过拟合 | Review Router 下放到 Scene Director，不扩张底层故事字段 |
| 修订到后期仍有中低级建议 | 不是必须继续追到零问题 | 使用停止条件和用户可覆盖建议，避免 Critic 驱动的过度设计 |

本轮最终 Repair Plan 恢复了多人受害者、乔曼主动引路、演示舱主动弃置和场景2的阅读空间。Writer 初稿仍有一处计划偏离，Verifier 拒绝后进行 1 次定向修复才接受。最终 Critic 从上一版 5 项降到 4 项，保持 `0 HIGH`，且 Source Contract 的 16 项显式承诺全部保留。

因此结论不是“Critic 太严格”或“原始 Writer 单独有问题”，而是应分别观察：

- 初稿是否漏掉来源承诺；
- Critic 的观察能否在文本中定位；
- Router 是否选择了正确修复层；
- Repair Planner 是否提出了错误的简化方向；
- Writer 是否忠实执行计划并保持状态连续；
- 修订后的问题是否减少，还是只是换了一批新问题。

## 停止策略

不再以“Critic 零问题”作为自动停止条件。当前采用普通产品工程的有界策略：

- 自动修订最多 3 轮；用户或 supervising Agent 可显式再发起一轮；
- Source Contract 必须保留，Revision Verifier 必须接受；
- 存在 HIGH 或核心来源承诺缺失时优先继续修复；
- 无 HIGH、问题没有回升且剩余问题可下放或属于中低级优化时，可以定稿；
- Revision Judge 只在最近一版出现问题数或 HIGH 数回升时比较版本，不为追求形式完整每轮都调用；
- 所有建议均可由用户忽略、直接编辑或要求 Agent 定向修改。

## 用户与 Agent 控制面

### PlanningRun 剧本节点

- `PUT /api/short-drama/planning-runs/{run_id}/outline`：用户直接提交完整 `EpisodeOutline`；
- `POST /api/short-drama/planning-runs/{run_id}/outline/agent-revise`：用自然语言要求 Story Architect 定向修改；
- `POST /api/short-drama/planning-runs/{run_id}/outline/revise-from-review`：supervising Agent 基于最新 Router、Repair Planner 和 Verifier 再修一版；
- 修改后仅清除失效的审阅与逐场规划结果，保留修订快照并回到 `SCRIPT_CRITIC`。

用户不操作时，AUTO 模式继续自主运行；这些 API 是同一 Harness 上的可选 steering Tools，不是人工审批门。

### 已成项目的语义修改

`POST /api/short-drama/projects/{project_id}/agent-tools/plan-change` 把自然语言修改转换为 `SemanticChangePlan`：

1. Semantic Change Planner 选择当前项目中的语义根对象，例如角色林夏；
2. Harness 通过真实 ID、场次、对白、ReferencePlan、资产版本、镜头和 Take 依赖计算影响范围；
3. 返回受影响资产、台词、场次、镜头、将失效的 Take 与建议 Tool Calls；
4. 计划默认 `applies_changes=false`，可由无人值守 Agent 继续执行，也可由用户改范围、指定镜头或忽略；
5. 系统没有为换衣服、颜色或某一类场景增加专用 workflow 字段。

## 持久化

- `PlanningRunRecord` 保存 `source_contract`、`latest_repair_plan`、`latest_revision_verification` 和 `review_disposition`；
- 用户直接改稿或 Agent 定向改稿会把本次指令和当前已认可大纲写入 `authoritative_user_overrides`，避免下一轮自动修订把用户选择改回去；
- 每个 `PlanningOutlineSnapshot` 保存对应的 Source Contract、Repair Plan 与 Verification；
- 每个 `PlanningReviewSnapshot` 保存 Critic 报告和 Router 路由；
- Story revision 改变场次数量时同步刷新 `PlanningRun.total_scenes`；
- 新剧本与资产检索继续按 `project_id` / `planning_run_id` 隔离，避免跨剧本串用。

## 当前验证

- Source Contract 能从真实三分钟输入中抽取并持久化 16 项显式承诺、5 项禁区和开放选择；
- Contract 进入 Repair Planner、Writer 与 Revision Verifier 的输入；
- Critic overreach 可以被诊断为不改稿；
- Writer 回退会触发最多两次有界定向修复；
- Critic JSON 尾逗号可在不丢语义的情况下修复；
- Review Router 可把问题路由给 Scene Director 而不调用 Writer；
- 规划大纲支持直接编辑、Agent 定向编辑和基于最新审阅再修订；
- 语义修改 Tool 能从根对象确定性计算下游影响且不直接修改项目；
- 定向后端测试 49 项通过；最终后端全量回归 744 项通过；Pyright 0 error，TypeScript `tsc --noEmit` 通过。

## 后续边界

下一步不是继续润色第 8 版，而是在用户确认恢复后，让 Scene Director 消化已路由的空间与动作建议，再进入关键资产、镜头计划和 H3 生成。通用 Change Executor 仍需把 `SemanticChangePlan` 逐项交给现有 Tools，并复用现有图像/H3 异步任务状态机；数字人口播等新类型只新增 Production Skill，不改变这一控制面。
