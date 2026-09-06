# MiniMax Design、Agent Team 与 H3 Skills 对当前产品的启发

> 调研日期：2026-09-04
> 目标：学习 MiniMax Design 的产品形态、Agent Team、上下文/工件交接、本地 ComfyUI 和公开 Skills，并映射到当前单用户桌面短剧制片平台。

## 1. 证据边界

本文继续使用项目的证据标签，并用 `REPO-CONFIRMED` 区分当前仓库已落地的后端事实与 MiniMax 公开或本机包证据。

- `DOC-CONFIRMED`：MiniMax 官方页面、官方指南、API 文档或论文明确写明。
- `SOURCE-CONFIRMED`：MiniMax 官方 GitHub 代码可直接确认。
- `PRODUCT-CLAIM`：MiniMax 官方对产品能力的描述，本轮未对生成效果做付费实测。
- `USER-SUPPLIED`：用户提供的 MiniMax Design 截图中可见信息。
- `LOCAL-PACKAGE-OBSERVED`：对本机已安装 MiniMax Design 包、配置、内置模板或用户下载 Skill 做静态检查；能证明当前包中存在的设计，不等于厂商对未来版本的公开承诺。
- `REPO-CONFIRMED`：当前仓库源码、持久化模型、Tool 合同或定向测试能确认的本项目实现；只代表后端路径已落地，不代表真实 Provider 成片质量、长时运行稳定性或用户体验已经验证。
- `INFERRED`：根据产品交互或开源边界得出的合理推断，不代表 MiniMax 内部实现。
- `UNKNOWN`：MiniMax 没有公开，或需登录/真实生成才能验证。

本轮定点审查的官方开源版本：

- `MiniMax-AI/MiniMax-H3` commit `d21241f0a4b3acbb34c97dae47fa417b7065e438`；
- `MiniMax-AI/Mini-Agent` commit `d76a4f6389688cabda39c224a6cdfa274215d47c`；
- `MiniMax-AI/minimax-desgin-plugin` commit `bef9de2101e9d873619c83553b8f03ebb0c10663`。

本机安装包静态快照：MiniMax Design `3.0.11 (1035)`。审查对象包括 `agent-profiles/v2`、内置 ComfyUI 插件、H3 示例工程和 `~/.hub/skills`；未触发付费生成。

## 2. 结论先行

MiniMax Design 最值得学的不是“用 ComfyUI 代替产品”，而是两层结构：

```text
外层：语义制片工作台
目标 / 剧本 / 角色 / 场景 / 分镜 / 候选 / 问题 / 时间线 / 成片
                         │
                         │ Agent 选择工具或工作流
                         ▼
内层：媒体执行图
模型加载 / conditioning / sampler / decode / 后处理 / output
```

对当前项目的核心判断是：

1. 当前 `AgentRun + ProductionManager + PlanGraph + WorkOrder/AgentResult + ProjectDocument` 的方向与 MiniMax Agent Team 公开架构一致，不需要推倒重做。
2. `PlanningRun` 应继续是 AgentRun 所有的 Content Planning Job，只保存因果依赖强的剧本创作游标；不应与 AgentRun 合并。
3. 截图中的八个“创作领域”不是八条互斥工作流。当前客户端只把选择保存到 `global.selectedInterests` 并上报分析事件，未发现 Router、Planner、Agent Profile 或 Skill Loader 消费它。当前项目将真正的制作方法落在可组合 `Production Method Catalog`，而不是领域关键词路由。
4. `Production Method Catalog` 已以两层文件化 Skill 落地：MiniMax Design 3.0.11 的 `drama-series`、`ad-tvc`、`mv` 是项目级 Workflow，MiniMax-H3 的 8 个公开 Skill 是任务/表达方法。`Story Orientation` 和 `PlanRevision` 依据完整语义选择、保留、替换或移除方法，不用正则、关键词或固定类型路由。
5. Root Planner 与 Specialist 已共用紧凑 Tool Catalog，并由 Agent 显式请求所需 Tool schema 再继续规划或执行；不再一次性向模型展开全部合同。
6. 多 Agent 不默认启动 copy/image/video/audio 四个 Worker。`LocalTeamExecutor` 只并行推进当前可独立产出与独立验收的 WorkOrders；同一项目的 `WRITE/EXTERNAL` Tool 调用共用一条串行写通道。WorkOrder 以 `next_owner` 明确下一责任方，统一 `advance` 每次只推进一个 Worker batch 或一个 Leader 单元；同一 Worker 可在该 batch 内沿已持久化的模型/Tool 回合续跑，但 Worker 返回不在同次调用内隐式触发 Leader 收尾，批次失败可见并停止。
7. `ContextManifest + 精确 ArtifactRef`、派生的 `ProductionBoardView`、append-only `WorkOrderProgressEntry` 以及 `carried_work_order_ids/stale_handoff` 已组成当前的上下文隔离、进度投影和重规划交接语义，不另建第二份项目真相。
8. H3 路由已支持 `DIRECT`、`FRAME_FIRST`、`FRAME_LAST`、`FRAME_FIRST_LAST`、`REFERENCE` 五类候选，并以资产准备交接、完整 freshness 检查和 `state_revision` 保持精确可解析的状态版本。
9. 商业 Skill 中真正跨 Agent 复用的中间成果不再只存在 Prompt：当前已用版本化 `ProductionDocument` 统一结构化承载；音乐主轴方法还已接通 Master Audio、实测 AudioStructureMap、精确 TimedText 与最终时间线换轨。
10. Design Workflow 中的 Hub/Canvas/Stage Plan 已映射到现有 ProjectDocument/ProductionDocument、AgentRun/PlanRevision/WorkOrder 与派生 ProductionBoardView，不覆盖本地 Harness。`clip_group` 作为跨 ShotPlan 的生成组单独表达，不与剪辑镜头或 `timeline_beats` 混合。
11. 交互服从用户意图：明确值直接采用；明确无交互或交由 Agent 决定则继续并记录；明确要求确认才停靠；其余只就实质信息缺口询问。
12. 时长应是显式但可由 Agent 建议的制片约束；风格应拆成 `StoryIntent` 的观众体验、可组合制作/叙事 Skills 和 `ProjectBible.style` 的视听表达。
13. 上述后端合同与运行路径已实现，但实际创作质量仍需通过完整作品实验验证。完整桌面 UI、语义画布和 ComfyUI 嵌入依然延后。

## 3. MiniMax Design 的实际产品流程

### 3.1 公开的主流程

