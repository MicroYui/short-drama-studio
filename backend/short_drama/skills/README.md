# 视频制片 Skills 目录

这里保存的是 Agent 可读取、产品和创作人员也可以直接审阅的制片方法。它不保存项目状态，不直接调用 Provider，也不定义另一套 Tool。

## 从哪里看完整流程

- [`workflows/end_to_end.md`](workflows/end_to_end.md)：所有视频类型共享的“目标与输入 → 内容/剧本 → ProductionSpec → 资产 → Storyboard/ClipGroup → GenerationPlan/GenerationUnit → H3/Take → 时间线 → 验收与导出”方法。
- `shared/roles/*.md`：所有格式共享的中文角色职责，例如故事架构、编剧、内容审校、导演和资产审核。
- `formats/<format>/SKILL.md`：与当前数据模型的宽粒度格式适配。`AI_SHORT_DRAMA` 现只保留本地 schema/profile 边界，不再自建短剧创作理论。
- `formats/<format>/playbooks/default.md`：格式与当前 ProjectDocument/Tool 工件的映射，不是第二套创作工作流。
- `methods/minimax_design_*/SKILL.md`：MiniMax Design 3.0.11 三个活动项目 Workflow 的本地薄适配层，分别对应短剧系列、广告/TVC 和 MV。
- `methods/minimax_h3_*/SKILL.md`：八个 MiniMax-H3 上游 Skill 的本地薄适配层，只负责角色与工件映射。
- `official/minimax_design/3.0.11/workflows/`：从本机 MiniMax Design 3.0.11 安装包保存的活动 Workflow 原文快照；仅收录 `drama-series`、`ad-tvc`、`mv` 及其共享文档，不把 `_disabled/` 当成当前能力。
- `official/minimax_h3/<skill>/`：按固定上游提交完整保存的 31 个文件，包括 23 个 Markdown/TXT 与 8 个原始 `meta.yaml`；`official/` 表示官方仓库快照，不等于目录中每个 Skill 都是 MiniMax 官方原创。后续更新通过正常 Git diff 审阅，不增加文档哈希锁。
- `translations/minimax_h3/<skill>/`：官方目录中只有英文的附属 references 的忠实中文翻译；原始英文文件仍完整保留在 `official/`。
- `providers/<provider>/SKILL.md`：把 provider-neutral 镜头事实翻译成特定模型语法的 Provider Skill。
- `providers/<provider>/references/*.md`：模型 Prompt 结构、引用方式和维护边界；易变 API 参数仍以 Tool Schema/Capability 为准。

## 运行时如何使用

1. 项目创建时把所选视频类型保存为 `ProductionProfile`。
2. Production Manager 根据当前项目、用户目标、已完成工件和记忆编译 `ContextPack`。
3. Production Manager 基于完整 ProjectDocument、用户目标和已有工件，先判断是否需要一个 MiniMax Design 项目 Workflow 作为主骨架，再组合零个或多个 MiniMax-H3 任务方法。
4. Loader/编译器只校验 Skill ID 和角色支持，不用关键词、正则、trigger words、题材枚举或必选数量替 Agent 做选择。
5. 角色 Prompt 按“共享中文职责 → 本地格式适配 → Agent 选中的上游 Method 的角色相关原文/忠实中译精确 section → 当前任务数据”组合。
6. Agent 生成可替换的 `PlanRevision`，自行跳过、合并、重排或并行步骤，并选择要调用的 Tool 或要委派的专业 Agent。委派时 `assigned_agent` 只表示接收者身份，Production Manager 必须根据完整任务语义显式选择 `production_skill_role`；编译器只按这个字段装载职责，不从名称或文本关键词猜角色。对会持续修订的交付物，WorkOrder 使用 `expected_output_slot_ids` 指定稳定逻辑槽位，同时仍以精确 ArtifactRef 作为输入和结果真相。
7. 专业 Agent 需要 Tool 时，一次外部执行只进行一次模型决策，或者执行一个已持久化的 Tool 请求；请求和结果写入 `WorkOrder.specialist_turns`，下次再由 Agent 语义决定继续或完成，不在进程内隐藏循环。
8. 无人值守统一 `advance` 根据持久化 `next_owner` 每次推进一个独立 Worker 批次或一个 Leader 单元；`LEADER_REPLAN` 不会被 TeamExecutor 重复消费，`USER_INPUT` 会停给用户，批次失败可见且不会自动重放。
9. Harness 只校验依赖、参数和状态，持久化进度并在获得新结果后继续规划；它不把上述制作方法解释为固定节点图。

