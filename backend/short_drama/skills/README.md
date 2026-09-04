# 视频制片 Skills 目录

这里保存的是 Agent 可读取、产品和创作人员也可以直接审阅的制片方法。它不保存项目状态，不直接调用 Provider，也不定义另一套 Tool。

## 从哪里看完整流程

- [`workflows/end_to_end.md`](workflows/end_to_end.md)：所有视频类型共享的“目标与输入 → 内容/剧本 → 资产 → 场次与分镜 → 候选生成 → 检查与局部修复 → 时间线 → 验收与导出”方法。
- `shared/roles/*.md`：所有格式共享的中文角色职责，例如故事架构、编剧、内容审校、导演和资产审核。
- `formats/<format>/SKILL.md`：该视频类型长期稳定的创作原则。
- `formats/<format>/playbooks/default.md`：该类型叠加在共享流程上的详细制作方法；每步说明输入、判断、Tool、产物和返工，但不是第二套固定工作流。
- `formats/<format>/roles/*.md`：只补充当前格式对编剧、导演、审校等角色的特有要求，不重复定义通用角色。
- `formats/<format>/references/*.md`：只在相关专业角色需要时加载的深入方法，避免把全部细节重复塞给每个 Agent。AI 短剧的创作维度位于 `formats/ai_short_drama/references/ai_native_forms.md`，观众行为与节奏诊断位于 `audience_behavior_and_rhythm.md`。
- `narrative/<skill>/SKILL.md`：可组合的叙事方法及紧凑语义描述；可只声明自己支持的角色。Agent 根据当前项目语义选择零个或多个，Loader 不做关键词映射、题材枚举或必选数量规则。
- `providers/<provider>/SKILL.md`：把 provider-neutral 镜头事实翻译成特定模型语法的 Provider Skill。
- `providers/<provider>/references/*.md`：模型 Prompt 结构、引用方式和维护边界；易变 API 参数仍以 Tool Schema/Capability 为准。

## 运行时如何使用

1. 项目创建时把所选视频类型保存为 `ProductionProfile`。
2. Production Manager 根据当前项目、用户目标、已完成工件和记忆编译 `ContextPack`。
3. Story Orientation Agent 先从完整用户输入形成 `StorySourceContract` 与 `StoryIntent`，再查看叙事 Skill 的 ID、说明和支持角色，按项目语义显式选择零个或多个 Narrative Skills。
4. Loader/编译器只校验 Skill ID 存在且支持当前角色，不用关键词、正则、题材枚举或必选数量替 Agent 做选择。
5. 角色 Prompt 按“共享中文角色职责 → 宽粒度格式 Skill/Playbook → Agent 选中的 Narrative Skills → `StoryIntent` 与当前任务数据”组合；Planner 另读共享流程和当前 Tool Catalog。
6. Agent 生成可替换的 `PlanRevision`，自行跳过、合并、重排或并行步骤，并选择要调用的 Tool 或要委派的专业 Agent。委派时 `assigned_agent` 只表示接收者身份，Production Manager 必须根据完整任务语义显式选择 `production_skill_role`；编译器只按这个字段装载职责，不从名称或文本关键词猜角色。
7. 专业 Agent 需要 Tool 时，一次外部执行只进行一次模型决策，或者执行一个已持久化的 Tool 请求；请求和结果写入 `WorkOrder.specialist_turns`，下次再由 Agent 语义决定继续或完成，不在进程内隐藏循环。
8. Harness 只校验依赖、参数和状态，持久化进度并在获得新结果后继续规划；它不把上述制作方法解释为固定节点图。

角色文件可以在 frontmatter 中声明 `references: [...]`。Loader 只把该角色真正需要的方法附加到它的 Prompt。例如 AI 短剧编剧读取视听形态与对白表演方法，资产审核读取 Style 与连续性方法，避免所有角色都携带全部资料。

H3 Prompt Compiler 还会读取 `providers/minimax_h3/SKILL.md` 及其 `prompt_references`。这层只翻译字段结构、时间线、对白和媒体标签，不负责选择剧情、镜头或提交 Provider 请求。

H3 Route Tool 向 Route Agent 暴露当前镜头技术上合法的全部候选与结构化证据，不用本地 top-k、候选序号、QA 状态或分数先代替它做选择。Route Agent 显式返回候选 ID、参考媒体 ID 和本批生成意图；同一 Route Plan 重复执行保持幂等，需要继续探索时由 Agent 建立新计划。没有显式 Agent 或调用方决定时保持 `NEEDS_AGENT_DECISION/PARTIAL`，不回退到本地自动选片或自动提交。

