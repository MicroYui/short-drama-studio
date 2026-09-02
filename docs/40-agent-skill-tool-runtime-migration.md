# Phase 29：Agent / Skill / Tool 运行时迁移与最终架构

## 结论

本阶段将项目的创作与制片控制面收敛为：

`Production Manager Agent + Markdown Production Skills + Typed Tool Registry + Dynamic AgentRuntime`

其中：

- **Tool** 是可独立调用的稳定能力，负责读取、修改、生成、检查或组装真实工件；
- **Skill** 是可审阅、可编辑的内容类型知识，负责品类原则、角色 Prompt 和共享流程上的类型制作重点；
- **Agent** 理解用户目标，根据当前工件动态制定或修订计划，组合 Tools，并在需要专业角色时委派子 Agent；制作类型由用户或请求选择 `ProductionProfile`，再由 Production Manager 确定对应的 Skill 映射，不由 Agent 自行更换格式 Skill；
- **Agent Harness** 只保留运行时所需的类型、依赖、持久化、恢复、有限推进和外部任务对账，不替 Agent 写死创作流程。

项目、剧本、资产、分镜、Take 和时间线仍是业务真相源。AgentRuntime 只保存“当前要做什么、为什么、做到哪里、接下来做什么”，不复制第二份 ProjectDocument。桌面端的默认新建项目和新 `AUTO_DRAFT` 制作已切到 AgentRun：调用 `POST /api/short-drama/agent-projects`，先建立 AgentRun，再由共享 `project.create` Tool 创建权威项目。

`PlanningRun` 不是第二套项目控制面。它作为 `planning.advance` Tool 背后的可恢复长规划作业：当已配置语言模型且 `AUTO_DRAFT` 目标超过 90 秒时，`project.create` 先写入同一项目 ID 的 `INPUT` scaffold 与 Agent-owned `PlanningRun`，随后 Production Manager 每个 Action 只推进一个规划单元；FINALIZE 再把同一 ProjectDocument 更新为 `PLANNED`。独立 PlanningRun 的公开 API 已删除，只允许所属 AgentRun 通过 `planning.advance` 进入这个内部子流程。旧 `AutonomousRun` 控制面、ProjectDocument 字段、领域模型、服务分支和脚本均已删除；Provider Task 继续使用自己的任务状态机完成提交、轮询、下载和结果对账。

## 1. 最终分层

```mermaid
flowchart TB
    UI["React / Electron 工作台"] --> API["FastAPI 薄路由"]
    API --> PM["Production Manager Agent"]
    PM --> CP["Context Compiler"]
    PM --> PG["Dynamic PlanGraph"]
    PM --> SK["Markdown Production Skills"]
    PM --> WO["WorkOrder / AgentResult"]
    PM --> TR["Typed Tool Registry"]
    CP --> MEM["Project / Run / Decision / Experience Memory"]
    CP --> AR["Canonical ArtifactRef"]
    TR --> DS["Domain Services"]
    DS --> DB["SQLite ProjectDocument / Tasks / Takes"]
    DS --> PP["Provider Adapters"]
    DS --> MP["FFmpeg / Media Inspection"]
    AR --> DB
```

### 1.1 交互层

用户操作的仍是项目、剧本、资产、分镜、候选 Take、检查结果和时间线。Agent 运行面向用户展示目标、当前计划、已完成动作、待处理问题和下一步，但不要求用户维护节点图。

### 1.2 Production Manager

Production Manager 是一次制片目标的根 Agent，负责：

1. 读取项目当前 `ProductionProfile` 和用户目标；
2. 编译最小必要 `ContextPack`；
3. 获取当前可用 Skill 和 Tool 描述；
4. 生成带依赖的动态 `PlanRevision`；
5. 执行已就绪 Tool Call，或通过 `WorkOrder` 委派专业角色；
6. 根据 Tool 结果、AgentResult 和用户新指令继续、跳过、并行或修订计划；
7. 在每个有意义的边界写入恢复检查点。

它不是“Story Architect 后必须进 Critic”这类固定转移表。当用户只要修改第三场的一句台词时，Agent 可以直接计划“语义影响分析 → 局部 Patch → 受影响镜头复核”，无需重跑整部剧的默认 playbook。