仓库收录 Method adapter 在 frontmatter 中显式声明各角色需要的源文件和 Markdown 标题。Loader 只加载这些精确 section，不把整份上游 Skill 当成当前 Harness 的可执行 constitution。

H3 Prompt Compiler 还会读取 `providers/minimax_h3/SKILL.md` 及其 `prompt_references`。这层只翻译字段结构、时间线、对白和媒体标签，不负责选择剧情、镜头或提交 Provider 请求。它可以把一个多 Shot GenerationUnit 按组内 source windows 编译为一次 H3 请求，但不消除原 Shot 的叙事和编辑边界。

### 方法选择与组合边界

活动 Catalog 分两层：

- 3 个 MiniMax Design 项目 Workflow：`drama-series`、`ad-tvc`、`mv`，提供从输入到成片的领域主骨架；
- 8 个 MiniMax-H3 任务/风格 Method，提供可在具体制片任务中按需装载的方法。

Agent 基于完整语义选择覆盖当前任务的最小充分组合。一个项目通常只需要一条主 Workflow，但这是语义判断，不由代码实现互斥、优先级、关键词路由或固定选择数量。

之前的七个 `PROJECT_SYNTHESIS` Method 和三个自创 Narrative Skill 不再作为活动能力。它们可用于回溯历史设计，但不能冒充 MiniMax 官方 Skill，也不应默认注入创作。

### 交互优先级

- 用户已明确给出的值直接采用，不因 Workflow 原文包含确认步骤而重复询问。
- 用户明确要求不交互，或把选择交给 Agent 时，Agent 自主补齐、记录决定与假设后继续。
- 用户明确要求确认某个内容或阶段时，才在对应语义停靠点进入 `USER_INPUT`。
- 其余情况只在信息缺口会实质改变作品、生产路线或外部执行，且 Agent 无法从项目工件与 Tool 获得时询问。

以上均由 Agent 语义判断，不用正则、关键词或固定审批次数路由。

## 来源边界

三个 Design Workflow 来自本机 MiniMax Design 3.0.11 安装包的活动 workflow target 快照，不宣称其私有 Agent Runtime 已开源。八个 H3 Method 的上游作者与收录来源混合；原始 `meta.yaml` 和 `upstream_sources` 随 adapter 保存，仅用于版本维护和避免误称。Agent-facing 标签保持中性，来源不参与 Skill 选择、组合或质量验收。

固定提交 `d21241f0a4b3acbb34c97dae47fa417b7065e438` 中的这八个任务/风格 Skill 已将 31 个上游文件原样纳入 `official/minimax_h3/`。运行时由薄 adapter 按角色加载上游原文或忠实中译的精确 section；上游 Hub/Canvas 操作、强制确认、固定时长或固定重试次数只作来源内容保留，不成为当前 Harness 的执行契约。

H3 Route Tool 向 Route Agent 暴露当前镜头技术上合法的全部候选与结构化证据，不用本地 top-k、候选序号、QA 状态或分数先代替它做选择。Route Agent 显式返回候选 ID、参考媒体 ID 和本批生成意图；同一 Route Plan 重复执行保持幂等，需要继续探索时由 Agent 建立新计划。没有显式 Agent 或调用方决定时保持 `NEEDS_AGENT_DECISION/PARTIAL`，不回退到本地自动选片或自动提交。

桌面安装包通过 `electron-builder.yml` 的 `short_drama/skills/**/*.md`、`**/*.txt` 和 `**/*.yaml` 规则携带这些运行时文档与来源描述。以后若 Skill 引入其他资产，需要同时更新打包过滤与回归测试，不能只验证开发目录。