桌面安装包通过 `electron-builder.yml` 的 `short_drama/skills/**/*.md` 规则携带这些运行时文档。以后若 Skill 引入非 Markdown 资产，需要同时更新打包过滤与回归测试，不能只验证开发目录。

因此，AI 短剧、数字人口播、动漫和通用视频共用同一套项目、剧本、资产、生成、检查和时间线 Tools。它们的区别来自格式 Skill、角色 Prompt、用户目标与当前工件，而不是复制四套底层能力。具体视觉风格写入项目 Bible，不由 ProductionProfile 硬编码。

## 故事合同、格式与 Style 的边界

- `ProductionProfile` 只表达 AI 短剧、通用视频、数字人、动漫这类宽粒度媒介/生产差异，不是题材、情绪、爽文或喜剧枚举。
- `StorySourceContract` 保存用户明确承诺、明确禁止、交给 Agent 的开放选择与后续权威覆盖，防止修订时把用户要的故事换掉。`SCRIPT_LOCKED` 普通文本由 Story Orientation Agent 依据完整语义整理到 `locked_dialogue`；调用方也可直接给结构化条目。代码只校验逐字出现、原始顺序与调用方结构未被改写，不用正则、冒号模板或关键词推断说话人和旁白类型。
- `StoryIntent` 保存目标观众体验、戏剧引擎、推进策略、调性/可信度、受保护元素与偏移风险；它是后续 Agent 可讨论、可修订的创作方向，不是评分表或质量门。
- `EndingContract` 保护本集要到达的最终状态、必须闭合/允许开放的线索、最后画面/台词意图和续集策略；它不是要求 Writer 把每个 closure 分别演成认可、授予、核验或交接的仪式清单。多个 closure 若只证明同一次状态变化，应由最有力的决定性动作统一兑现。
- `ProjectBible.style` 描述视觉、摄影、声音、材质、色彩等项目风格，不代替 `StoryIntent` 的叙事体验，也不由格式 Skill 预设写实/动画等答案。
- 人物、地点、道具和台词归属以稳定 `character_id`、`location_id`、`prop_id`、`speaker_character_id` 连接；名称只用于展示。媒体参考使用结构化 `kind`、`target_id`、`purpose` 和 `required`，不从自由文本猜身份或必需性。

四视图、场景俯视/全景/功能区参考、分镜时间段、镜头/动作/台词/情绪拆解，以及 H3 中真实 `@` 媒体绑定，分别属于资产 Agent、导演/分镜 Agent 与 Provider Prompt Compiler。这些能力在剧本批准后读取剧本工件，不注入共享 Story Architect 令其过早为某个模型或资产表达写故事。

## 哪些顺序仍写在代码里

这里的 Markdown 描述创作方法，不取代三类为了正确恢复而保留的内部状态机：

- 首次剧本规划作为 **Content Planning Job** 运行，由 AgentRun 总调度。当前代码和持久化合同仍沿用兼容实现名 `PlanningRun`；它只保存恢复这项内容规划所需的游标和中间工件，依次推进 Story Orientation、Story Architect 轻量蓝图与 `EndingContract`、逐场写作和持久化、一次完整 Critic、Repair Planner/逐场修订，以及增强 Verifier。Critic 把同一语义问题以稳定 `issue_id` 纳入 Issue Ledger，保留语义分类、状态、文本/修复证据、演变说明、已接受取舍和下游交接。Repair Planner 诊断哪些 issue 需要实际改稿；修订 Writer 按场持久化改动。增强 Verifier 既对照 Ledger 验收修复与新回归，也脱离修复说明，根据用户简报、`StoryIntent`、`EndingContract` 和当前完整剧本做自然冷读；它直接决定接受当前稿，或带 issue ID、场次范围和理由继续聚焦修订。聚焦修订后重新交给增强 Verifier，不重跑完整 Critic。Writer 与修订 Writer 保存当前成稿的自然表演时长证据，Critic 和增强 Verifier 按实际文本复核；这些证据交给 Agent 理解取舍，不形成字数、语速或偏差门槛。每个场次还会继承前文的人物关系、群体态度、资源和权限，换地点、见证者或交接形式不会自动把同一状态变化算作新升级。Production Manager 执行 Critic/Repair/Verifier 的显式语义决定，不再运行空的串行 Review Router Agent，也不叠加严重度、问题数、关键词或循环次数门槛。默认链路不再进入独立 `FINAL_COLD_READ`，该类型只作历史数据和持久化兼容，不作为现行角色阶段排入新计划。只要系统负责原创或扩写且配置了语言 Provider，所属 AgentRun（包括 `AUTO_DRAFT` 和 `GUIDED`）就进入该作业，不以目标时长分流。不带 AgentRun owner 的直接兼容创建只建立本地结构骨架，不调用模型生成整稿。`SCRIPT_ONLY` 在增强 Verifier 接受当前稿后进入 `SCRIPT_READY`；`SCRIPT_AND_SHOTS` 才继续形成分镜。Production Manager 通过 `planning.advance` 每次推进一个单元，一次 HTTP 最多成功执行一个该动作，并在返回时暴露下一游标或终态。任何“整稿修订”也要拆为 `SCENE_REVISION 1/N…N/N` 队列，不覆盖已完成进展。场景总数和跨场修订范围不再受 40 场、12 场这类低常数创作上限约束；长任务仍以持久化单元推进。作业不设固定总超时或创意修订轮次上限，也不用重试次数、错误指纹或预设时间阈值替代 Agent 的进展判断；Provider 或结构校验失败时保留当前游标和证据，交给 Agent 决定继续、换路线或请求用户协助。
- 专业 Agent 的 WorkOrder 以 `specialist_turns` 保存每次 Tool 请求、理由、状态和结果。一次外部 `execute` 只进行模型决策或一个 Tool 执行；下次调用再从已保存历史继续。重启时尚未落盘结果的 `IN_PROGRESS` turn 变为 `INTERRUPTED`，WorkOrder 回到 `NEEDS_ATTENTION`，由 Agent 根据规范项目状态决定对账、重试或改变路线，不自动重放。单步边界用于可观察、可恢复，不是固定 Tool 次数上限。
- 图像、视频、语音等 Provider Task 仍按提交、轮询、下载、成功/失败等真实外部状态推进。