对应实现位于 `backend/short_drama/production_manager.py`。`LanguageProductionPlanner` 可使用已配置语言模型生成计划；用户或测试也可直接提交类型化 `AgentPlanProposal`。未配置语言模型时，`LocalProductionPlanner` 使用当前项目 stage、当前 Run 的制作目标和已注册 Tools 生成小而确定的后续计划：GUIDED bootstrap 在建立后即等待用户编辑，已有 GUIDED 项目可以另起明确的 Mock/REAL_H3 Run；Mock 路径可继续候选生成、任务推进、时间线与导出，H3 路径直接从权威 Task Repository 召回全部非终态任务，不从经过裁剪的 recent actions 反推任务集。当当前 Run 没有制作目标，或必须由人/专业 Agent 做内容判断时，它返回 `USER_INPUT`，不继承旧 Run 的执行目标，也不伪装成已完成创作。

`PlanRevision.goal_complete` 是 Agent 对“整个 Run 目标是否已满足”的显式反馈，不是“当前计划的 Steps 是否跑完”的别名。当当前 PlanGraph 完成但 `goal_complete=false` 时，Production Manager 会用最新 ContextPack 自动追加新 PlanRevision；只有 `goal_complete=true` 才将 AgentRun 置为 `COMPLETED`。同一次 advance 内的自动重规划有普通的次数上限，超过后转为 `WAITING`，避免单次 HTTP 请求无界执行。

### 1.3 AgentRuntime

AgentRuntime 是格式中立的可恢复控制面：

- `AgentRun`：目标、根 Agent、活动 Skill 和运行状态；
- `PlanRevision`：Agent 当次制定的计划版本，包含独立的 `goal_complete` 判断；
- `PlanStep`：Tool Call、Delegation、Artifact Patch、User Input 或 Checkpoint；
- `PlanGraph`：只根据依赖与动作状态计算就绪步骤，不内置内容制作顺序；
- `ActionRecord`：记录真实执行的输入摘要、结果摘要、工件引用和错误；
- `AgentRunSnapshot`：运行、当前计划、动作、记忆和待回复委派的最小恢复集。

Plan 是 Agent 可修订的工作假设，不是业务真相源。每次重新规划追加新的 `PlanRevision`，不覆盖项目工件，也不要求预先枚举所有可能的创作分支。旧持久化 PlanRevision 没有 `goal_complete` 字段时按 `false` 恢复，不需要为此写一次性数据迁移。

进程重启时，已持久化的完成、等待和用户输入状态原样恢复；遗留的 `IN_PROGRESS` WorkOrder 转为 `NEEDS_ATTENTION`，可由专业 Agent 重试。`project.create` 和 `planning.advance` 会先根据已持久化的 ProjectDocument 与 PlanningRun 游标对账：领域结果已经存在时补记 Action 完成并从最新游标继续，确实无法确认的 Tool Action 才进入等待和重新规划。

### 1.4 Agent API

新控制面通过薄 HTTP 路由对外暴露：

- `GET /api/short-drama/agent-runtime/capabilities`：查看已注册 Tools 与 Markdown Skills；
- `POST /api/short-drama/agent-projects`：默认新建/bootstrap 入口，先创建 AgentRun，再执行 `project.create`，返回项目 ID 与当前 AgentRun；
- `POST/GET /api/short-drama/projects/{project_id}/agent-runs`：创建或列出项目 AgentRun；对确定性本地制作可显式传入当前 Run 的 `execution_target` 与 `render_export`；
- `GET /api/short-drama/agent-runs/{run_id}`：读取当前 snapshot、就绪步骤和 ContextPack；
- `POST /api/short-drama/agent-runs/{run_id}/replan`：让 Agent 重新规划，或直接提交可检查的新计划；
- `POST /api/short-drama/agent-runs/{run_id}/advance`：有限执行 1–8 个已就绪动作；
- `POST /api/short-drama/agent-runs/{run_id}/user-input`：回复真正需要用户决定的步骤；
- `GET /api/short-drama/agent-work-orders/{work_order_id}`：读取委派、精简上下文和角色 system prompt；
- `POST /api/short-drama/agent-work-orders/{work_order_id}/execute`：使用已配置语言模型执行专业 Agent；
- `POST /api/short-drama/agent-work-orders/{work_order_id}/result`：提交外部、人工或测试 AgentResult。

`advance.max_actions` 是防止单次请求无界运行的普通调度边界，不是创作步骤门禁。Agent 可随时发起下一次 advance 或 replan。

### 1.5 代码责任边界