`DOC-CONFIRMED / PRODUCT-CLAIM`：[MiniMax Design](https://design.minimax.io/) 和官方使用指南展示的主流程是：

1. 用户输入想法、brief，或导入本地素材/项目。
2. Main Agent 理解意图、拆解任务，自动匹配或允许用户手动选择模型。
3. Copy、Image、Video、Audio 等能力在合适的任务上并行工作。
4. 剧本、分镜、图片、视频、音乐与编辑结果汇入同一画布，并自动形成过程节点。
5. 用户在核心方向或高成本节点参与，也可在进行中继续与 Agent 对话、追加任务或修改工件。
6. Agent 与 Harness 加载知识/Skills，在交付前进行检查和局部返工。
7. 用户编辑、组装并导出。

官方指南展示的界面分区也很清楚：

- 右侧：Agent 对话和任务区；
- 中间：多模态画布、预览和编辑；
- 左侧：Skill/模板/插件、项目管理和本地资产库；
- 资产中心：角色、场景、风格包、道具等可复用资产。

### 3.2 截图中的八个领域意味着什么

`USER-SUPPLIED`：首次使用时可“最多选择 3 个领域”：

- 短剧 / 漫剧；
- 影视 / 混剪；
- 带货 / 电商；
- 广告 / 营销；
- MV / 音乐视频；
- 动画 / 二次元；
- 知识 / 科普；
- 其他。

`LOCAL-PACKAGE-OBSERVED`：MiniMax Design 3.0.11 客户端将这一选择保存为 `hasSelectedInterests` 和 `selectedInterests`，并上报领域选择查看/完成事件。包内静态搜索未发现 Router、Planner、Skill 装载器或 Agent Profile 读取该字段。本机只选择 `shortDrama`，但 `~/.hub/skills` 仍安装了广告、MV、教育、KOC、UI Motion 等跨领域 Skills。

因此已确认的产品语义是“用户兴趣持久化 + 分析上报”；它是否被服务端用于首页推荐仍是 `UNKNOWN`。不能声称它会切换 Agent Team、安装对应 Skill 或决定实际任务路由。

从分类语义看，它仍更像“个人创作兴趣与能力发现排序”，而不是一个项目的硬路由器：

- 允许同时选三个，说明它们并非互斥产品模式；
- 分类维度本身混合：“动画”是媒介，“电商”是商业目标，“MV”是以音乐为主轴的制作方法，“短剧”是内容形态；
- 文案是“Agent 团队会提供更贴合的创作支持”，更像初始化推荐、Skill 展示和默认建议。

因此当前产品后续可以引入 `WorkspaceCreativePreferences`，但它只影响：

- Skill 广场和示例的排序；
- 新项目初始建议；
- 常用 Agent 角色/方法的快速入口；
- 用户未给出足够信息时的候选方向。

它不应：

- 根据关键词强制路由项目；
- 替代项目当下的完整语义判断；
- 锁定 Tool 允许列表或模型；
- 成为剧本风格、项目 Style 或观众体验的真相源。

### 3.3 兴趣、Format 与 Skill 的关系

一个用户同时选择“短剧 / 漫剧 + 广告 / 营销 + 动画 / 二次元”是合理的，一个项目也可能同时是动画短剧、品牌内容和音乐主导视频。所以三者应分层：

```text
CreatorInterest / SkillCategory
  = 长期软偏好，只影响发现和排序

Format / ProductionProfile
  = 本地项目 schema/profile 适配，如 AI 短剧、数字人、动漫、通用视频

Production Method Skill
  = 两层制作方法：3 个 MiniMax Design 项目 Workflow + 8 个 MiniMax-H3 任务 Method adapter
```

Agent 依据当前用户目标、完整 ProjectDocument 和已有资产选择 Method Skills；用户兴趣只是弱先验，不能覆盖当前语义。

## 4. Agent Team：公开架构与适用边界

`DOC-CONFIRMED`：MiniMax 在 [Agent Team 技术博客](https://www.minimax.io/blog/minimax-agent-team-long-running-1779893953) 中把多 Agent 定义为运行时与控制平面，而不是若干 Prompt 的简单串联。

### 4.1 Leader / Worker / Verifier

Leader 负责：

- 判断当前目标是否值得拆成 Team；
- 将目标转成任务、依赖和可并行边界；
- 选择先读取哪些工件，哪些任务可同时开始；
- 处理补充信息、异常、升级、重试和最终汇总。

Worker 的专业化不只是换一段角色 Prompt，还应包括：

- 不同的 Tools；
- 最小必要上下文；
- 不同的记忆和 Skills；
- 不同的输出协议和验收方式。

Verifier 和 Worker 形成刻意分离的产出/评估视角，但不意味着每份工件都必须配一个独立 Verifier。只有当结果需要独立通读、视觉观察、来源核对或跨工件验收时，分离才能提高质量。

### 4.2 Team 不是默认的

MiniMax 明确提到，短且确定的任务更适合单 Agent 或传统自动化。当下项目的委派原则应是：

```text
目标是否可拆成多个独立产出？
├─ 否：保留在主 Agent 或顺序领域 Job
└─ 是：这些产出是否可独立验收？
   ├─ 否：重新切分边界，不盲目并行
   └─ 是：建立带依赖的 WorkOrders，必要时增加 Verifier
```

适合并行的例子：

- 剧本批准后，角色资产、场景资产和声音方案的准备；
- 已经拆分清楚、不共享动作状态的镜头候选；
- 不同参考方案、风格探索或可独立比较的实验。

不适合盲目并行的例子：

- 首次完整剧本的不同场次，因为人物状态、信息揭示、节奏和结尾因果强依赖；
- 一个未定义空间关系的连续动作；
- 只是为了让 trace 看起来像“多 Agent”的人为分工。

### 4.3 状态机与中途介入

MiniMax 公开的任务生命周期可概括为：

```text
producing → verifying → done
    ▲          │
    └─ 未通过 ─┘
```

这个回路的价值是“从最近的工件局部唤醒生产者”，而不是“强制三轮或必须清零问题”。Leader 可在子任务进行中追加消息；用户也可继续与 Main Agent 对话，不必等待整个长任务结束。

MiniMax 没有公开 Verifier 通过分数、最大轮数、创意重试上限或固定停止阈值；当前项目不应自行补成这些门禁。

### 4.4 MiniMax Design 3.0.11 的实际角色拓扑

`LOCAL-PACKAGE-OBSERVED`：新项目当前默认使用 Agent Architecture v2；旧项目和配置同步失败仍可回退 v1。v2 不是官网营销文案所表现的四个长驻模态 Agent，而是认知职责拓扑：

```text
media-agent (primary / user-facing)
├─ 简单任务：直接调用图像、视频、语音、音乐、后处理或画布 Tool
├─ 复杂项目：router → planner → executor(stage)
└─ 明确 ComfyUI 任务：comfyui-agent
```

- `router`：无状态地读取意图、来源指针与 workflow 索引，只返回紧凑 `routing_capsule`，不做生产计划或生成媒体。
- `planner`：物化人类可读文档与 Stage Execution Plan，返回计划 ID、阶段数和最小阶段摘要。
- `executor`：每次只接收一个 Stage 指针，可执行图像、视频、语音、音乐和后处理，不规划多阶段也不继续派生 Agent。
- `comfyui-agent`：只处理已指定或当前画布的 ComfyUI workflow，默认无头执行，用户明确要求时才展开完整节点编辑器。

子 Agent 只看显式 payload 和来源指针，不继承全量父对话；这与当前项目的 `ProductionManager → WorkOrder → ArtifactRef/AgentResult` 基本同构。当前 v2 profile 里没有一个常驻、全局的 `verifier-agent`；评审由 workflow/Skill 的审阅节点和主 Agent 组织。因此我们保留按领域需求创建 Verifier WorkOrder 的设计，比固定启动一个总 Verifier 更合理。

包内目前只有 `ad-tvc`、`drama-series`、`mv` 三个 active workflow target；`koc-video` 由 Skill 在 workflow 路由前承接，`_disabled` 中的旧目录不能当成当前功能。这也证明“八个兴趣 = 八条 workflow”是错误映射。

`REPO-CONFIRMED`：这三条 active workflow 及其 `_shared` 文档已从 Design 3.0.11 安装包按版本保存到 `skills/official/minimax_design/3.0.11/workflows/`，并分别以 `method.minimax_design.drama_series`、`method.minimax_design.ad_tvc`、`method.minimax_design.mv` 薄适配到现有角色与工件。`_disabled/` 未纳入活动 Catalog，根 workflow 的进入信号也不被解释为本地关键词路由。

## 5. 上下文隔离、Shared Board 与 Artifact Handoff

### 5.1 信息不是全量广播

MiniMax Agent Team 公开的信息共享方式包括：

- Agent 自身记忆；
- Agent 间的直接消息/中断；
- 可读的 handoff 文件和简短摘要；
- 持久化共享公告板，大信息按需拉取；
- 模型上下文之外的任务、事件、文件和决定状态。

`REPO-CONFIRMED`：当前项目已将这些原则收敛为：

```text
ProjectDocument / versioned Artifacts
  = 完整权威事实

ProductionBoardView
  = 目标、已确认决定、任务状态、工件变化、阻塞与下一步的派生投影

ContextManifest / ContextPack
  = 当前 Worker 完成任务所需的最小视图

Artifact Handoff
  = 精确 ArtifactRef + 摘要 + 已确认约束 + 验收要求
```

### 5.2 Shared Board 不是第二真相源

`REPO-CONFIRMED`：`ProductionBoardView` 已作为无独立持久化的只读投影，从 ProjectDocument、AgentRun、PlanRevision、Action、WorkOrder、DecisionMemory、AgentResult、PlanningRun 和 Issue Ledger 重建，展示：

- 当前用户目标与语义阶段；
- 已确认决定和不能静默改变的承诺；
- Team / WorkOrder 状态和依赖；
- 正在处理、新建和修改的工件；
- 阻塞、待决定问题与建议下一步；
- Issue Ledger 与局部返工位置；
- 剧本、资产、分镜、Take、时间线和导出物的链接。

Agent 不直接“改白板来改项目”，而是写入正式工件、Decision、WorkOrder progress 或 AgentResult，白板只投影最新事实。

### 5.3 ContextManifest 由 Agent 显式形成

`REPO-CONFIRMED`：对每个新 WorkOrder，Leader 已显式编译持久化 `ContextManifest`，选择：

- 当前目标和 `why_now`；
- 必读的精确工件版本；
- 需要理解的相邻场次/镜头或输入边界；
- 必须继承的用户决定、角色设定和连续性事实；
- 相关经验标签与已知能力边界；
- 预期返回的工件类型和验收方式。

它是上下文选择说明，不是权限系统。对 script、bible、character、scene、shot、asset version、timeline 以及 H3 plan/preparation 等版本化工件，`ArtifactRef` 携带精确版本；Loader 只解析并校验这些 ID/版本与所属项目，不根据关键词决定该给谁看什么。因此 Worker 读取的是指定工件快照，不会把“同 ID 的最新内容”冒充成旧计划当时的上下文。

### 5.4 Stage Execution Plan 值得吸收的状态设计

`LOCAL-PACKAGE-OBSERVED`：Design v2 将完整来源放在计划顶层 `sources`，不伪装成一个执行 Stage；首次写入完整 `stage_outline`，但只物化当前 Stage。当前阶段产物被接受后，再恢复同一 Planner session 追加下一 Stage。对长项目来说，这比一次性展开所有下游 Prompt 更节省上下文，也不会用尚未生成的结果填充假计划。

其它值得吸收的字段语义包括：

- `depends_on`：明确上游依赖；
- `work_items[].id`：稳定逻辑输出槽，重试或局部重做不删除已成功项；
- `runtime_refs`：当前有效输出；
- `superseded_runtime_refs`：已被新版替换的输出；
- `ref_capsules`：当前 Stage 可解析的资产与其作用；
- `execution_policy`：表达工作项是可并行还是有真实依赖地串行。

当前项目已有 `PlanRevision/PlanGraph`、`WorkOrder`、`ArtifactRef`、版本化媒体和依赖失效，无需复制一份 Stage Plan 引擎。`carried_work_order_ids` 让 Leader 在替换 PlanRevision 时显式携带仍有效的未完成委派；未携带的旧 WorkOrder 停止自动执行，迟到结果以 `stale_handoff` 保留为历史交接而不合并回新计划。append-only `WorkOrderProgressEntry` 则保留子 Agent 的逐步进展，不用 PARTIAL 结果覆盖旧进度。这些是当前对“稳定输出槽 + 指针交接 + 当前阶段渐进物化”的普通工程实现。包内 workflow 里的关键词匹配表、硬确认点、固定轮次和时长档位不属于这个状态模型的必要部分，没有带入我们的 Harness。

### 5.5 核心资产的源码边界与当前迁移

`SOURCE-CONFIRMED`：MiniMax 没有开源 Design 中“决定谁需要角色卡/场景卡、如何拆给 Image/Video/Audio Agent、怎样共享状态”的编排实现。[MiniMax-H3 README](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/README.zh-CN.md#L89-L104) 明确说明 H3-Context-IR 依赖多阶段工作流和多个托管模型/服务，本次开源不包含该模块。因此不存在可直接替换当前资产管线的 MiniMax 公开源码。

`SOURCE-CONFIRMED`：公开的[3D 动画短片 Skill](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/3d-animation-short-generator/SKILL.cn.md#L130-L182) 给出了角色卡与场景卡方法：主 3/4 视图、正/侧/背、表情、服饰/材质/道具和 identity lock；场景卡保持 environment-only，包含总览、光态、地标和关键道具。但这是 Markdown 方法，不是 `generate_character_card` 一类 Tool 实现；且其 [`meta.yaml`](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/3d-animation-short-generator/meta.yaml) 标记为 `source: community`。准确说法是“MiniMax-H3 官方仓库收录并维护的公开 Skill”，不是 MiniMax 团队私有 Design Prompt 的源码。

`SOURCE-CONFIRMED`：开源 [MiniMax Design ComfyUI plugin](https://github.com/MiniMax-AI/minimax-desgin-plugin/tree/bef9de2101e9d873619c83553b8f03ebb0c10663) 只暴露通用图像/视频节点、ComfyUI 执行面和 Design 私有网关适配。公开代码中没有角色卡、场景卡、四视图、Leader/Worker/Verifier 或 Shared Whiteboard 的领域编排源码。[Mini-Agent](https://github.com/MiniMax-AI/Mini-Agent/blob/d76a4f6389688cabda39c224a6cdfa274215d47c/mini_agent/agent.py#L45-L63) 也是单 Agent 通用 tool loop，不是 Design Agent Team 源码。

`LOCAL-PACKAGE-OBSERVED`：Design 3.0.11 安装包内的 workflow 配置可观察到它的责任分层：Workflow/Skill 决定何时建锚点及其形态，Planner 写出稳定 logical ID、最终 prompt、有序 refs 与 dependencies，无状态 Executor 调用通用图像生成 Tool。这是可吸收的架构范式，但安装包配置不是公开开源实现。

`REPO-CONFIRMED`：当前迁移方式是保留已有的 `Asset/CoreAssetBrief/AssetVersion/ReferencePack/ImageTask/H3RoutePlan` 数据与 Tool，把 MiniMax 方法映射到现有边界：

- 角色卡/场景卡 → 结构化 brief + 已批准视图 + 可选 reference sheet + ReferencePack；
- Planner `work_items` → WorkOrder 中的稳定 ID、最终 prompt、有序 ArtifactRefs 和 dependencies；
- 通用图像执行 → `image.enqueue` + 单步任务生命周期；
- 实际看图再验收 → `qa.inspect_asset_version` 返回语义建议，`artifact.review_version` 由 Agent 记录最终决策；
- base-first 状态链 → `parent_version_id` + 有序 `input_version_ids` + lineage root；
- latest-approved 纪律 → ReferencePack 只接受当前已批准正式图片。

Design 默认倾向一次生成带面部、正/侧/背视图的角色卡，或 2×2 场景 coverage。本地不因此删除 `image.compose_reference_sheet`：直接生成标准卡适合从零建立紧凑锨点；多张单视图经看图验收后确定性拼板，适合复用已有视图、局部修复或保留精确版本系谱。Asset Worker 根据项目、已有工件与连续性风险选择，两者是互补路线，不是新旧替换。

H3 参考语义也已明确：[Ref2VA Prompt 指南](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/references/ref-en.txt#L24-L88) 将用于定义角色/场景/风格的媒体归入 Subject/reference media；只有图片本身真正规定镜头开场、结束或构图时，才作为首帧、尾帧或 Picture 锚点。角色卡不应被自动当成首帧。

## 6. Retry、恢复与长任务

需要区分三类“重试”：

### 6.1 传输层重试

网络查询、无副作用读取和已知未提交的请求，可以使用指数退避等普通工程策略。Mini-Agent 开源示例包含固定 `max_steps` 和网络重试次数，但其定位是教学型实现，不应将这些数字复制为 Design 或当前产品的创意规则。

### 6.2 Provider 任务恢复

对 H3 等异步外部任务：

- 查询失败可安全重查；
- 下载 URL 失效可重新查询最新结果；
- Provider 明确返回创建失败时可由 Agent 选择重新提交；
- 创建请求超时且无法确定服务端是否收到时，不可盲目重复提交，应保留 `SUBMIT_UNKNOWN` 并对账。

### 6.3 创意返工

创意返工不由失败次数决定：

```text
生产当前工件
  → 独立阅读/观察它是否完成职责
  → 若未完成，定位最小根因层
  → 局部唤醒对应 Worker 或改变输入/方法
  → 再检查新工件
```

如果同一路径重复失败，Leader 应看到“当前工件 + 已尝试方法 + 可选替代方案”，语义选择继续、改变路线、接受取舍或请求用户决定。

## 7. MiniMax Design 的 ComfyUI 实现

### 7.1 可确认的开源结构

`SOURCE-CONFIRMED`：MiniMax 已开源 [MiniMax Design Plugin for ComfyUI](https://github.com/MiniMax-AI/minimax-desgin-plugin)。这不是整个 Design 产品源码，但能确认其本地执行层的一部分真实设计：

- ComfyUI 可独立打开，也可嵌入 Design/Hub；
- 嵌入模式使用 `127.0.0.1:18188`，避免与用户自有 ComfyUI 常用的 8188 端口冲突；
- Hub 可向 ComfyUI 发送加载、编辑、编译和运行工作流的命令；
- Agent 可修改节点属性、连接和参数，编译为可执行 prompt；
- 当前已打开的图可直接入队，因此未另存的当前参数修改也可生效；
- 图片、视频、音频输出可回传外层画布，并保留来源节点关系；
- 用户可修改参数、运行、更新原工作流或另存副本；
- 工作流可保存最多 200 字符的 `Agent Hint`，说明 Agent 何时可使用该工作流；
- 工作流草稿自动保存并可在 iframe 重载后恢复；
- 编译/更新前可使用 `source_sha256` 检查源工作流是否已变化，避免 Agent 基于过时图继续编辑。

`LOCAL-PACKAGE-OBSERVED`：Design 3.0.11 已正式打包 ComfyUI plugin `0.2.4`，并在 v2 Agent Profile 中提供 `comfyui-agent`。插件首次使用时下载受管的本地 ComfyUI backend，后续打开节点时自动启动；模型权重仍由用户放入 `models/`。这证明 ComfyUI 是已交付的可选高级执行面，不是所有项目的默认工作台。

### 7.2 对当前产品的正确接法

ComfyUI 不应成为剧本、项目、Agent Team 或时间线的总控制面。它更适合成为一类版本化媒体工具：

```text
WorkflowArtifact
- workflow_id / version
- graph_json
- executable_prompt_snapshot
- agent_hint
- supported_media_kinds
- required_inputs
- expected_outputs
- provider/model capability
- source revision

Agent Tool
- 选择工作流
- 绑定当前 ArtifactRefs
- 修改参数或图
- 执行并对账
- 将输出登记回 ProjectDocument
```

默认用户界面仍然是剧本/资产/分镜/Take/时间线的语义工作台。高级用户需要本地 H3、超分、插帧、修帧、抠像或定制模型时，再展开完整 ComfyUI 图。

### 7.3 与“高级执行图”的区别

产品最终可以有两种图，但不应混为同一个：

1. **Agent 生产图**：PlanGraph / WorkOrder / Artifact 的派生投影，按 Agent 泳道展示依赖、输入、输出和状态。
2. **ComfyUI 媒体图**：某一个媒体 Tool 内部的模型、节点和参数。

前者帮助用户理解“整个作品在做什么”，后者帮助高级用户改“这一个媒体处理怎么跑”。

### 7.4 画布与运行状态也应分开

`LOCAL-PACKAGE-OBSERVED`：随包 H3 示例工程的 `.hilo/canvas.json` 包含 64 个节点（33 image、15 video、13 group、2 file、1 audio）和 26 条 `derivation` 边；项目设置、活动生成记录与 Agent 对话/任务分别保存在 `.hilo/storage.json`、`.hilo/active-generations.json` 和 `.hub/opencode-export.json`。

它证明外层 Canvas 主要是“内容资产 + 派生关系 + 空间编组”，而不是唯一任务状态机。我们也应保持 `ProjectDocument/Artifacts`、Agent/WorkOrder 状态与 UI 空间布局分离；画布只是投影和编辑入口，不成为第二份项目真相。

当前 Workflow 接入的语义映射是：

| Design 概念 | 本地权威表达 |
| --- | --- |
| Canvas 内容节点/派生关系 | ProjectDocument 的版本化 Artifacts + 派生 ProductionBoardView |
| Stage Plan / Work Item | AgentRun + PlanRevision + WorkOrder |
| Script / Production Plan / Blocking Memory | ScriptVersion + ProductionDocument |
| Character / Scene / Prop anchors | Asset/CoreAssetBrief/AssetVersion/ReferencePack |
| Storyboard shot rows | ShotPlan |
| `clip_group` | ProductionDocument 中的 `clip_group_id -> ordered shot_ids` 生成组 |
| Generated clips | GenerationTask + Take |
| Post / delivery | TimelineManifest + ExportRecord |

`clip_group` 不能直接替换 ShotPlan。Design 允许多个 storyboard 行编译为一个生成片段；本地 ShotPlan 是剪辑/叙事镜头，`timeline_beats` 只表示同一连续镜头中的时间阶段。因此保留 ShotPlan 边界，再由生成组工件记录跨镜顺序和生成意图，直到实际 Provider Capability 将其编译为 GenerationTask。

## 8. MiniMax-H3 官方仓库 Skills 目录与八个领域映射

`SOURCE-CONFIRMED`：[MiniMax-H3 Skills 目录](https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills) 当前包含 1 个通用 H3 Prompt Skill 与 8 个风格/任务特化 Skill。这些并不一一等同于 Design 截图中的领域，但能直接说明 MiniMax 的分层思路。

| 官方仓库条目 | 主要方法 | 对应领域 | 当前项目的吸收位置 |
| --- | --- | --- | --- |
| [`h3-prompt-writing`](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/SKILL.md) | 五类 H3 生成模式、时间描述、稳定媒体标签、声音结构 | 所有视频 | Provider Skill 已落地，支持五类路由候选与结构化 Prompt 编译 |
| [`minimalist-product-ad-generator`](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/minimalist-product-ad-generator/SKILL.cn.md) | 卖点、产品变体、英文文案、节拍字幕、精致产品镜头 | 带货/电商、广告 | Production Method Skill，不应把 Apple 风和固定时长写入通用架构 |
| [`3d-animation-short-generator`](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/3d-animation-short-generator/SKILL.cn.md) | 故事→角色/场景卡→镜头表→分镜→单镜→合成 | 短剧/漫剧、动画 | Format + Production Method，不照搬每秒表和强审批 |
| [`brand-promo-video-generator`](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/brand-promo-video-generator/SKILL.cn.md) | 品牌事实、授权素材、故事脊柱、产品证明、CTA | 广告/营销、电商 | Production Method + Source Contract |
| [`music-video-subtitle-generator`](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/music-video-subtitle-generator/SKILL.cn.md) | Master Audio、歌词时间、节拍切点、人物/场景/字体参考分工 | MV/音乐视频 | Audio-led Production Method |
| [`papercraft-stop-motion-explainer`](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/papercraft-stop-motion-explainer/SKILL.cn.md) | 学习目标、视觉隐喻、纸艺资产、讲解分镜 | 知识/科普、动画 | Explainer Method + 可选 Style Method |
| [`paper-collage-explainer-generator`](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/paper-collage-explainer-generator/SKILL.cn.md) | 观点/知识提炼、拼贴隐喻、触感声效 | 知识/科普 | Explainer Method + 纸拼贴 Style |
| [`handdrawn-live-video-generator`](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/handdrawn-live-video-generator/SKILL.cn.md) | 手绘与实景接触、形变、追随运镜 | 其他、动画/影视 | 狭粒度 Visual Method，不是平台全局 Style |
| [`co-op-game-intro-generator`](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/co-op-game-intro-generator/SKILL.cn.md) | 人物身份、游戏菜单首图、UI 事件节拍 | 其他、动画/二次元 | 非常具体的可安装 Skill，不上升为核心架构 |

上表的“官方仓库条目”不等于“MiniMax 官方原创”。固定提交 `d21241f0a4b3acbb34c97dae47fa417b7065e438` 中的作者和收录来源是混合的；原始 `meta.yaml` 随包保留供版本维护，不进入 Agent-facing 标签、选择逻辑或质量验收。

“影视/混剪”目前没有在 MiniMax-H3 仓库找到同名 Skill。Design 可能在平台内提供未开源 Skill 或通过通用编辑能力覆盖，具体实现是 `UNKNOWN`。

### 8.1 Design 3.0.11 本机已安装 Skills

`LOCAL-PACKAGE-OBSERVED`：截图的领域与实际 Skill 不是一对一。下表是对本机 MiniMax Design 已安装 Skills 的调研记录，不代表它们已进入当前活动 Catalog：

| Skill | 值得学的方法/工件 | 当前边界 |
| --- | --- | --- |
| `anime-game-pv` | 角色身份、风格来源、色板权威、Panel/Logical Shot/Master Timeline/Render Contract | 本地包调研；未进入活动 Catalog |
| `brand-ad` | 品牌事实与出处、产品身份锚点、卖点声明、CTA、屏幕文字和品牌 QA | 本地包调研；未进入活动 Catalog |
| `cinematic-title-sequence` | 精确屏幕文字合同、字体系统、片名揭示、转场和结尾卡 | 本地包调研；未进入活动 Catalog |
| `cool-music-video` | 音乐是主时间轴，歌词、节拍、表演者、场景、字体分开引用 | 本地包调研；未进入活动 Catalog |
| `education-studio` | 学习目标、先备知识、例题/活动/测验、教学脚本和视觉解释合同 | 本地包调研；未进入活动 Catalog |
| `h3-visual-design` | 主 Skill 保持较薄，字体包装、跟踪图形、手绘实拍等技法按需加载 | 本地包调研；未进入活动 Catalog |
| `koc-video` | 产品事实 → 用户行动 → Hook → 表达载体，先写连续自然表达再拆镜 | 本地包调研；未进入活动 Catalog |
| `ui-motion` | 品牌视觉真相、交互故事板、长视频使用真实尾帧 `continuation_of` 交接 | 本地包调研；未进入活动 Catalog |
| `video-deconstruct` | 关键帧联系表、事实/推断分离、镜头/声音/连续性证据、复现合同 | 本地包调研；未进入活动 Catalog |
| `voice-clone` | 参考音频、清理、试听、QC 和可复用声线资产 | 音频调研；不是当前视频 Method |
| `skill-creator` / `skill-reviewer` | `SKILL.md + references + scripts + meta` 渐进披露与 Skill 编写质量检查 | 开发者能力；不参与创作或验收 |

`shortDrama` 并没有以一个同名下载 Skill 出现，它的主骨架在内置 `drama-series` workflow：剧本 → 生产计划 → 角色/场景锚点 → 故事板 → 关键帧 → 视频片段 → 后期。这说明“领域兴趣”、“可安装 Skill”和“项目 workflow”是三个层次。

`REPO-CONFIRMED`：当前活动 `Production Method Catalog` 已分为两层：

- 3 个 MiniMax Design 3.0.11 项目 Workflow adapter：`method.minimax_design.drama_series`、`method.minimax_design.ad_tvc`、`method.minimax_design.mv`；
- 8 个 MiniMax-H3 官方仓库收录的任务/表达 Method adapter。

Design 快照保存于 `skills/official/minimax_design/3.0.11/workflows/`，只纳入三个 active target 及 `_shared`，不纳入 `_disabled/`。H3 固定提交中的 23 个 Markdown/TXT 与 8 个 `meta.yaml` 文件原样纳入 `skills/official/minimax_h3/`。原有七个项目综合 Method 与三个自创 Narrative Skill 不再作为活动能力。

上游原文中包含为 Hub/Canvas 产品定制的进入信号、选项卡、确认步骤、时长档位和重试建议。这些文字在 vendored 源文件中完整保留，但不成为当前 Harness 的执行契约。Loader 只按 Agent 显式选择的 Skill ID 和当前角色加载精确 section，不读取上游 trigger words 做路由。用户明确值直接采用；明确要求无交互或交由 Agent 决定时继续并记录；明确要求确认才在对应工件停靠；其余只在实质信息缺口时询问。

Design Workflow 的快照来自已安装产品包，可以学习并在当前本地 POC 中映射，但不等于 MiniMax Design 私有 Agent Runtime 已开源。八个 H3 Method 的作者与收录来源混合，本地仅在内部 provenance 和 `meta.yaml` 中保留差异。适配层只添加角色、ProjectDocument、ProductionDocument 和 Tool Schema 映射。将来对外分发安装包时，再单独核对两类上游内容的发布授权。

## 9. 取其精华：应直接沉淀的通用方法

### 9.1 H3 Prompt 与 Artifact Contract

H3 官方工程和 Prompt Skill 强烈支持“先结构化上下文，后编译 Provider Prompt”：

- `subject_definitions`：稳定主体和角色定义；
- `summary`：镜头/片段总体意图；
- `retention_analysis`：每份参考应全保留、部分保留、属性转移还是弱参考；
- `detailed_description`：带时间的镜头、动作、表演、对白和切换；
- `overall_soundscape`：画内对白、环境音与音效；
- `non_diegetic_music`：非画内音乐；
- 稳定 `<Subject N>`、`<Picture N>`、`<Video N>`、`<Audio N>` 和说话人 ID；
- 只用于身份/风格的图像与真正的首帧/关键帧/构图锚点分开。

当前项目已有 `ShotPlan + ReferencePlan + H3MediaInput + Provider Prompt Skill`，不把整段剧本原样发给视频模型。`REPO-CONFIRMED`：路由系统已注册并编译 H3 官方对应的五类输入模式：

- `DIRECT / T2VA`：无边界帧硬锚定的直接生成；
- `FRAME_FIRST / I2VA`：以已批准首帧约束开场；
- `FRAME_LAST / L2VA`：以已批准尾帧约束结果；
- `FRAME_FIRST_LAST / FL2VA`：在已批准首尾帧之间生成连续过程；
- `REFERENCE / Ref2VA`：组织已批准参考资产与保留意图。

对每个可编译镜头，路由候选都会暴露首帧、尾帧和首尾帧所需的 asset-preparation 交接；镜头互动、构图或末态信号是 Route Agent 做语义取舍的证据，不是代码判断“有没有资格使用该模式”的门禁。历史 preparation 只在父 H3 plan 的 script、Bible、voice、shot/参考等完整 freshness 仍有效，且 `input_version_ids` 仍是各资产的 current approved 版本时复用。`version` 表示编译计划序号，每次 preparation/执行状态变化递增 `state_revision`；H3 plan 与 preparation 的精确 `ArtifactRef` 使旧上下文不会解析成新状态。这些合同已落地，但哪类路由能在真实成片中稳定提升质量仍需实验。

### 9.2 资产先行，但资产重度由 Agent 决定

可吸收的是：

- 角色卡将人设、气质、五官、发型、体态、服装、表演气质和稳定 ID 结构化；
- 场景卡分离 Master Wide、空间地标、功能区和局部资产；
- 下游工件引用精确资产版本，而不是只写一个名称；
- 修改已批准核心资产时，只使其真正依赖的分支失效。

不应照搬的是：

- 每个项目强制四视图；
- 每个场景强制俯视图、九宫格和所有局部；
- 资产不完美就阻止任何下游。

这与当前项目已经采用的“核心角色按需四视图，群像轻量，场景先 Master Wide 再按镜头派生”一致。

### 9.3 不同领域需要不同判断，但不应由本项目重写成平行工作流

- **短剧/漫剧**：高概念可视化、角色状态递进、反应镜头、对白表演、快节奏与回报；
- **影视/混剪**：素材语义索引、时间线剪辑意图、音画对点、来源镜头可用性和重新包装；
- **带货/电商**：受众、卖点、产品证据、展示顺序、变体/尺寸/材质可视化、CTA；
- **广告/营销**：品牌事实、授权资产、主张-证明-回报链、平台投放要求；
- **MV/音乐视频**：Master Audio 是时间真相，歌词/字幕/动作/切点按时间码绑定；
- **动画/二次元**：silhouette、pose、线条/材质、layout、运动设计和有限动画资源分配；
- **知识/科普**：学习目标、先备知识、错误理解风险、视觉隐喻、讲解与画面的信息分工；
- **其他**：使用 General Video 的中性方法，由 Agent 从完整简报中组合需要的 Skills。

上述是 Agent 理解不同项目的领域维度，不是题材路由表。`drama-series`、`ad-tvc` 和 `mv` 已直接提供三类项目主骨架，H3 Methods 再提供任务级表达方法。因此不再把通用真人短剧、动态漫或爽剧整体声明为能力缺口；具体节奏、爽点、幽默、媒介和结局由用户简报、`StoryIntent`、`EndingContract`、项目 Style 与 Agent 选中方法共同决定。真正没有上游方法也无法由通用语义组合处理的特定任务，再按项目记录精确缺口，不用本地仿写的“全能题材 Skill”填充。

## 10. 去其糟粕：不应照搬的 Skill 设计

MiniMax-H3 的风格 Skills 中存在大量为 Hub 交互定制的强制规则。这些可以帮助理解某个示例如何跑通，但不应升级为当前平台的全局架构：

- 强制全部项目经过固定 10–19 个步骤；
- 每个阶段都强制 choice card 审批；
- 强制 5/10/15 秒或 15/30/60 秒档位；
- 每镜每秒都写固定数量字段；
- 每隔固定镜头数出 hook；
- 用“确认”、“继续”等关键词解锁下一步；
- 固定第一/第二/第三次重试路线，超过次数停止；
- 将 Apple 式极简、纸艺、纸拼贴或某个 3D 动画表演规则写成平台默认 Style；
- 用 Skill 限制 Tool allowlist 或把未实现 Provider 能力写成已可执行。

我们要保留的是“工件合同、专业判断维度、可复用方法、Provider 编译规则”，不是“为了展示可控而固定每一步”。

对上游交互文字的统一适配顺序是：

1. 用户已明确给出的值直接采用，不重复询问。
2. 用户明确要求不交互或交由 Agent 决定时，Agent 完成剩余选择，将理由和假设写入 trace 后继续。
3. 用户明确要求在某份内容或某个阶段确认时，才在该语义停靠点进入 `USER_INPUT`。
4. 其余情况只在缺失信息会实质改变作品、产物或外部执行，且当前工件和 Tool 都无法解决时询问。

这是语义优先级，不实现成正则、关键词或审批次数。

## 11. 当前架构与 MiniMax 的对照

| MiniMax 公开概念 | 当前实现 | 判断 |
| --- | --- | --- |
| Main Agent / Leader | `ProductionManager + AgentRun` | 方向正确 |
| Task decomposition | `PlanRevision + PlanGraph + carried_work_order_ids` | 可表达 DAG，并在重规划时显式决定哪些未完成委派继续 |
| Method selection | `Production Method Catalog + StoryOrientation + PlanRevision` | 3 个 Design 项目 Workflow 与 8 个 H3 任务 Method 分层，Agent 可按完整语义选择、替换或移除 |
| Tool discovery | Root/Specialist compact Tool Catalog + 显式 schema request | 目录与完整 Schema 分层加载，不把 Tool 目录变成白名单工作流 |
| Worker | `WorkOrder + LanguageSpecialistAgent + LocalTeamExecutor` | 有持久化委派合同，并可并行推进就绪的独立 WorkOrders；`next_owner` 让统一 `advance` 每次只移交一个责任单元 |
| Verifier | 剧本、资产、镜头、成片等分领域验收职责 | 统一为 Team 中的 Verifier 责任，再按产物加载不同检查上下文；不再另建 Critic/Cold Read 拓扑 |
| Team state | AgentRun / Action / WorkOrder / PlanningRun / Provider Task | 分层正确 |
| Context isolation | `ContextManifest + ContextPack + 精确 ArtifactRef` | Leader 选择必读版本、边界、决定和验收方式，Loader 只校验与解析 |
| Artifact handoff | `ArtifactRef + AgentResult + ProjectDocument + stale_handoff` | 活跃交接与已过期历史交接分开，不把迟到结果静默合并回新计划 |
| Shared board | 派生 `ProductionBoardView` + append-only `WorkOrderProgressEntry` | 白板可重建，进度可累积，两者都不是第二 ProjectDocument |
| Concurrency / write ownership | `LocalTeamExecutor` + 统一项目 `WRITE/EXTERNAL` Tool 通道 | 读取与语言工作可并行，同项目可变更或外部副作用调用串行进入权威状态 |
| Method artifacts | 版本化 `ProductionDocument` + 通用 Artifact Tools/API | 品牌事实、文案锁、教学结构、音乐结构等保持稳定 identity、不可变版本和来源引用，不为每个 Method 新建状态孤岛 |
| Music-led handoff | Master Audio + AudioStructureMap + TimedTextLock + TimelineManifest | 主音频、实测结构、精确文字和最终换轨共享同一版本链；未知节拍维度明确留空 |
| Retry/recovery | Provider 对账、PlanningRun 游标、specialist turn 中断恢复、carried/stale handoff | 恢复依据持久化状态和 Agent 语义决定，没有引入创意返工次数门禁 |
| H3 route / prep | 五类路由候选 + asset-preparation freshness + `state_revision` | 输入模式、资产准备与精确状态交接已打通 |
| Storyboard generation grouping | `ShotPlan + ProductionDocument clip_group map` | 剪辑镜头与跨镜生成组分层，不把 `timeline_beats` 伪装成切镜 |
| Incremental recompute | `ChangeIntent → ImpactPlan → apply` | 很适合外层语义画布 |
| User participation | AUTO/GUIDED、USER_INPUT、编辑工件 | 后端功能基础已有，完整交互 UI 尚未组织 |

### 11.1 不合并 PlanningRun 与 AgentRun

目标层级应继续是：

```text
AgentRun / Production Manager
├─ Content Planning Job (PlanningRun compatibility name)
│  └─ 顺序保存故事意图、蓝图、逐场 Worker 写作/修订与 Verifier 验收游标
├─ parallel WorkOrders
│  └─ 资产、分镜、镜头候选、声音、调研或检查等可独立工作
└─ Provider Tasks
   └─ 提交、轮询、下载、对账和结果登记
```

AgentRun 负责整个用户目标，PlanningRun 只负责内容规划子任务的顺序恢复。如果将两者合并，会让资产、Provider 轮询、剪辑和剧本游标混成一个巨大状态机。

### 11.2 当前已落地的关键工程能力与边界

`REPO-CONFIRMED`：本轮已将之前识别的主要后端缺口实现在现有 Agent Harness 中：

1. **上游制片方法可组合**：文件化 Catalog 分为三个 MiniMax Design 项目 Workflow adapter 和八个 MiniMax-H3 任务 Method adapter；Production Manager 基于完整项目语义选择，Loader 按角色加载精确上游 section。
2. **Tool 渐进披露**：Root Planner 和 Specialist 先读取同一紧凑 Tool Catalog，确实需要精确输入/输出合同时，再显式返回 `requested_tool_schema_ids` 加载指定 Schema。
3. **上下文与交接可解析**：Leader 生成 `ContextManifest`，版本化工件使用精确 `ArtifactRef`；重规划使用 `carried_work_order_ids`，迟到结果标记 `stale_handoff`。
4. **共享视图与进度分层**：`ProductionBoardView` 从权威状态派生；`WorkOrderProgressEntry` append-only 记录过程，不覆盖旧进展。
5. **本地并行和唯一写通道**：`LocalTeamExecutor` 在一个桌面进程内并行推进就绪 WorkOrders；Production Manager 让 Leader 和 Worker 的同项目非 READ Tool 共用一条 `WRITE/EXTERNAL` 通道。`next_owner` 区分 Worker 决策、Worker Tool、Leader 重规划与用户输入；统一 `advance` 不把 Worker 返回与 Leader 收尾藏在同一次调用里。不需 Redis、Celery、租约或分布式协调。
6. **H3 资产准备与状态版本**：五类路由候选、首/尾/首尾帧 preparation、父计划完整 freshness、输入资产 current-approved 校验和 `state_revision` 已组成持久化交接。
7. **Method 工件不再只存在 Prompt**：通用 `ProductionDocument` 保存稳定文档身份、不可变结构化版本、父版本、当前项目内可解析的精确来源 ArtifactRef 和来源角色，并通过 Artifact Tools/API 精确交接；有专属不变量的音频种类只允许对应领域操作写入。
8. **音乐主轴形成最小执行闭环**：Agent 可绑定已批准 Master Audio，保存本机实测的 AudioStructureMap 与逐字 TimedTextLock；`time_origin + duration` 定义成片长度，时间线用主音频替换 Take 原生音轨，画面不足时保留尾帧、过长时裁切，导出统一规范声画长度。没有分析能力时只保留可验证元数据，不编造 BPM 或段落。

这些能力的“已落地”指代码路径、合同与定向测试存在，不代表制片效果已经通过真实项目验收。仍需实际验证的是：Design Workflow 与 H3 Method 的选择和组合、`drama-series` 的真实剧本/分镜效果、多 WorkOrder 长时运行体验、真实 H3 路由与资产锚定质量，以及 TTS/音乐/音效等真实音频能力的完整度。

当前活动 Production Method 目录为 **3 个 MiniMax Design 项目 Workflow adapter + 8 个 MiniMax-H3 任务 Method adapter**。数量只用于说明可发现范围，不是 Agent 必选清单。

## 12. Skill 分层的当前形态

截图和官方仓库 Skill 目录提供了分层依据；`REPO-CONFIRMED`：当前后端区分本地 Format adapter、上游 Production Method adapter 和 Provider Skill。Workspace Creative Preferences 仍只是未来 UI 的软偏好，不参与现有项目路由：

| 层 | 作用 | 示例 | 是否直接决定流程 |
| --- | --- | --- | --- |
| Workspace Creative Preferences | 个人常做的领域，用于排序与建议 | 短剧、电商、MV | 否 |
| Format adapter | 现有 ProjectDocument 的 schema/profile 映射 | AI 短剧、动漫、数字人、General Video | 否，不自建领域创作理论 |
| Project Workflow Skill | 完整项目的领域制片主骨架 | MiniMax Design `drama-series` / `ad-tvc` / `mv` | 否，Agent 按完整语义选择 |
| Task Method Skill | 可组合的任务或表达方法 | 8 个 MiniMax-H3 上游 Skills | 否，Agent 按当前任务组合 |
| 精确能力缺口 | 上游 Workflow、任务 Method 和通用语义组合都无法完成的具体任务 | 按项目记录 | 不仿写“全能题材 Skill”填充 |
| Provider Skill | 将中立工件编译成模型语法 | MiniMax H3 Prompt | 否，只约束技术合同 |

`ProjectBible.style` 不是第六个 Skill 目录；它是项目自己的可修改资产，记录色彩、材质、摄影、线条、动作、声音和包装表达。

当前活动项目 Workflow adapter 是：

- `method.minimax_design.drama_series`、`method.minimax_design.ad_tvc`、`method.minimax_design.mv`。

当前活动 H3 任务 Method adapter 是：

- `minimax_h3.brand_promo_video`、`minimax_h3.minimalist_product_ad`、`minimax_h3.music_video_subtitle`；
- `minimax_h3.3d_animation_short`、`minimax_h3.papercraft_stop_motion_explainer`、`minimax_h3.paper_collage_explainer`；
- `minimax_h3.handdrawn_live_video`、`minimax_h3.co_op_game_intro`。

`ai_short_drama` 只保留本地 schema/profile adapter，不再承担短剧叙事理论。`drama-series` 承担通用短剧/漫剧项目方法主骨架；3D 动画 Skill 等 H3 Method 只在项目确实需要其任务范式时叠加。爽剧、喜剧、悬疑等不需要为每个题材重建一个 Format 或本地自创 Skill，其创作承诺保存在 `StoryIntent` 和 `EndingContract`。

### 12.1 为什么不把八个领域变成八个 Profile

一个项目可能同时是：

```text
动画媒介
+ 短剧叙事
+ 品牌营销目的
+ MV 式音乐节奏
+ 荒诞喜剧叙事方法
+ 某个项目自己的二次元 Style
```

如果只能选一个 Profile，信息必然丢失；如果为组合爆炸增加枚举，又会迅速失控。来源可核验的可组合 Skill 比题材枚举更符合 Agent-native 架构；缺失方法保持为缺口，不用本地仿写填充。

## 13. 时长和风格是否需要用户指定

### 13.1 时长：重要，但不必强制用户先填

时长会直接影响：

- 内容容量、场次和冲突递进；
- 爽点/信息点密度与对白长度；
- 镜头数与声音窗口；
- H3 需要拆分的 4–15 秒执行段；
- 剪辑、字幕、音乐结构和平台交付。

建议项目保存：

```text
target_duration
acceptable_duration_range_or_note
duration_source: USER | AGENT_SUGGESTED
distribution_platform_or_viewing_context
```

用户说“大约 3 分钟”时，Agent 应直接规划并说明最后估算；用户未说时，Agent 可依据用途与内容承诺建议一个时长。时长不用于选择不同代码 workflow，3/5/10/15 分钟也只能是 UI 快捷值。

### 13.2 风格：可指定，但要分开三层含义

1. **StoryIntent**：观众应感到爽、搞笑、紧张、温暖还是可信；
2. **Method Skills**：用什么叙事/生产方法实现，例如爽点递进、MV 音频主轴、品牌主张-证明链；
3. **ProjectBible.style**：画面、摄影、材质、色彩、动作、声音和字幕包装。

用户可以提供自然语言风格或参考图。如果未提供，Agent 自主提出并写入项目工件；如果用户已明确指定，则成为后续不能静默覆盖的项目承诺。

## 14. 用户应该参与到什么程度

不应把 MiniMax 个别 Workflow/Skill 中的“每阶段必须审批”复制过来。AUTO 与 GUIDED 共用同一优先级：

| 优先级 | 处理 |
| --- | --- |
| 用户已明确给出值 | 直接采用，不重复确认 |
| 用户明确要求不交互，或交由 Agent 决定 | Agent 补齐选择，把理由和假设写入 trace 后继续 |
| 用户明确要求确认某个工件/阶段 | 仅在该语义停靠点进入 `USER_INPUT` |
| 其他情况 | 只在真正信息缺口会实质改变作品、生产路线或外部执行时询问 |

GUIDED 可以更方便地展示和编辑中间工件，但“可见”不等于“必须审批”。上述选择由 Agent 理解完整语义，不用正则、关键词或固定停靠次数实现。

用户在任意时刻可以：

- 暂停当前 AgentRun；
- 锁定某个工件不再静默修改；
- 用自然语言追加修改；
- 替换资产或 Take；
- 只重做受影响分支；
- 要求 Agent 重新规划；
- 展开高级执行图或某个 ComfyUI 工作流。

## 15. 面向当前单用户桌面产品的目标架构

```text
WorkspaceCreativePreferences
  └─ 只用于推荐、排序和默认建议

User Brief / Existing Assets / Existing Script
  ↓
Project Intent
  ├─ StorySourceContract
  ├─ StoryIntent
  ├─ Duration / delivery context
  ├─ Format schema/profile adapter
  ├─ Agent-selected Design project Workflow
  ├─ Agent-selected H3 task Methods
  ├─ exact capability gaps only when neither layer nor generic reasoning can handle a task
  └─ ProjectBible.style
  ↓
Production Manager / AgentRun / PlanGraph
  ├─ Content Planning Job (sequential screenplay state)
  ├─ Story Orientation / PlanRevision semantic method selection
  ├─ compact Root/Specialist Tool Catalog → explicit schema loading
  ├─ LocalTeamExecutor (parallel independent WorkOrders)
  ├─ Verifier WorkOrders when independent verification adds value
  └─ Provider Tasks / local media jobs / H3 route plans
  ↕
ContextManifest + exact ArtifactRef
  ├─ carried_work_order_ids for active handoffs
  └─ stale_handoff for superseded late results
  ↕
ProductionBoardView + append-only WorkOrderProgressEntry
  (derived/projected, not a second truth)
  ↕
ProjectDocument + versioned Artifacts
  ├─ Script / Bible / Character / Scene / Prop
  ├─ ProductionDocument (brand facts / copy / teaching / audio structure / other method artifacts)
  ├─ clip_group map (ordered ShotPlan IDs for one generation unit)
  ├─ Master Audio / AudioStructureMap / TimedTextLock
  ├─ Shot / ReferencePlan / ProviderPrompt
  ├─ Image / Audio / Video / Take / Inspection
  ├─ H3 DIRECT / FRAME_FIRST / FRAME_LAST / FRAME_FIRST_LAST / REFERENCE
  ├─ H3 asset-preparation freshness / state_revision
  └─ Timeline / Export
  ↕
Per-project Tool lane
  ├─ READ may overlap
  └─ WRITE / EXTERNAL serialized across Leader and Workers
  ↕
Optional advanced execution
  ├─ Agent production graph
  └─ versioned ComfyUI WorkflowArtifact
```

其中从 Production Manager 到 H3 route/preparation 的主要后端路径已实现。这是一个本地控制平面，不需多实例、多租户、分布式队列或 Agent 微服务集群。图中的 Optional advanced execution 仍是延后项，不应被读为当前已有 ComfyUI 产品集成。

## 16. 当前实现状态与后续验证

### 16.1 已落地的后端基线

1. 保留 `AgentRun / PlanningRun` 双层：前者管整个用户目标，后者管因果依赖强的顺序剧本游标。
2. Production Manager 可在 3 个 MiniMax Design 项目 Workflow adapter 与 8 个 MiniMax-H3 任务 Method adapter 中作语义选择并记录理由；Loader 只按角色装载上游精确 section。
3. Root/Specialist 共用紧凑 Tool Catalog，完整 Schema 只在 Agent 显式请求后渐进加载。
4. `ContextManifest`、精确 `ArtifactRef`、`ProductionBoardView` 和 append-only progress 已接入当前 Agent 运行时。
5. `LocalTeamExecutor`、`next_owner`、统一无人值守 `advance`、`carried_work_order_ids/stale_handoff` 与同项目 `WRITE/EXTERNAL` 串行通道已接入单进程桌面运行模型。
6. H3 五类路由候选、首/尾/首尾帧 asset-preparation、freshness 校验和 `state_revision` 精确交接已落地。
7. 通用版本化 `ProductionDocument` 及其 Artifact Tools/API 已落地，可作为不同 Production Method 的统一结构化中间工件。
8. Master Audio、AudioStructureMap、TimedTextLock、音频 Tool/API、TimelineManifest 投影与导出换轨已落地；真实音乐理解质量仍需样本验证。
9. Design Canvas/Hub 语义已映射到现有权威工件与派生 ProductionBoardView；`clip_group` 用 ProductionDocument 保存跨 ShotPlan 生成组，不改写 ShotPlan 或 `timeline_beats` 语义。
10. `image.compose_reference_sheet` 仍作为多张已审视图的确定性拼板能力，与 Design 一次直接生成标准角色/场景卡的方法并存。

### 16.2 现在需要的验证

1. 用不同类型的完整项目观察 Story Orientation 和 PlanRevision 是否会选出合适、克制的 Method/Skill 组合，不将“能选”当成“选得好”。
2. 用真实长任务验证多 WorkOrder 进度可观测性、中断恢复、重规划携带与迟到结果处理是否符合预期。
3. 用可控对照观察 H3 `DIRECT/FRAME_FIRST/FRAME_LAST/FRAME_FIRST_LAST/REFERENCE` 在不同镜头上的质量、一致性、成本与失败模式，而不为任一模式设固定使用门禁。
4. 用不同音乐类型验证本机节拍/起音/能量分析是否足够支撑 MV 编排，并观察 Agent 是否把 Master Audio 结构转成有意义的镜头与动作，而不是机械逐拍切镜。
5. 继续用完整剧本、资产、分镜和成片检验实际创作质量；发现效果问题时追回 StoryIntent、Skill、工件合同或 Provider 输入层，不用固定重试次数和硬门禁代替诊断。

### 16.3 仍延后的产品化

1. 完整桌面语义制片工作台与多模态画布；
2. Agent 生产图高级视图，不要求用户手工连完流程；
3. Workspace Creative Preferences 和 Skill Plaza 排序界面；
4. 在经实验证明有价值的本地 H3/媒体工作流上嵌入 ComfyUI；
5. 用 `WorkflowArtifact + Agent Hint + 输入/输出合同` 管理工作流；
6. 允许高级用户查看、修改、另存和重跑某个媒体分支。

## 17. 已确认、推断与未知

### 已确认

- Design 是本地桌面创作工作台，支持 Agent、Skills、本地资产与多模态画布。
- MiniMax 公开描述 Main Agent 和 copy/image/video/audio 并行能力。
- Agent Team 公开 Leader/Worker/Verifier、状态机、上下文隔离、handoff 和 shared board 思路。
- Design ComfyUI 插件支持嵌入、Agent 编辑/编译/执行、草稿恢复、Agent Hint 和结果回传。
- MiniMax-H3 官方仓库有 1 个通用 Prompt Skill 和 8 个任务/风格 Skills。
- 本机 Design 3.0.11 的新项目默认是 v2 认知职责拓扑：`media-agent` 为 primary，`router/planner/executor/comfyui-agent` 按需调度；v1 仍作旧项目兼容和回退。
- 当前 active project workflows 是 `ad-tvc`、`drama-series`、`mv`；KOC 由 Skill 承接，八个兴趣不是八条 workflow。
- `selectedInterests` 在客户端的已确认作用是持久化和分析上报；未发现 Agent/Skill 运行时读取。
- ComfyUI 0.2.4 已随包发布，但仍是可选插件/专用 Agent 路径。
- 当前仓库的活动 Production Method Catalog 暴露 3 个 MiniMax Design 项目 Workflow adapter 与 8 个 MiniMax-H3 任务 Method adapter，由 Agent 按完整项目语义分层选择。
- Design 3.0.11 的 active workflow 原文已按版本保存，只纳入 `drama-series`、`ad-tvc`、`mv` 与 `_shared`，排除 `_disabled/`。
- 8 个 MiniMax-H3 官方仓库收录 Skill 对应的 23 个 Markdown/TXT 与 8 个 `meta.yaml` 文件已按固定提交原样 vendoring；英文附属 references 同时保留原文与忠实中译。上游来源混合，只用于内部版本维护，薄适配层按角色加载精确 section。
- `AI_SHORT_DRAMA` 只保留本地 schema/profile adapter；通用短剧/漫剧项目方法主骨架由 `drama-series` 提供，不再声明为整体能力缺口。Critic、Repair、Review Router 和 Final Cold Read 只作历史状态兼容，活动责任拓扑为 Leader → Worker → Verifier。
- `EndingContract` 优先于 `drama-series` 的连载默认；用户要求单篇完结时不强制留钩。
- Root Planner 和 Specialist 已共用紧凑 Tool Catalog 与显式 Schema 渐进加载合同。
- `ContextManifest`、精确 `ArtifactRef`、派生 `ProductionBoardView`、append-only progress、`LocalTeamExecutor` 和 carried/stale handoff 已进入当前 Agent 运行时。
- 版本化 `ProductionDocument` 已作为跨 Method 的统一结构化工件；Master Audio、AudioStructureMap、TimedTextLock 和时间线换轨已有后端闭环。
- Leader 和 Worker 的同项目非 READ Tool 已共用 `WRITE/EXTERNAL` 串行通道，H3 已支持五类路由候选及 preparation freshness/`state_revision` 交接。

### 合理推断

- 截图中最多三个领域适合被定义为个性化推荐和能力发现先验，而不是项目硬路由。
- Design 外层画布是语义生产图，ComfyUI 是可展开的局部执行图。
- Design 很可能使用类似 Agent Team 的架构原则，但不能证明两者是同一份生产代码。

### 仍未知

- Design Main Agent、Skill 选择器和 Team Engine 的完整实现；
- 八个创作领域是否被服务端用于首页推荐或远程个性化；
- Shared Board 的真实存储 schema 与冲突处理；
- 线上调度优先级、服务端停止策略和与公开 Agent Team 控制平面的实际共用程度；
- 外层 Design 画布除随包示例之外的完整数据模型和迁移规则；
- 平台内未开源的短剧、混剪、电商等 Skills 全文；
- 不同 Skills 在真实 Design 成片中的质量和稳定性。

### 本项目仍待实验验证

- Agent 在不同项目中对 Design Workflow 与 H3 Task Method 的选择与组合质量；
- LocalTeamExecutor 在真实长任务中的体验、恢复与交接效果；
- H3 五类路由和资产准备交接对一致性、动作完整性与成片质量的实际改善；
- 完整剧本到成片闭环的稳定性与用户满意度。

## 18. 主要一手资料

- [MiniMax Design 官方站](https://design.minimax.io/)
- [MiniMax Design 官方使用指南](https://my.feishu.cn/wiki/VEoVwpfCKiTHvHkAGQ7cQJxCncf)
- [MiniMax H3 本地/ComfyUI 活动页](https://design.minimaxi.com/campaign)
- [Agent Team: Building a Production-Grade Multi-Agent System](https://www.minimax.io/blog/minimax-agent-team-long-running-1779893953)
- [MiniMax Agent: What We Learned While Building in 2025](https://www.minimax.io/news/minimax-agent-what-we-learned-while-building-in-2025)
- [Why Is Interleaved Thinking Important for M2](https://www.minimax.io/news/why-is-interleaved-thinking-important-for-m2)
- [MiniMax M2 Technical Report](https://arxiv.org/abs/2605.26494)
- [MiniMax M2.7](https://www.minimax.io/news/minimax-m27-en)
- [MiniMax M3](https://www.minimax.io/blog/minimax-m3)
- [MiniMax-H3 官方仓库](https://github.com/MiniMax-AI/MiniMax-H3)
- [MiniMax-H3 Skills README](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/README.md)
- [MiniMax Design Plugin for ComfyUI](https://github.com/MiniMax-AI/minimax-desgin-plugin)
- [Mini-Agent 官方仓库](https://github.com/MiniMax-AI/Mini-Agent)
- [MiniMax API Overview](https://platform.minimax.io/docs/api-reference/api-overview)