`ProjectDocument` 及其版本化领域工件始终是项目内容真相；Content Planning Job 的中间状态不能成为第二份可独立编辑的剧本或项目控制面。AgentRun 负责目标、计划、何时推进作业以及作业结束后的下一步，Content Planning Job 只负责其内部内容规划游标。

当前现行角色阶段是 `STORY_ARCHITECT`、`SCENE_WRITER`、`SCRIPT_CRITIC`、`STORY_REPAIR`、`REVISION_VERIFIER` 和 `SCENE_DIRECTOR`。Story Repair 重新诊断审校证据并制定聚焦修复方案；增强 Verifier 同时读取 Issue Ledger/修订证据做修复验收，并脱离修复说明通读来源承诺、StoryIntent、EndingContract 与当前可读全稿。`FINAL_COLD_READ` 仅为历史数据与持久化兼容保留，新计划不将它视为独立角色阶段。这些角色共享一条故事状态链，不是由默认 `agent.delegate` 并行调度的独立 Agent。真实子 Agent 只用于可以独立产出和独立验收的调研、资产候选、方案变体或已拆分镜头等工作；首次剧本生成不为了形式上“多 Agent”而把相互依赖的场次拆成 WorkOrder。

当前不建立通用 `ChildJob` 基类或统一子作业状态机。只有未来出现多个确实共享生命周期、恢复与取消语义的领域作业，并且重复已经可观察时，才从 Content Planning Job 和这些同级作业中提取共同合同。

这些状态机只保证作业顺序和恢复，不为不同视频类型各写一套端到端 workflow。项目进入正常制作后，下一步仍由 Agent 的 `PlanRevision` 决定。

## 已实现与待实验

当前代码已实现中文共享角色、文件化 Narrative Skill Catalog、Agent 语义选择、`SCRIPT_LOCKED` 语义结构化与逐字/顺序保护、`StorySourceContract`/`StoryIntent`/`EndingContract` 持久化、显式 `production_skill_role` 分角色 Prompt 组合、`specialist_turns` 单步持久化与中断恢复、Issue Ledger、自然表演时长证据、跨场人物/群体/资源/权限状态继承、无 40/12 低常数场景限制的逐场修订、增强 Verifier 的修复验收与全稿自然冷读，以及 H3 全部合法候选的显式 Agent 选择与 `NEEDS_AGENT_DECISION/PARTIAL` 边界。独立 `FINAL_COLD_READ` 只作历史/持久化兼容。这只证明边界和闭环可执行。

仍需用真实语言模型与不同类型样本观察：Story Orientation 是否选对叙事 Skill，`StoryIntent` 是否真正改善爽感/喜剧/悬念等不同目标体验，Critic 是否减少过度理性化与重复挑错，局部修订是否在保护优点的同时真正改稿。Prompt 和单元测试不构成这些成片质量结论。

## 关于经验

格式 Skill 只保存跨项目稳定成立的指导，不存放某个合同、融资、办公室等具体样片经验。`references/` 保存的也是可复用方法，不是某个项目的样片记录。项目执行中形成的经验由 AgentRuntime 的 `ExperienceMemory` 按项目、Skill 和证据保存，并在相关任务中按需召回。