| 模块 | 拥有的责任 | 不应拥有的责任 |
| --- | --- | --- |
| `production_manager.py` | 根 Agent 协调、计划/重新规划、有限推进、委派与用户回复 | 项目业务数据的第二份实现 |
| `agent_runtime/` | 运行、计划、Action、记忆、ContextPack 和 WorkOrder 持久化 | 品类 Prompt、Provider 选型或固定制片顺序 |
| `skills/` | 人可审阅的共享制片流程、品类原则、角色 Prompt 和类型制作重点 | 副作用、任务状态、项目案例经验和 Tool Schema |
| `tools/` | 可发现合同、参数校验、薄适配与统一结果 | 复制 Domain Service 的业务逻辑 |
| `service.py` | 依赖组合、领域能力与内部可恢复作业 facade | 新的 Agent 计划模型、Skill 内容或 Tool Registry 实现 |
| Provider / Worker / Media | 真实外部调用、轮询、下载、恢复、检查与 FFmpeg | 品类创作策略 |

这样可以继续复用已经稳定的领域能力，同时把 Agent 控制面从大型 Service 中拆出；不需要为了形式上的“重构完成”一次性重写已通过回归的媒体制片核心。

### 1.6 默认路径与内部恢复边界

当前产品默认路径是：

```text
桌面端新建 GUIDED / AUTO_DRAFT
→ POST /agent-projects
→ 预分配 project_id 并创建 AgentRun
→ project.create Tool
  ├─ 短内容或本地 fallback：直接写入 PLANNED ProjectDocument
  └─ 已配置语言模型的 >90 秒 AUTO_DRAFT：写入 INPUT scaffold + Agent-owned PlanningRun
→ 若存在未完成 PlanningRun，由 planning.advance 每个 Action 推进一个持久化规划单元
→ FINALIZE 在同一 project_id 上写入 PLANNED ProjectDocument
→ 编译对应 Markdown Skill
→ LanguageProductionPlanner 或 LocalProductionPlanner 产生下一个 PlanRevision
→ Tool / WorkOrder / USER_INPUT
→ 基于最新 ContextPack 继续重规划，直到 goal_complete=true
```

用户在已有项目上点击新的 Agent 制作任务时，直接调用 `POST /projects/{project_id}/agent-runs`。桌面端只为本次从默认入口启动的 `AUTO_DRAFT` Run 保持自动续跑：Mock 可快速执行当前就绪步骤；真实 H3 每轮只执行一个 Agent Action，并根据项目阶段、MiniMax 任务状态、Take、Manifest 与 Export 的实际变化决定是否重置等待时间，状态未变化时按 3/6/12/15 秒退避。续跑持续到完成、失败、需要 WorkOrder/用户输入或进入需人工处理状态；GUIDED 与 Agent 面板手动 Run 保持停靠式交互。

同一项目无人值守推进期间，桌面端保留项目切换、刷新、Agent Run/WorkOrder 只读查看，但不挂载会主动写入的 Script、Assets、Shots、H3、Takes、Timeline 与 QA 面板，并在父级写处理器再次校验运行状态；这样 H3 面板的内部轮询也不会与 Agent 同时修改 `ProjectDocument`。当前桌面窗口一次只启动一个无人值守续跑，新建项目或启动另一个 `AUTO_DRAFT` 会等当前 Run 完成或暂停。进程重启后只恢复最新项目未结束且显式标记为 `unattended` 的 AgentRun；等待 WorkOrder 或用户输入的 Run 继续由 Agent 控制面展示。桌面端不查询、展示或推进独立 PlanningRun；Agent-owned PlanningRun 只由所属 AgentRun 通过 `planning.advance` 推进。

### 1.7 这几层与 workflow 的关系

最终结构是“一个通用 Agent 控制循环 + Agent 动态计划 + 少量可恢复子流程”，不是一条覆盖所有视频类型的固定 workflow，也不是多条完整 workflow 让 Agent 任意挑选：

| 层 | 本质 | 顺序由谁决定 |
| --- | --- | --- |
| AgentRuntime / Production Manager | 通用的 plan → act → observe → replan 控制循环 | Harness 只保证状态、依赖、有限推进和恢复 |
| PlanRevision / PlanGraph | 本次目标的动态执行计划 | 根 Agent 根据 ContextPack、Skill 和 Tools 生成并可随时替换 |
| Markdown Skill / playbook | 某类视频与某角色的制作方法、Prompt 和建议路径 | Agent 参考，不是可执行 DSL 或强制状态机 |
| Typed Tool | 原子、稳定、跨视频类型复用的业务能力 | Agent 选择调用；Tool 内不决定整部视频流程 |
| PlanningRun / Provider Task | 需要断点恢复、严格游标或外部对账的小型子流程 | Agent 决定何时推进，子流程内部只约束必要顺序 |