因此，AI 短剧、数字人口播、动漫和通用视频共用同一套项目、剧本、资产、生成、检查和时间线 Tools。它们的区别来自格式 Skill、角色 Prompt、用户目标与当前工件，而不是复制四套底层能力。具体视觉风格写入项目 Bible，不由 ProductionProfile 硬编码。

`formats/ai_short_drama` 仍作为本地 schema/profile adapter 存在，只保留剧本、单集、资产、镜头、Take 和时间线的现有数据映射；它不再定义竖屏节奏、爽点、喜剧或审校方法。`drama-series` 已提供通用短剧/漫剧的项目方法主骨架，因此不再把“通用真人短剧、动态漫或爽剧”整体声明为能力缺口。具体题材、节奏、爽点和表达仍由用户要求、`StoryIntent`、`EndingContract`、项目 Style 与 Agent 选中的任务方法共同决定，无需为每个题材恢复一套本地自创 Skill。

Design 文档中的 Hub/Canvas/Stage Plan 是产品与交接语义，在本系统分别落到 ProjectDocument/版本化 ProductionDocument、AgentRun/PlanRevision/WorkOrder 和派生 ProductionBoardView，不覆盖本地 Harness。ProductionSpec、Storyboard 和 GenerationPlan 是受管的版本化 ProductionDocument；`ProjectOutputSlot` 只保存稳定逻辑输出的当前与历史 ArtifactRef。Design storyboard 的 `clip_group` 在本地是 Storyboard 中的类型化 ClipGroup，可将多个 ShotPlan 合为一个 GenerationUnit/H3 生成片段；`ShotPlan` 仍是剪辑镜头，`timeline_beats` 仍只表示同一连续镜头内部阶段。GenerationUnit 以 source windows 保留每个 Shot 边界，不改写 ShotPlan 语义。

## 当前执行边界

- 不启用、不集成、不分发 LTX 模型；LTX 只是后续桌面编辑、多轨时间线和 FFmpeg 导出底座。
- 当前后端验收使用 Fake Provider 和本地媒体，不调用真实付费 Provider。Tool Catalog 中存在 `REAL_H3` 执行能力不等于获得了本轮真实提交授权。
- ComfyUI plugin、语义画布和 LTX 编辑器双向桥接按 PRD 放在几分钟成片 PoC 之后，不在当前 Agent 后端闭环中伪造已接通状态。

如果以后增加“短剧/漫剧、广告/营销、MV/音乐视频、知识/科普”等入口，它们只是 `CreatorInterests/SkillCategory` 软偏好，用于示例和 Skill 排序。它们不得改写当前 ProjectDocument 的语义、限制 Tool/Skill，或替代 Production Manager 对当前目标的判断。

## 故事合同、格式与 Style 的边界

- `ProductionProfile` 只表达 AI 短剧、通用视频、数字人、动漫这类宽粒度媒介/生产差异，不是题材、情绪、爽文或喜剧枚举。
- `StorySourceContract` 保存用户明确承诺、明确禁止、交给 Agent 的开放选择与后续权威覆盖，防止修订时把用户要的故事换掉。`SCRIPT_LOCKED` 普通文本由 Story Orientation Agent 依据完整语义整理到 `locked_dialogue`；调用方也可直接给结构化条目。代码只校验逐字出现、原始顺序与调用方结构未被改写，不用正则、冒号模板或关键词推断说话人和旁白类型。
- `StoryIntent` 保存目标观众体验、戏剧引擎、推进策略、调性/可信度、受保护元素与偏移风险；它是后续 Agent 可讨论、可修订的创作方向，不是评分表或质量门。
- `EndingContract` 保护本集要到达的最终状态、必须闭合/允许开放的线索、最后画面/台词意图和续集策略；它不是要求 Writer 把每个 closure 分别演成认可、授予、核验或交接的仪式清单。多个 closure 若只证明同一次状态变化，应由最有力的决定性动作统一兑现。
- `drama-series` 中的连载钩子、付费点和下集动力只在 `EndingContract` 允许系列延续时采用；用户要求单篇完结时，不得被 Workflow 默认覆盖。
- `ProjectBible.style` 描述视觉、摄影、声音、材质、色彩等项目风格，不代替 `StoryIntent` 的叙事体验，也不由格式 Skill 预设写实/动画等答案。
- 人物、地点、道具和台词归属以稳定 `character_id`、`location_id`、`prop_id`、`speaker_character_id` 连接；名称只用于展示。媒体参考使用结构化 `kind`、`target_id`、`purpose` 和 `required`，不从自由文本猜身份或必需性。