因此数字人口播、AI 短剧和动漫共享同一套项目、剧本、资产、生成、检查与时间线 Tools；区别主要来自加载的 Markdown Skill、角色 system prompt、当前目标与工件上下文。具体 Style 是项目工件，不由 Profile 写死。只有“上一规划单元必须先持久化”“Provider 任务必须先提交再轮询”这类正确性顺序留在状态机里。

## 2. 统一 Tool Registry

### 2.1 Tool 的责任

Tool 只表达一个可独立调用的能力。`ToolSpec` 包含稳定 ID、版本、类别、说明、效果类型、幂等提示、标签、强类型输入/输出与处理函数。`ToolResult` 统一返回状态、输出、`ArtifactRef`、提示和错误。

`READ | WRITE | EXTERNAL` 用于让 Agent 理解副作用、调度重试和向用户说明外部调用，它不是 RBAC、安全策略或 Tool 许可门。

### 2.2 目录与类别

```text
backend/short_drama/tools/
├── contracts.py
├── registry.py
├── defaults.py
├── project/
├── planning/
├── artifact/
├── script/
├── shot/
├── image/
├── video/
├── audio/
├── route/
├── timeline/
├── qa/
├── task/
├── change/
└── agent/
```

当前默认适配器将已有项目、可恢复规划、资产、剧本、分镜、图像、视频、音频、时间线、QA、任务、H3 路由和语义变更能力接入同一 Registry。长规划通过 `planning.advance` 每次推进一个持久化单元；语义变更的 Registry ID 为 `change.preview` 和 `change.apply`。`agent.delegate` 适配器只在宿主显式注入 Agent runtime delegator时注册；Production Manager 的 `DELEGATION` PlanStep 不会伪装成一个默认可用的领域 Tool。所有适配器只做参数校验与返回封装，不复制业务实现；Repository、锁、Provider Client 与私有 helper 继续留在 Domain Service 内部。

Tool ID 不包含内容类型。例如数字人、AI 短剧和动漫都使用同一个 `script.revise_lines`、`video.inspect_take` 和 `timeline.rebuild`，不创建按风格拆分的重复能力。

Production Manager 在计划接收和 Tool 真正调用前都校验当前项目：显式 `project_id`、只携带 `task_id`/`take_id` 的操作、ToolResult 与 AgentResult ArtifactRef 都必须回到父 Run 项目。`project.create` 只允许 bootstrap 预分配的 project_id，不允许已有项目 Run 借缺省 ID 旁路创建新项目。这是防止业务数据串项目的正确性约束，不是 Tool 权限系统。

### 2.3 能力与 Provider 的边界

全局 Registry 只注册已有真实实现的能力。数字人虚拟主播生成、专用口型驱动、动漫补帧或二维骨骼等未接入的 Provider 能力可以在 Skill 中被记为后续可选能力，但不注册伪 Tool，也不把 Prompt 文档写成已可执行的产品能力。

## 3. Markdown Production Skills

### 3.1 为什么改为文件

共享制片流程、品类原则、角色 Prompt 和制作重点需要被产品、编剧、导演和工程共同审阅，不适合长期埋在大型 Python 数据结构中。现在通用流程与各类型 Skill 分开管理：

```text
backend/short_drama/skills/
├── README.md
├── workflows/
│   └── end_to_end.md
├── formats/<format>/
│   ├── SKILL.md
│   ├── playbooks/default.md
│   ├── references/*.md
│   └── roles/
│       ├── concept.md
│       ├── writer.md
│       ├── planner.md
│       ├── critic.md
│       ├── review_router.md
│       ├── director.md
│       ├── asset_reviewer.md
│       ├── route_planner.md
│       ├── video_prompt_compiler.md
│       └── episode_reviewer.md
└── providers/<provider>/
    ├── SKILL.md
    └── references/*.md
```

`workflows/end_to_end.md` 保存“内容→资产→分镜→生成→审片→时间线→导出”的中文通用方法；格式 `SKILL.md` 保存 profile、显示名称、媒介、默认 playbook 路径和稳定类型原则；`playbooks/default.md` 逐阶段说明输入、判断、Tool、产物和返工；`roles/*.md` 保存专业角色职责，并通过 frontmatter 只加载它需要的 `references/*.md`。Provider Skill 只负责把 provider-neutral 镜头事实翻译成特定模型语法，不承担剧情、镜头决策或请求提交。

Loader 将 Markdown 编译成强类型 `ProductionSkill` 和 `ProviderPromptSkill`。`compile_production_prompt()` 在 Planner 角色中统一注入共享流程与完整类型 Playbook，其他角色只加载各自职责和明确引用的方法；H3 GPT 编译入口再叠加 `providers/minimax_h3` 的 Prompt Skill。项目运行中产生的具体经验不再放进格式 Skill，只通过 AgentRuntime 的 `ExperienceMemory` 按项目与 Skill 范围沉淀和召回。

### 3.2 Skill 负责与不负责的事

Skill 负责：

- 该类成片的长期原则和用户体验；
- Writer、Scene Director、Critic、Asset Reviewer 等角色的不同 Prompt；
- 共享流程上的详细类型制作方法；
- 对共享 Tools 的使用重点与结果解释。

Skill 不负责：

- 不保存项目状态、Agent 进度或 Provider 任务；
- 不定义第二套 Tool Schema；
- 不直接发起外部调用；
- playbook 不是可执行 DSL、强制工作流、Tool allowlist 或内容审批门。

## 4. 多视频类型：共享能力，分离制作方法

当前文件化 Skill Catalog 包含：

| ProductionProfile | 内容重心 | Scene Director 等角色的差异 |
| --- | --- | --- |
| `GENERAL_VIDEO` | 按用户意图选择节奏、氛围与视听形式 | 不强加短剧冲突、密度或剪辑偏好 |
| `AI_SHORT_DRAMA` | 连续短剧叙事与生成式生产，视觉 Style 由项目自由描述 | 关注承诺、选择、回报、可生成镜头职责和局部返修，不预设真人或动画语法 |
| `DIGITAL_HUMAN` | 信息准确、主播身份稳定、自然语音、口型和辅助视觉的可读性 | 关注镜头视线、口播节奏、手势与 B-roll/图形是否真正帮助理解 |
| `ANIME` | 角色设定、线条/材质、色彩、构图、动作与风格化一致性 | 关注 silhouette、pose、layout、镜头语言与有限动画资源分配，不照搬真人覆盖逻辑 |

四种 profile 使用同一项目、剧本、资产、分镜、生成任务、Take、QA 和时间线核心。差异通过 Skill 的类型原则、角色 Prompt、制作重点以及当前项目上下文表达。

“存在数字人或动漫 Skill”只证明 Agent 已能使用相应制作方法规划和评审，不自动证明专用虚拟主播、口型、补帧或动画 Provider 已经接入或达到成片质量。

## 5. ContextPack 与四层记忆

### 5.1 记忆边界

| 层 | 保存内容 | 不保存内容 |
| --- | --- | --- |
| `ProjectMemory` | 项目摘要、稳定约束、关键 `ArtifactRef` | 完整 ProjectDocument 副本 |
| `RunMemory` | 当前焦点、已完成动作、阻塞、开放问题、下一步 | 无界增长的对话全文 |
| `DecisionMemory` | 已确认决定、理由和相关工件 | 未确定猜测 |
| `ExperienceMemory` | 可复用教训、适用范围、证据、信心和标签 | 无条件的“永久规则” |

`ArtifactRef(project_id, artifact_type, artifact_id, version)` 是 AgentRuntime、ToolResult、WorkOrder 和 AgentResult 共享的工件引用。需要执行时再从权威项目数据解析。用户只修改一部分时，Agent 通过项目摘要、已确认决定和精确工件引用恢复全局，而不把整个项目和历史对话重新塞入每次调用。

DecisionMemory 按项目召回：当前 Run 的决策排在最前，其后是项目级决策，再其后是同项目其他 Run 中的近期决策。因此新的局部修改 Run 会继承项目已确认的设定，但当前 Run 中的最新选择保持优先。

ExperienceMemory 有明确的沉淀入口。完成的 WorkOrder 带有 findings 或 decisions 时，Production Manager 调用 `distill_experience_from_result()`，自动带入项目、活动 Skill、证据和工件引用。去重键是同一 project/skill scope 中规范化后的 `summary + applicability`；命中时复用原 ID，合并新证据、标签和工件引用，置信度只向更高等级更新。不将每个普通 Tool 输出无差别写入长期经验。

### 5.2 ContextPack 编译

Context Compiler 根据当前目标、活动 Skill ID、请求的 ArtifactRef、运行检查点和经验标签编译有界上下文：