四视图、场景俯视/全景/功能区参考、分镜时间段、镜头/动作/台词/情绪拆解，以及 H3 中真实 `@` 媒体绑定，分别属于资产 Agent、导演/分镜 Agent 与 Provider Prompt Compiler。这些能力在剧本批准后读取剧本工件，不注入共享 Story Architect 令其过早为某个模型或资产表达写故事。

## 哪些顺序仍写在代码里

这里的 Markdown 描述创作方法，不取代三类为了正确恢复而保留的内部状态机：

- 首次剧本规划作为 **Content Planning Job** 运行，由 AgentRun/Production Manager 这个 Leader 总调度。Leader 将当前写作或修订目标交给 Writer Worker，再由一个 Verifier 对照用户要求、来源承诺和当前完整稿验收。未通过时，Leader 将具体问题退回原 Worker 聚焦修订。底层仍使用 `PlanningRun` 的持久化游标和 Issue Ledger 保证恢复；`SCRIPT_CRITIC`、`STORY_REPAIR`、`REVIEW_ROUTER`、`FINAL_COLD_READ` 等名称只为历史数据或状态兼容保留，不再对应新计划中的独立 Agent/Skill。任何修订仍按场持久化，不设固定轮数、问题数、严重度或总超时门槛。
- 专业 Agent 的 WorkOrder 以 `specialist_turns` 保存每次 Tool 请求、理由、状态和结果。一次外部 `execute` 只进行模型决策或一个 Tool 执行；下次调用再从已保存历史继续。重启时尚未落盘结果的 `IN_PROGRESS` turn 变为 `INTERRUPTED`，WorkOrder 回到 `NEEDS_ATTENTION`，由 Agent 根据规范项目状态决定对账、重试或改变路线，不自动重放。单步边界用于可观察、可恢复，不是固定 Tool 次数上限。
- 图像、视频、语音等 Provider Task 仍按提交、轮询、下载、成功/失败等真实外部状态推进。

`ProjectDocument` 及其版本化领域工件始终是项目内容真相；Content Planning Job 的中间状态不能成为第二份可独立编辑的剧本或项目控制面。AgentRun 负责目标、计划、何时推进作业以及作业结束后的下一步，Content Planning Job 只负责其内部内容规划游标。

现行责任拓扑是 `Leader → Worker → Verifier`。Story Architect、Writer、Director、Asset Worker 等是 Worker 的专业职责，而不是另一套总调度；Verifier 根据产物类型执行独立验收。历史 Critic/Repair/Cold Read 枚举可继续解析旧持久化数据，但新计划不为它们各自建立格式 Skill 或独立审校 Agent。

当前不建立通用 `ChildJob` 基类或统一子作业状态机。只有未来出现多个确实共享生命周期、恢复与取消语义的领域作业，并且重复已经可观察时，才从 Content Planning Job 和这些同级作业中提取共同合同。

这些状态机只保证作业顺序和恢复，不为不同视频类型各写一套端到端 workflow。项目进入正常制作后，下一步仍由 Agent 的 `PlanRevision` 决定。

## 已实现与待实验

当前方法层已包含 3 个 MiniMax Design 项目 Workflow adapter 和 8 个 MiniMax-H3 任务 Method adapter，并保留现有 Tool、ContextManifest、WorkOrder、ProductionBoard 和 H3 路由交接。这证明上游 Markdown 能被发现、按角色加载并映射到本地 Harness，不代表不同题材的真实创作质量已经通过样本验证。

## 关于经验

格式 Skill 只保存跨项目稳定成立的指导，不存放某个合同、融资、办公室等具体样片经验。`references/` 保存的也是可复用方法，不是某个项目的样片记录。项目执行中形成的经验由 AgentRuntime 的 `ExperienceMemory` 按项目、Skill 和证据保存，并在相关任务中按需召回。