```text
ContextPack
= objective
+ active Skill ids
+ project summary and constraints
+ confirmed decisions
+ relevant experiences
+ resolved artifact summaries
+ recent action digests
+ blockers, open questions and next actions
```

默认 Tools 返回的 project/script/bible/scene/shot/character/asset/reference pack/timeline/task/image task/Take/H3 route/provider run/export/检查报告等 ArtifactRef，都从现有 ProjectDocument 或 SQLite Repository 解析为精简 ArtifactSummary，不复制第二份数据。真正不存在或版本无法定位的引用显式返回在 `unresolved_artifact_refs`，不由 Agent 猜测对象。Production Manager 再根据活动 profile 和当前角色单独编译 Skill system prompt，与 ContextPack 一起交给 Agent。ContextPack 是每次 Agent 调用的投影，可以重新编译，不成为新真相源。

除了条目数量上限，Context Compiler 还对人类可读内容使用默认 `24_000` 字符预算。objective、项目摘要、Run focus、约束/问题/下一步、ArtifactSummary、DecisionMemory、ExperienceMemory 和 ActionDigest 都会按字段上限截断或在预算用尽时停止加入。这是便于预测 Prompt 大小的普通字符预算，不是模型 Token 计费器；稳定 ID 和 ArtifactRef 结构仍保留，不会为了截断文本而变成不可解析的引用。

## 6. Multi-Agent 通信合同

主 Agent 不把整段历史对话转发给子 Agent，而是发送结构化 `WorkOrder`：

- `objective` 与 `why_now`；
- 由 ArtifactRef 构成的精确 `scope`；
- 必须保留的约束与已确认决定；
- 开放问题；
- 期望输出 Schema；
- 可检查的验收条件。

子 Agent 返回结构化 `AgentResult`：

- 一段简洁结论；
- 新建/修改的 ArtifactRef；
- findings、decisions 与 assumptions；
- 未解问题与建议的下一步；
- 支持结论的 evidence refs。

这个合同保证信息足够自包含，又避免无关对话、大段 Prompt 和重复项目数据在 Agent 之间传递。专业 Agent 可以因内容类型获得不同的角色 Prompt，但仍通过同一 WorkOrder/AgentResult 合同与共享 Tools 工作。

### 6.1 Tool-capable specialist

内置 `LanguageSpecialistAgent` 不只能根据静态 Prompt 返回文本。它获得与 Production Manager 同一份 Tool Catalog，可在返回最终 AgentResult 前请求单次 Tool 调用，并把 ToolResult 和累积的 ArtifactRef 带入下一轮。默认最多进行 4 轮 Tool 调用，每轮只执行一个明确 Tool，之后必须给出当前最佳结果。这个边界是防止一次委派无界循环的调度限制，不是 Tool 权限门。

专业 Agent 仍需要已配置的语言模型。`LocalProductionPlanner` 只为根 Agent 生成确定性的本地后续计划，不伪造一个“本地专业导演”；未配置语言模型而计划确实需要 DELEGATION 时，运行进入可解释的等待/配置状态。

专业 Agent 的 Tool 调用使用同一个项目范围 invoker，不绕过根 Agent 的 ID 归属和 ArtifactRef 结果检查。

### 6.2 AgentResult 的比例适当校验

AgentResult 提交时只做普通的结构与完成性检查：

- `PARTIAL` 和 `FAILED` 允许保留未完整输出，便于上层重规划；
- `COMPLETED` 不能同时携带 `unresolved_questions`；
- WorkOrder 提供 `acceptance_criteria` 时，完成结果至少要有 finding、decision、新建/更新工件、evidence ref 或建议下一步中的一种结构化依据；
- `expected_output_schema` 对完成结果校验常用 JSON Schema 子集，包括基本类型、object required/properties、array items/长度、string 长度、number 范围和 enum。

这些检查不尝试用机械规则判断导演方案“艺术质量是否足够好”，也不引入审批门。输出结构或完成状态自相矛盾时，Production Manager 把合同错误转成可见的运行冲突，而不将错误结果写入已完成 WorkOrder。

## 7. 用户局部修改：语义 plan / apply

语义修改拆为两个可组合阶段：

### 7.1 `plan-change`

- 解析用户原话或显式目标；
- 基于当前 ProjectDocument 按需建立只读 ProjectIndex；
- 返回可编辑 `ImpactPlan`；
- 列出确定命中、需要失效的下游和保持不变的分支；
- 无项目写入、无 Provider 调用。

### 7.2 `apply-change`

- 校验编辑后的计划仍属于当前项目与可编辑范围；
- 为 Script、ProjectBible、Asset 或 Shot 创建新版本/局部更新；
- 只将真正依赖变更的 ReferencePlan、ExecutionPlan、Take 和 TimelineManifest 标记为 `STALE`；
- 保留无关镜头、旧版本和已有 Take；
- 返回应用结果、失效范围和验证摘要。

Apply 本身不伪装成图像/视频已重新生成。它只执行语义变更和真实依赖失效；之后 Production Manager 再根据实际需要调用资产、图像、ReferencePlan、H3、检查和时间线 Tools。因此用户只改一部分时，Agent 既能理解全局，又不会将整部项目重做。

## 8. 七阶段迁移结果

| 阶段 | 结果 | 主要位置 |
| --- | --- | --- |
| 1. 统一 Tool 合同 | 类型化 Registry、发现、调用和统一结果封装 | `backend/short_drama/tools/` |
| 2. Skill 文件化 | 共享流程、类型原则、角色 Prompt 和制作重点移入中文 Markdown | `backend/short_drama/skills/` |
| 3. 动态 AgentRuntime | AgentRun、带 `goal_complete` 的 PlanRevision/PlanGraph、ActionRecord、跨 Run 记忆、字符预算 ContextPack、委派合同 | `backend/short_drama/agent_runtime/` |
| 4. Production Manager | 从用户目标与当前工件制定/修订计划，调用 Tool 或委派子 Agent，并在有/无语言模型时选择相应 Planner | `production_manager.py` |
| 5. 语义变更执行 | `ImpactPlan` 可编辑预览与局部 apply，精确失效下游 | `change_planner.py` / `change_executor.py` |
| 6. 多格式 Skill | General、AI 短剧、数字人、动漫共享 Tools，使用独立角色 Prompt；具体 Style 属于项目 Bible | `skills/formats/` |
| 7. 默认路径收口 | 新建 GUIDED/AUTO_DRAFT 经 `/agent-projects` 进入 AgentRun；删除旧 Run 公开路由与桌面端恢复链 | Agent API / desktop Agent panel |

此次迁移采取增量内部切换：先将稳定领域能力包装为 Tools，再把 Skill 从 Python 数据结构移到 Markdown，最后引入 AgentRuntime 和 Production Manager。旧 PlanningRun/AutonomousRun 接口不保留兼容入口；独立 PlanningRun 与 AutonomousRun 控制面均已删除。内部只保留同一 ProjectDocument、Agent-owned PlanningRun 和 Provider Task 的必要恢复语义，不重建第二套数据源。

## 9. 已实现与待 Provider 验证的边界

### 已实现的工程能力

- 可发现、强类型、可注入的 Tool Registry 及现有能力适配器；
- 四类文件化 Production Skills 与按角色 Prompt 编译；
- 动态 PlanGraph、计划修订、Action 记录、SQLite 恢复和有界 ContextPack；
- `goal_complete` 驱动的目标反馈循环，以及 Tool 失败、WorkOrder 返回和用户输入后的动态重规划；
- Project/Run/Decision/Experience 四层记忆，包括当前 Run 优先的项目跨 Run 决策召回、结果经验沉淀和去重；
- 默认 24,000 字符内容预算及 ArtifactSummary/ActionDigest/记忆文本截断；
- WorkOrder/AgentResult 委派闭环、完成合同校验与可调用共享 Tools 的内置专业 Agent；
- 语义变更的无副作用 plan 和局部 apply；
- capability catalog、Agent 项目 bootstrap、AgentRun 创建/查询/重新规划/推进/用户回复与 WorkOrder 回传 API；
- 未配置语言模型时基于 stage 和 Tool Catalog 的 `LocalProductionPlanner` fallback；
- 桌面端默认新建与新自动制作进入 AgentRun/Agent 面板；
- 保留 Agent-owned PlanningRun 恢复、ProjectDocument、Provider Task 和 Timeline 的数据语义；不保留旧公开接口。

### 仍需真实 Provider 或跨项目样本确认的能力

- 数字人主播的身份稳定、长口播口型、声线与稳定演播；
- 动漫角色 model sheet、线条/材质一致、风格化动作与补帧路线；
- 不同内容类型下默认 Tool 组合的成片质量与稳定性；
- 真实多 Agent 长运行中的上下文量、委派粒度与重新规划策略。

这些是产品/质量验证项，不影响当前架构分层成立，也不应被 Mock、Prompt 文档或类型检查提前宣称为已达到生产质量。

## 10. 普通产品工程边界

本架构特意不增加与制片闭环无关的复杂性：

- 不增加创意审批门、Tool 执行门或独立策略引擎；
- 不建立文档冻结层、SHA256 锁定、密码学承诺或防篡改证据链；
- 不引入图数据库、第二项目真相源、事件溯源或 Agent 微服务集群；
- 不因为存在 Multi-Agent 就建立红队、对抗评测或安全审计框架；
- 不用固定镜头数、每 N 秒信息点或类型模板阻止 Agent 采用更合适的创作路径。

需要保留的确定性约束只包括 Schema、真实 ID、项目范围、依赖闭包、文件存在性、Provider 参数合法性、任务恢复/对账与时间线可执行性。这些是普通的正确性控制，不替代 Agent 的内容判断。

## 11. 最终验收标准

以下是最终交付的验收口径，不在命令实际完成前预先标记为“通过”：

1. 桌面端新建 GUIDED/AUTO_DRAFT 通过 `POST /api/short-drama/agent-projects` 创建 AgentRun，再由 `project.create` 产生权威 ProjectDocument；
2. Tool Registry 可列出和调用已注册能力，错误参数、未知 Tool 与处理失败都返回统一结果；
3. 新增或修改内容类型的创作策略只需审阅 `skills/formats/<format>/` 下的 Markdown；同一 Scene Director 在不同 profile 获得不同角色 Prompt，但调用相同 Tool 合同；
4. Agent 可创建带依赖的计划、计算就绪步骤并替换计划；当当前 PlanGraph 完成但 `goal_complete=false` 时继续重新规划，只有 `goal_complete=true` 才完成整个 Run；
5. 进程重启后可恢复当前 PlanRevision、Action、RunMemory 和待回复 WorkOrder；计划记录缺失 `goal_complete` 时使用模型默认值 `false`；
6. 新 Run 能按“当前 Run 优先”召回项目内已确认决定，完成的有价值 AgentResult 能按 project/skill scope 沉淀并去重经验；
7. ContextPack 除条目数之外受默认字符预算约束，会截断 ArtifactSummary、ActionDigest 与记忆文本，但保留稳定 ID 和 ArtifactRef；
8. WorkOrder/AgentResult 能完成上下文编译、专业 Agent 的 Tool 调用与结果回传，并对完成结果执行比例适当的 Schema/完成性校验；
9. `plan-change` 保持无副作用，`apply-change` 保留旧版本并只失效真正受影响分支，不隐式调用付费 Provider；
10. 独立 PlanningRun 与 AutonomousRun 的接口、前端链路、ProjectDocument 字段、领域模型、服务分支和旧脚本均已删除；已配置语言模型的超过 90 秒 `AUTO_DRAFT` 由当前 AgentRun 拥有并通过 `planning.advance` 逐步推进可恢复 PlanningRun，Provider Task 状态机仍独立对账；
11. 全量回归包含后端测试、Pyright strict、TypeScript strict、OpenAPI 产物、前端构建、文档链接和 `git diff --check`。

验收结果由最终交付轮次按实际命令更新；当前不用真实付费媒体 Provider 调用来代替架构和回归验收，也不将未完成的数字人/动漫真实质量实验记为通过。

### 11.1 2026-09-01 最终工程验收结果

- `./scripts/pnpm-project.sh backend:test`：810 项后端测试全部通过；
- `./scripts/pnpm-project.sh typecheck`：TypeScript strict 与 Pyright strict 均通过，Pyright 为 0 errors / 0 warnings；
- `./scripts/pnpm-project.sh openapi:generate`：FastAPI OpenAPI JSON 与 TypeScript 类型重新生成成功；
- `./scripts/pnpm-project.sh build:frontend`：React renderer、Electron main 与 preload 均完成生产构建；
- 文档本地链接检查与 `git diff --check` 通过；
- 默认 Mock Agent 项目回归已从 `project.create` 运行到 `EXPORTED`，并确认只使用 AgentRun、Tools 与领域任务；
- 当前能力目录实际加载 63 个共享 Tools 与 4 个 Markdown Production Skills。

本轮没有调用 GPT Image 2、MiniMax-H3、Azure Speech 或其他付费 Provider。真实数字人、动漫和长时间多 Agent 成片质量仍按第 9 节的产品实验边界处理，不混入本次工程验收结论。
