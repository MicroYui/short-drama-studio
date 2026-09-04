# 通用端到端视频制片流程

## 这份流程是什么

这是 Production Manager 在制定与修订计划时共享的制片方法，适用于通用视频、AI 短剧、数字人口播和动漫。它说明每个阶段要解决什么问题、应读取哪些权威工件、可以调用哪些 Tool、产出什么，以及何时应返工。

它不是固定节点图。Agent 应根据用户目标、现有工件、已确认决定和实际检查结果跳过、合并、重排或并行步骤。用户只改一句台词时，不应从项目创建重新开始；用户已经交付完整剧本和批准资产时，也不应为了“走流程”重新生成它们。

运行时的基本循环是：

```text
用户目标与当前 ProjectDocument
  → ContextPack（只装配当前任务需要的事实、决定、经验和工件引用）
  → PlanRevision（本轮可替换的计划）
  → Tool Call / WorkOrder / User Input
  → 新工件与检查结果
  → 根据证据继续、局部返工或重新规划
```

`ProjectDocument` 及其 ScriptVersion、Asset/AssetVersion、ShotPlan、ReferencePlan、GenerationTask、Take、TimelineManifest 和 ExportRecord 是项目内容与制作结果的业务真相。`AgentRun` 是总调度，只记录当前目标、计划、进度和下一步，不复制第二份项目内容。

首次剧本规划在概念上是 AgentRun 拥有的 **Content Planning Job**。当前代码和持久化模型仍使用兼容名称 `PlanningRun`；它只保存大纲、逐场写作、审校，以及目标需要时的导演规划所需的恢复游标与中间工件，通过 `planning.advance` 一次推进一个单元。只要系统负责原创或扩写且配置了语言 Provider，所属 AgentRun（包括 `AUTO_DRAFT` 和 `GUIDED`）就始终使用这个作业，不以 60、90 或 300 秒等时长阈值决定是否一次性生成。不带 AgentRun owner 的直接兼容创建仅建立本地结构骨架，不调用模型生成整稿。`SCRIPT_ONLY` 目标在内容审校完成后直接写入可完整阅读的 ScriptVersion，并以 `SCRIPT_READY` 作为本轮 AgentRun 的终点；`SCRIPT_AND_SHOTS` 才继续逐场导演并形成分镜。它不是另一份 `ProjectDocument`，也不自行决定资产、生成、检查或时间线等后续制片流程。当前只有这一种内容规划子作业，不提前抽象通用 `ChildJob`。场次数与跨场修订范围也由 Agent 根据内容决定；原先 40 场、跨场 12 场这类低常数创作上限已移除，长任务通过逐场、逐修订单元持久化推进，而不是把整个任务塞进一次模型调用。

内容与制作链将四类边界分开保存：Story Orientation Agent 在进入 Story Architect 前形成前两项，Story Architect 在蓝图中形成 `EndingContract`，后续 Bible 维护项目 Style。

- `StorySourceContract`：用户明确承诺、明确禁止、交给 Agent 的开放选择与后续权威覆盖；
- `StoryIntent`：目标观众体验、戏剧引擎、推进策略、调性/可信度、受保护元素和偏移风险；它是版本化的创作方向，不是评分表或门禁；
- `EndingContract`：本集要兑现的回报、必须闭合/可保留的线索、最后画面/台词意图与续集策略；
- `ProjectBible.style`：视觉、摄影、材质、色彩和声音等项目表达，不代替 `StoryIntent` 的叙事目标。

人物、地点、道具和台词归属分别以稳定 `character_id`、`location_id`、`prop_id`、`speaker_character_id` 贯穿蓝图、逐场写作、修订和文档投影；名称只用于人类阅读，不作为身份连接键。媒体参考使用结构化 `kind`、`target_id`、`purpose` 与 `required` 表达，是否必需不从自由文本中的词语推断。

当来源模式为 `SCRIPT_LOCKED` 时，普通文本中的说话人、旁白/内心音类型与锁定台词范围由 Story Orientation Agent 依据完整语义整理到 `StorySourceContract.locked_dialogue`；调用方也可以直接提交结构化锁定台词。确定性代码只检查每句文本逐字存在、顺序未变，以及调用方已结构化内容未被改写，不用正则、冒号格式或说话人关键词推断语义。

`ProductionProfile` 只选择 AI 短剧、通用视频、数字人或动漫这类宽粒度媒介/生产方法，不把爽剧、喜剧、悬疑等题材写成代码枚举。Story Orientation Agent 查看文件化 Narrative Skill Catalog 的紧凑说明和支持角色，根据整段项目语义选择零个或多个 Skill；Loader 只检查 ID 与角色支持，不做关键词映射、正则路由、题材分类或必选数量。角色 Prompt 依次组合共享中文职责、宽粒度格式 Skill、Agent 选中的 Narrative Skills、`StoryIntent`、相关经验与当前任务数据；共享 Story Architect 本身不特化成“爽剧架构师”或“悬疑架构师”。在专业委派中，`assigned_agent` 只标识实际接收任务的 Agent 身份；Production Manager 必须基于完整任务语义显式填写 `production_skill_role`，运行时只按这个角色字段装载职责 Prompt，不从 Agent 名称或用户文本做关键词路由。

代码中的正则只可承担 Markdown/JSON 协议边界、SQLite 全文检索、资产搜索或媒体元数据读取等技术解析；这些检索结果仍由 Agent 阅读和选择，不能用匹配结果替代题材、Skill、修订、候选或 Provider 路线判断。

内容作业采用轻量、可恢复的收敛链：Story Architect 根据 `StorySourceContract`、`StoryIntent` 和所选 Narrative Skills，保存只包含核心承诺、因果主线、场次契约和 `EndingContract` 的故事蓝图，再逐场写作并在每场完成时持久化；全场到齐后先检查场序、缺失字段、时长偏差和用户约束的对齐情况，再让 `SCRIPT_CRITIC` 对当前完整稿做一次全稿诊断。Critic 为问题保存稳定 ID、语义分类、状态、文本证据、修复证据、演变说明、已接受取舍、责任层和场次范围，并基于完整剧本显式设定 `requires_revision`。这些形成可继承的 Critic Issue Ledger，避免后续换个说法重新打开同一问题。问题严重度只是影响说明，不是“某一级别自动改稿”的代码门槛。Production Manager 只把非故事问题作为下游 handoff，不再串行调用另一个空 Review Router Agent，也不叠加基于严重度、问题数或关键词的代码判定器。Critic 完成后进入 Repair Planner；Repair Planner 根据 `StorySourceContract`、`StoryIntent` 和 Issue Ledger 重新诊断，决定哪些问题需要实际改稿，或记录为无需改动的已接受取舍。需要修改时，以 issue ID 和相关场次为上下文，即使需要全场调整也仍按 `SCENE_REVISION 1/N…N/N` 持久化，不用一次整集重写覆盖中间进展。完成这一修订批次后始终交给增强 `REVISION_VERIFIER`；不重新跑另一轮完整 Critic。

场次的目标秒数与总和是规划分配，不是实际文本能自然演完的证明。Writer 和修订 Writer 对当前成稿输出自然表演时长证据；Critic 和增强 Verifier 再按对白、动作、停顿、反应和观众读清信息的实际顺序判断。只有成稿明确写成同时发生、蒙太奇或可连续掠过时才按并行处理；不以字数、语速公式、场次数或固定偏差代替 Agent 的语义判断。

逐场写作与审校还要继承前文已建立的人物关系、群体态度、资源与权限。换地点、换见证者、换道具，或把口头认可改成文件、挂牌，不会自动把同一次震惊、让位、授予或交接变成新升级；后续场应产生新的选择、代价或后果。

增强 `REVISION_VERIFIER` 同时承担两个视角：先对照 Issue Ledger、Repair Plan 和修订证据，检查原问题是否真正解决、受保护的回报是否仍在、是否引入新回归；再脱离修复说明通读用户简报、`StoryIntent`、`EndingContract` 和人类可读的完整剧本，不用大纲或“已修复”声明替实际观感辩护。这次自然冷读同时检查本集回报、最后一场、最后一句台词、最终画面、自然表演时长和跨场状态是否成立。Verifier 直接决定接受当前稿，或带具体 issue ID、场次范围和理由继续聚焦修订；聚焦修订完成后再回到增强 Verifier，不重跑完整 Critic，也不设固定循环次数。默认链路不再进入独立 `FINAL_COLD_READ`；该类型只作历史数据与持久化兼容，不作为现行角色阶段排入新计划。Production Manager 执行 Verifier 的语义决定，不用固定严重度、问题数、关键词或循环次数代替它。

`EndingContract` 显式保存本集必须兑现的最终状态、结尾画面/台词意图和续集策略，不是要求 Writer 把每个 closure 分别演成一次认可、授予、核验或交接仪式。多个 closure 若只在证明同一次人物状态变化，应由最有力的决定性动作统一兑现。Story Architect 直接阅读用户原始需求与项目上下文，通过语义理解判断用户是明确要求单篇完结、明确要求系列延续，还是把选择交给 Agent；不使用关键词正则或枚举短语替代这个创作判断。单篇完结时完成主要因果并不留续集钩子；系列延续时可在本集回报已完成后留下有前文根据的下一步动力；用户交由 Agent 判断时，Story Architect 根据题材、本集形态和当前承诺自主决定并记录理由，不必为此默认追问用户。三种情况都不允许用未完成的台词、无依据新人物或突然黑屏代替本集回报。

AgentRun 的一次 advance HTTP 最多成功执行一个 `planning.advance`。它可以在返回前完成当前计划的收敛与自动重规划，以暴露下一个 ready step 或终态，但不会继续执行新计划中的任何动作。因此场次、审校或修订单元一完成，调用方就能刷新 trace 和项目状态；单个长步骤执行中也应通过已持久化的 `RUNNING` Action 和观察心跳更新 trace。心跳间隔只决定多久刷新一次，不是生成超时或剧本时限；`max_actions` 不能把多个内容单元合并成一次不可见的长请求。内容作业不设固定总超时或创意修订轮次上限，也不用重试次数、错误指纹或预设时间阈值代替 Agent 对当前进展和下一步的判断。`FAILED` 或 `NEEDS_ATTENTION` 状态本身不会生成用户确认门禁；遇到重复失败时，将错误、当前工件和已尝试路线暴露给 Agent，由它决定继续、改变输入/路线或在真正缺少用户决定时请求协助。

## 可复用的视频生成范式

高密度多模态 Prompt 案例的价值不在于复制其风格，而在于分离稳定事实、时间段和模型输入。本流程吸收以下通用方法：

- 先定义并批准核心主体，再设计运动、镜头和环境；人物身份、造型、服装和其他连续性锚点不应在视频 Prompt 中临时改写。
- 把参数、主体定义、媒体职责和按播放顺序的时间段分开；每段说清镜头、动作、台词/声音、情绪或表演变化，以及所用媒体。
- 让每个切点或镜内节拍承担可观察的作用，例如建立、揭示、反应、决定、冲击或收束；画面、文字和声音可交替主导，但不强求每层始终高强度。
- 用景别、焦点、运镜、遮挡、图形匹配、动作或声音连续组织镜头转换；转场方式服务当前信息和情绪，不是必填特效清单。
- 最终 Provider Prompt 只引用已绑定的实际媒体，并说明每份媒体要保留什么；资产名称或 `@文件名` 可作为创作标记，但必须在提交前解析为具体 AssetVersion 和 Provider 媒体标签。

不应从单个样例泛化的内容包括：固定 15 秒、固定 13 切、固定 AAA 游戏预告质感、固定配色、每段都使用字卡，或全程硬切、甩镜和速度渐变。这些都是特定成片的风格选择，不是 H3 或 AI 视频的必要合同。

这些范式只在资产、分镜/导演和 Provider 层使用。角色四视图或轻量群像资产、场景俯视/全景/功能区 coverage、按时间线拆分的镜头/动作/台词/情绪，以及提交 H3 前将创作中的 `@` alias 解析为真实 AssetVersion 和 Provider 媒体标签，都不属于 Story Architect 的故事 Prompt。Story Architect 只需给出后续层可读的人物、场景、行动、因果和表演意图，不为某个 Provider 预编译资产语法。

## 每次规划前先回答的六个问题

1. 用户这次真正要改变或完成什么，哪些内容明确不能动？
2. 当前项目处于什么状态，最新且已批准的剧本、Bible、资产、分镜、Take 和时间线分别是哪一版？
3. 缺的是创作判断、可执行工件、Provider 结果，还是对已有结果的检查？
4. 哪些下游确实依赖本次变化，哪些已完成内容可以原样复用？
5. 下一步需要的是 Tool 的确定性能力，还是专业 Agent 的内容判断？
6. 完成这一步后，什么可观察结果足以决定继续、停止或换路线？

优先使用 `project.get` 读取完整当前聚合；局部修改先用 `change.preview` 得到受影响范围，再决定是否 `change.apply`。不要根据旧对话或 recent actions 猜测项目现状。

## 不同输入从哪里进入

- 一句话或主题：从目标澄清、内容方案和剧本开始，再建立资产与镜头。
- 已有剧本：先确认锁定范围、结构完整性和可制作性；只修补必要部分，不把原作改写成另一部作品。
- 已有角色、场景或参考媒体：先导入为有版本的资产并说明用途，再让镜头引用它们。
- 已有 ProjectDocument：从用户本次目标和最新工件继续，不重建项目。
- 局部修改：先做语义影响分析，只更新受影响分支，并保留无关镜头、Take 和时间线片段。
- 已有可用视频：可以直接进入检查、选片、时间线或局部修复，不要求重新生成。

## 阶段一：理解目标、来源与可修改范围

### 要解决的问题

把模糊需求转成可规划的生产目标，同时保留用户提供的事实、题材、人物、台词、媒介与交付约束。这里要区分“用户已经决定的内容”“Agent 可以提案的内容”和“当前还不知道的内容”。

### 读取与判断

- 读取目标受众、观看场景、成片用途、语言、目标时长、画幅、输出规格和可用 Provider。
- 识别来源模式：原创构思、锁定剧本、已有素材或现有项目修改。
- 标出锁定事实、允许改写的范围、用户偏好以及真正会改变结果的开放问题。
- 将用户原始输入编译为 `StorySourceContract`，再形成可讨论、可修订的 `StoryIntent`；不用隐含评分项代替创作对齐。
- `SCRIPT_LOCKED` 普通文本由 Story Orientation Agent 语义整理为 `StorySourceContract.locked_dialogue`；若调用方已提交结构化条目则原样继承。后续代码只验证逐字与顺序，不猜说话人或旁白类型。
- 让 Story Orientation Agent 根据整段语义选择零个或多个 Narrative Skills，并保存选择理由；不为此建立关键词路由表。
- 判断是否有必要询问用户。能从项目工件、Tool 能力或合理默认值获得的信息，不要反复追问。

### 可用能力

- `project.list`、`project.get`：发现并读取已有项目。
- `project.create`：为新目标创建权威项目；系统负责原创或扩写且配置了语言 Provider 时，始终在当前 AgentRun 内建立 Content Planning Job（兼容实现名 `PlanningRun`），不按时长分流。
- `planning.advance`：只在当前 AgentRun 拥有未完成 Content Planning Job 时推进一个可恢复单元。
- `agent.delegate` 或 DELEGATION PlanStep：把明确的创作判断交给概念策划、编剧或其他专业角色。

### 产物与完成信号

- 清楚的生产目标、来源边界、锁定项、交付规格和当前开放问题。
- 新项目已经创建，或已明确要继续修改的项目与工件版本。
- 下一步可以直接产生内容方案或执行局部变更，不再依赖未说明的关键假设。

## 阶段二：形成内容方案与可制作剧本

### 要解决的问题

让内容不仅“有一个故事或文案”，而且能在目标时长和媒介中被表演、被拍成镜头并被观众理解。格式 Skill 决定该视频类型在结构、节奏、对白和观看体验上的重点。

### 读取与判断

- 对原创内容建立主题、观看承诺、人物欲望/信息目标、主要变化和结尾回报。
- 对已有剧本检查场景目的、因果、对白可演性、信息重复、时长承载和来源忠实度。
- 先做轻量全局蓝图、场次契约和 `EndingContract`，再逐场写作与持久化；不要让后写场次遗忘前文状态，也不因短于某个时长阈值而改回一次性整稿生成。
- 蓝图的场次秒数只是分配。Writer 对每场实际成稿输出自然表演时长证据，Critic 和增强 Verifier 按实际顺序复核；除非成稿明确是同时发生、蒙太奇或可连续掠过，不假设未写出的快切，也不用字数、语速或场次数阈值代替 Agent 判断。
- 每场都以前文已成立的人物关系、群体态度、资源和权限为入场状态；换地点、见证者、道具或交接形式不自动等于升级，后续场需要真正改变选择、代价或后果。
- Story Architect 根据用户原始需求与 `StorySourceContract` 语义判断是否要求单篇完结、系列延续或交由 Agent 决定；不用关键词正则作为创作分类器，且所有情况都先完成本集承诺。
- `EndingContract` 描述本篇要到达的最终状态，不是仪式清单；多个 closure 若只是反复证明同一次状态变化，应由最有力的决定性动作统一兑现。
- 默认审校链是一次完整 Critic，接着由 Repair Planner 诊断并按需逐场修订，然后始终进入增强 Verifier。Verifier 既验收 Issue 修复和回归，也脱离修复说明对全稿做自然冷读，直接决定接受或带具体证据继续聚焦修订。聚焦修订后回到 Verifier，不重跑完整 Critic，不设固定修订次数、问题数或严重度门禁，也不再安排独立 Final Cold Read。
- 默认先形成一份可完整阅读和批准的 ScriptVersion，再生成全量分镜；探索性预演、局部修改或用户明确要求可只规划受影响片段。
- 已经批准的内容只在有明确问题或用户指令时修改。

### 可用能力

- `planning.advance`：推进 Content Planning Job 中可恢复的大纲、分场写作、审校、修订和最终写入；若目标只要求剧本，写入 ScriptVersion 后即停止，不能继续生成分镜或资产；作业完成后由 AgentRun 重新决定下一步。
- `script.revise_lines`：只修改选定对白行并形成新 ScriptVersion。
- `change.preview`、`change.apply`：处理会影响人物、场景、镜头或下游媒体的语义修改。
- 内部角色阶段：Story Orientation 负责保存来源承诺、创作意图与 Narrative Skill 选择，Story Architect 负责轻量蓝图与承诺，Writer 负责逐场写作，Critic 负责一次完整全稿诊断和 Issue Ledger，Repair/修订 Writer 负责聚焦逐场修订，增强 Verifier 同时负责修复验收和脱离修复说明的全稿自然冷读。`FINAL_COLD_READ` 只作历史/持久化兼容，不是现行角色阶段。这些是一个 Content Planning Job 内的可恢复阶段，不是默认并行的真实子 Agent。

### 产物与完成信号

- 当前 ScriptVersion 包含可完整阅读的 `screenplay_text`，并能明确映射到场次、台词、可见行动和目标时长。
- 主要人物动机、信息与因果没有依赖未写出的设定才能成立。
- 内容问题与制作问题已分开；镜位、资产或 Provider 问题没有被错误地塞回对白中解决。

## 阶段三：准备制作资产，建立 Bible、资产清单与连续性锚点

### 要解决的问题

把跨镜头需要保持的内容从散落 Prompt 中提取为可复用工件。Bible 说明角色、场景、道具和风格是什么；AssetVersion 保存实际媒体版本；ReferencePack 说明哪些批准版本可以怎样被引用。

### 读取与判断

- 从剧本提取角色的叙事身份、人设、气质/行为基调、外观与服装，场景的叙事用途、氛围、空间布局、光线与色彩，道具外观与状态变化，以及整体媒介和风格。
- 为核心资产建立结构化 brief：区分叙事事实、稳定视觉锚点、服装/材质、背景与质感约束、连续性锚点、允许变化项以及当前覆盖策略。不要只保留一串形容词。
- 区分跨项目/跨镜复用的 canonical asset 与只服务一个镜头的关键帧。
- 根据角色/场景的剧情重要度、预计复用范围、跨镜连续性风险、空间/动作复杂度和已有素材覆盖，由 Agent 选择轻量单图、标准多视图、场景 coverage 或镜头专用关键帧。
- 反复出现且外观必须稳定的主角可使用正面、侧面、背面、服饰/细节等标准视图，并把显式选定且相互一致的源图确定性组成参考板；次要群像、一次性路人或远景人物可使用轻量覆盖。
- 长时间复用的场景可准备俯视/布局图、全景 master 和反复出现的功能区局部，例如“食堂全景”与“打饭窗口”；单镜幕布或抽象背景无需强行建立真实空间套图。
- 优先检索和复用已批准版本；不存在合适版本时才生成或导入新版本。

### 可用能力

- `artifact.create_asset`：创建角色、场景、道具或风格资产。
- `artifact.search_catalog`、`artifact.import_catalog`：检索并导入已批准的复用资产。
- `artifact.search_stock`、`artifact.import_stock`：在配置的素材源中查找并导入候选。
- `artifact.import_local_version`、`artifact.import_url_version`：把用户文件或远程媒体作为 AssetVersion 导入。
- `artifact.update_brief`：为已有核心资产保存结构化身份、气质/材质、连续性锚点、覆盖策略和所需视图。
- `artifact.inspect_coverage`：检查当前项目真正缺少的参考视图。
- `image.enqueue` 与图像任务生命周期 Tools：生成或编辑需要的图像版本。
- `image.compose_reference_sheet`：把 2–9 份显式源 AssetVersion 确定性排成标准多视图或场景 coverage 参考板；可要求仅使用已批准输入，也可为迭代比较显式选择非拒绝版本。它不生成新人物/场景，输出仍为 DRAFT，并保留每个格子的源 AssetVersion 和视图标记。
- `artifact.review_version`、`artifact.add_reference_entry`：记录版本判断并加入参考包。

### 产物与完成信号

- ProjectBibleVersion 与当前剧本一致，关键人物、场景、道具和风格有明确描述。
- 核心资产的 brief 同时说清“这是谁/什么”、“叙事中如何给人感受”、“哪些可见特征要保持”与“为什么需要当前覆盖”。
- 本轮会用到的媒体有可追溯的 AssetVersion，已批准版本与普通候选被区分。
- 每份参考的职责清楚：身份、造型、空间、道具状态、声音、构图或运动，不是“把所有图都塞进去”。

## 阶段四：设计场次与分镜，明确镜头和连续性状态

### 要解决的问题

把剧本转换为可执行的视听单元。分镜不是一次性中间文本，而是连接故事、资产、Provider 输入、候选 Take 和时间线的控制层。

默认在完整 ScriptVersion 可审阅后，先按时间线把场次分成连续片段，再逐段决定是一个长镜、镜内节拍还是多镜覆盖。时间段总长应覆盖目标时长，不重叠、不留无意空洞，也不为追求固定切数拆镜。

`ShotPlan.timeline_beats` 可选地保存一个连续 editorial shot 内部的时间阶段。非空时，各 beat 从 0 开始、连续覆盖该 ShotPlan 的完整时长，并按原顺序恰好分配本镜对白；每段可保存画面、景别、摄影机、动作、表演/情绪、声音和 `ShotReferenceCue`。简单镜头或旧文档可以保持空列表，继续使用镜头级概要。Beat 不是隐藏切镜：出现显式切镜、独立生成或独立剪辑边界时，仍拆成相邻 ShotPlan。

### 每个 ShotPlan 至少要说清

- 本镜头在故事或信息上的作用，以及相对上一镜发生了什么变化。
- 可见人物、动作、表情、道具和环境状态。
- 景别、构图、机位、摄影机运动和竖屏/横屏可读性。
- 对白原文、说话人与声音意图，谁在画内说话、谁是画外音。
- 情绪/表演起点、变化和落点，并用目光、动作、姿势、语气或停顿表达，不只写抽象形容词。
- 如需镜内分段，`timeline_beats` 只描述同一连续镜头中的阶段；每段的 `ShotReferenceCue` 用 `subject_asset_id`、不带 `@` 的语义 alias、所需 tags 和 purpose 声明媒体意图。
- 开场状态、结束状态、人物位置、视线、银幕方向和道具状态。
- 媒体绑定意图：哪个角色、场景、功能区、道具、首/尾帧或声音资产在本段可见/可听，各自保留什么。
- 进入下一镜的转场关系，以及本镜是否适合拆分、合并或局部延展。

### 可用能力

- 专业 Agent：镜头规划负责把内容转成 ShotPlan；导演负责表演、调度、构图和覆盖；内容审校只处理故事层问题。
- `shot.update_reference_plan`：为一个镜头更新参考绑定、动作节拍与物理交互关系。
- `shot.preview_reference_plan`：在不写入项目的情况下查看解析后的引用。
- `shot.compile_keyframe_brief`：把拓扑、起始状态或终止状态编译为可生成的关键帧说明。
- `shot.director_advice`：获得当前格式的镜头级导演判断。

### 产物与完成信号

- ShotPlan 的总时长能承载剧本，且每镜的存在有明确作用。
- 相邻镜头的开场/终态可以对接；复杂动作和接触关系已经显式化。
- 镜头粒度服务于可生成性和剪辑，而不是机械追求镜头数量。

## 阶段五：选择生成路线并组装参考包

### 要解决的问题

根据镜头的主要风险选择最小充分输入，而不是默认把文本、首帧、尾帧、角色图、视频和音频全部加入。格式 Skill 说明创作重点，Provider Skill 负责翻译当前模型语法，Route Tool 根据当前能力和工件选择可执行路线。

### 路线判断原则

- 纯文本路线：适合探索、低连续性风险或没有成熟参考的镜头。
- 首帧路线：开场身份、构图或动作起点重要，终态允许模型自然发展。
- 首尾帧路线：开场和结束状态都必须锁定，并且中间存在可描述的连续路径。
- 全参考路线：人物、场景、声音或运动特征的复用比精确边界更重要。
- 已有 Take 复用：当前版本、镜头意图和检查结果仍匹配时，优先复用而不是重新生成。
- Provider 不支持的组合应换路线、先合成关键帧或拆镜，不通过反复改写同一 Prompt 碰运气。
- Route Tool 将当前镜头技术上合法的所有候选及其结构化证据交给 Route Agent，不用本地 top-k、候选序号、QA 或评分先截断创作选择。Agent 显式返回候选 ID、参考媒体 ID 和本批生成意图；一次可选一个或多个，不设固定数量。

### 可用能力

- `qa.rerank_reference_visuals`：按当前镜头意图重排候选参考。
- `qa.compare_keyframes`：比较关键帧候选，并允许“都不可用”。
- `qa.check_input_compatibility`、`qa.list_visual_input_reports`：检查输入间的身份、风格、空间或状态冲突。
- `route.h3_capability_profile`：读取当前项目积累的 H3 能力证据。
- `route.plan_h3`、`route.list_h3_plans`：生成并查看镜头级 H3 路线计划。
- 生成路线规划 Agent：解释选择理由、缺失输入和检查重点；不得把 Provider 参数编造成格式规则。

### 产物与完成信号

- 每个待生成镜头都有路线、输入职责、候选策略、检查重点和必要的回退说明。
- 所有引用都指向具体且当前可用的 AssetVersion、关键帧或视频/音频工件。`ShotReferenceCue.alias` 与人可读的 `@标记` 是语义层，只有名称、alias 或 Bible 描述仍不算已绑定媒体。
- 路线与当前 Provider 能力一致；易变的文件数量、时长、分辨率和组合限制来自 Tool Schema/Capability，而不是格式 Skill。

## 阶段六：准备并检查生成输入

### 要解决的问题

在付出视频生成成本之前，让静态身份、空间、构图、动作起点/终点和媒体绑定达到可执行状态。这里的目标是减少明显输入冲突，不是追求“所有资产都完美”才允许继续。

### 执行方法

- 用 `shot.compile_keyframe_brief` 形成镜头专用首帧、尾帧或拓扑说明。
- 用 `image.enqueue` 生成或编辑必要图像；通过 `image.advance`、`image.retry`、`image.recover_output` 或通用 `task.*` 推进真实任务状态。
- 用 `artifact.review_version` 记录选择理由；被拒绝的版本保留为历史，不进入当前绑定。
- 用 `qa.rerank_reference_visuals`、`qa.compare_keyframes` 和 `qa.check_input_compatibility` 检查身份、服装、场景、道具状态、构图、比例和输入间冲突。
- 若关键帧本身错误，先修图或更换资产；不要指望视频 Prompt 自动修复一个错误起点。

### 产物与完成信号

- 路线要求的媒体已经存在、可读取、用途明确，且与镜头描述不冲突。
- 关键帧和参考包足以表达当前镜头最重要的约束。
- 可选美化项不会阻止 Agent 继续；只有真实缺失或不兼容输入才需要返工。

## 阶段七：编译 Provider Prompt 并生成候选

### 要解决的问题

把 provider-neutral 的 ShotPlan、ReferencePlan 和已批准媒体翻译成当前模型的合法请求，并把外部生成过程保存为可恢复任务。

### 执行方法

- Provider Prompt Compiler 只翻译既定镜头：保留锁定对白、人物、动作顺序、情绪/表演变化、时长、画幅和媒体绑定，不在最后一刻重写剧情或增加镜头。
- H3 参考模式中，创作稿的 `@食堂全景` 或 `@男主` 可来自 `ShotReferenceCue.alias`；Route 必须先把 cue 解析到已批准的实际 AssetVersion/媒体，并将语义 alias 与职责写入 `H3MediaInput.reference_alias` / `binding_purpose`。alias 方便人和 Agent 理解，不是文件路径或上传标识；真实上传绑定仍由 `H3MediaInput.source`、AssetVersion、媒体输入顺序及由顺序得到的 `<Picture N>`、`<Video N>`、`<Audio N>` 决定。Prompt 不得引用请求中没有发送的媒体，也不得用未批准候选静默替换。
- 若锁定分镜的媒体绑定不完整、版本已失效或职责互相冲突，先返回上游修复；不通过删掉引用或改写人物来生成一条“能提交”的 Prompt。
- REAL_H3 目标可先用 `route.compile_h3_prompt` 或 `route.preview_h3_probe` 查看最终 Prompt/请求，再由 `route.execute_h3_plan` 建立真实 MiniMax-H3 任务，并用 `task.advance` 逐步推进。
- MOCK 目标使用 `video.enqueue` 建立确定性本地任务，并用 `task.drain` 批量推进；`task.reconcile` 也只处理 Mock 任务。`task.retry` 和 `task.recover_output` 则必须跟随原任务的 Provider。
- H3 专属运行可通过 `route.list_h3_runs`、`route.list_h3_reconciliation` 和相应 resolve Tool 对账。
- Route Agent 根据当前镜头、已有结果与实际证据，从全部合法候选中显式选择候选 ID，并决定本批生成一个或多个、复用 Take、改变输入或换路线；不以候选序号、QA 状态、分数、失败类别或固定数量自动停止或放行。同一 Route Plan 重复执行只对账并保持幂等；如果 Agent 根据新证据决定继续探索，建立新的 Route Plan 和显式候选批次。若没有语言 Route Agent 且调用方没有显式指定 `candidate_id` 或复用 Take，计划保持 `NEEDS_AGENT_DECISION/PARTIAL`，技术基线只供展示，不提交、不计费。

### 产物与完成信号

- 每次提交都能追溯到输入 ScriptVersion、BibleVersion、Shot revision、最终 Prompt 和媒体绑定。
- 成功任务形成 Take；失败或取消任务保留可解释状态，不被当成无事发生。
- Trace 能说明 Agent 选择了哪个实际候选进入检查/时间线，或为什么当前证据支持修改路线；代码不另设候选数量或 QA 通过门槛。

## 阶段八：检查、比较并局部修复

### 要解决的问题

判断生成结果能否完成这个镜头在成片中的职责，并定位问题属于故事、分镜、资产、输入、生成、音频还是剪辑层。不要只给一个模糊总分，也不要从“相对最好”推导出“已经可用”。

### 检查层次

- 技术层：文件可读、时长、尺寸、帧率、音轨和下载完整性。
- 镜内层：身份/造型、场景/道具、动作路径、表情、构图、摄影机、对白与声音来源。
- 镜间层：人物位置、视线、银幕方向、持物、光线、动作终态和声音是否能与相邻镜头衔接。
- 候选层：在相同镜头意图下比较各 Take 的真实取舍，并允许没有可接受候选。
- 整体层：单镜虽好看但不服务剧情、节奏或时间线时，仍可能不适用。

### 可用能力

- `video.inspect_take`：抽帧并记录技术与语义观察。
- `video.compare_takes`：相对比较同镜候选并可应用选择。
- `video.select_take`：显式选择进入当前时间线的 Take。
- `route.review_h3_take`：记录 H3 路线的类型化质量判断。
- `shot.director_advice`、`shot.apply_director_repair`、`shot.run_director_loop`：把问题路由到导演修复策略。
- `video.retake`、`video.extend`、`video.gap_fill`：为 MOCK 目标只重做当前镜头、延展片段或补齐局部缺口；REAL_H3 应回到 H3 路线计划创建新候选。

### 修复选择

- 故事目的、因果或对白有误：回到内容或剧本层。
- 构图、覆盖、镜头粒度或动作设计有误：修改 ShotPlan/ReferencePlan 或拆分镜头。
- 身份、空间、道具状态或起止姿态有误：更换/修复资产或关键帧。
- 运动执行、表演或随机缺陷：同路线再生成，或根据证据换路线。
- 只有局部时间段失败：优先 retake、extend、gap fill 或时间线替换，不重做整集。
- 剪辑、字幕或混音能解决的问题：保留合格画面，在后期阶段修复。

### 产物与完成信号

- 每个选定 Take 都有可追溯检查或明确人工选择理由。
- 问题被交给责任最小且正确的一层，未受影响工件继续复用。
- Agent 能说明为何继续生成、为何停止，或为何需要重新规划。

## 阶段九：处理音频、字幕与时间线

### 要解决的问题

把合格镜头组装成可重建的成片结构，并检查对白、环境音、音乐、字幕、图形与画面节奏之间的关系。原生音频存在不代表对白一定正确，也不代表无需字幕与混音。

### 执行方法

- 根据选定 Take 构建镜头顺序、入出点、转场和必要的局部裁切。
- 核对画内对白、画外音、录音设备播放、环境声和音乐的真实来源，避免“声音内容正确但来源错”。
- 数字人或指定声线场景可用 `audio.update_voice_reference` 更新角色绑定；是否需要独立配音或口型路径取决于已接入能力，不能假装存在未注册 Tool。
- 使用 `timeline.rebuild` 从当前选定 Takes 重建 TimelineManifest。
- 使用 `audio.inspect_manifest` 检查时长、响度、静音、对白覆盖和字幕时间关系。
- 字幕应忠实于锁定文本、易读且不遮挡关键表演；具体排版由格式 Skill 补充。

### 产物与完成信号

- TimelineManifest 精确引用当前 ScriptVersion、BibleVersion 和选定 Takes，可重复构建。
- 音画、字幕与转场在相邻边界上成立，时长与交付规格一致。
- 未接入的后期能力被明确列为缺口，不通过 Prompt 或文档伪装成已完成。

## 阶段十：完成成片验收与导出

### 要解决的问题

从整集而不是孤立镜头判断作品是否兑现用户目标。验收聚焦真实可见/可听问题和交付完整性，不把所有可选打磨项升级成失败。

### 可用能力

- `qa.review_episode`：基于当前时间线进行整集内容、连续性、音画和交付审看。
- 成片审核 Agent：解释承诺、节奏、表演、镜间关系、音频、字幕和格式问题，并给出责任层。
- `timeline.export`：从当前 TimelineManifest 渲染导出工件。
- 发现问题后通过 `change.preview` 或相应镜头/资产/视频 Tool 建立最小返工计划。

### 产物与完成信号

- 成片在内容、表演、连续性、音画、字幕、画幅、编码和用户目标上没有已知阻断问题。
- ExportRecord 对应当前最新 TimelineManifest；旧版本仍可追溯，但不会被误报为当前结果。
- 剩余可选优化与真正未完成项被清楚区分，Agent 的 `goal_complete` 只在整个用户目标满足时设为 true。

## Multi-Agent 交接方式

真实子 Agent 只用于可以独立产出和独立验收的工作，例如并行调研、互不依赖的资产候选、方案变体或已经拆分清楚的镜头任务。Story Orientation、故事蓝图、逐场编剧、一次完整 Critic、Repair/逐场修订与增强 Verifier 共享同一条因果与状态链，作为 Content Planning Job 内的角色提示阶段执行，不为看起来“多 Agent”而拆成并行子任务。

专业 Agent 不应收到整段无界对话。主 Agent 发送的 WorkOrder 至少包含：

- 当前目标以及为什么现在需要这个角色；
- 精确的项目与 ArtifactRef 范围；
- 不能改变的事实和已确认决定；
- 当前开放问题；
- 期望输出结构和可检查的完成条件。

WorkOrder 中接收者身份与专业职责分开表达：`assigned_agent`/接收者只说明任务交给谁，`production_skill_role` 才决定装载概念策划、编剧、导演、审校等哪一组职责。该角色由 Production Manager 依据完整目标显式选择，不能从名字中推断。

专业 Agent 需要 Tool 时也不在一次外部 `execute` 中隐藏循环：一次调用只完成一次模型决策，或者执行一个此前已经持久化的 Tool 请求。请求参数、理由、状态与结果按顺序写入 `WorkOrder.specialist_turns`，下一次调用再把这段历史交给专业 Agent 决定继续调用 Tool 还是返回 AgentResult。进程重启后，尚处于 `IN_PROGRESS` 的 turn 标记为 `INTERRUPTED`，WorkOrder 回到 `NEEDS_ATTENTION`；Agent 查看规范项目状态和中断证据后决定对账、重试或换路线，Harness 不自动重放。这个边界用于暴露进度与正确恢复，不是 Tool 次数上限。

专业 Agent 返回的 AgentResult 至少说明：

- 一段简洁结论；
- 新建或更新了哪些工件；
- 关键发现、决定与假设；
- 仍未解决的问题；
- 支持结论的工件引用和建议下一步。

信息必须足以让主 Agent 继续规划，但不要重复整个 ProjectDocument。若专业 Agent 需要更多事实，应先通过共享 Tool 读取相关工件，再返回结果。

## 已实现与待真实样本验证

当前代码已实现 Story Orientation、`SCRIPT_LOCKED` 语义结构化与逐字/顺序保护、`StorySourceContract`/`StoryIntent`/`EndingContract` 持久化、Narrative Skill 语义选择与分角色编译、显式 `production_skill_role` 委派、`specialist_turns` 单步持久化与中断恢复、Issue Ledger、定向逐场修订、增强 Verifier 和 `planning.advance` 可恢复游标；默认新链路不再进入独立 `FINAL_COLD_READ`，该类型只作历史/持久化兼容。场景及跨场修订已不受 40/12 这类低常数创作上限约束。资产结构化 brief、参考板组合、分镜 timeline beats 与 H3 媒体 alias 解析也已有对应工件/工具边界。这些是工程能力，不是成片质量证明。

仍需真实语言模型、图像和 H3 样本检验：Narrative Skill 是否选择得当，爽感/喜剧/悬念等不同 `StoryIntent` 是否真正进入剧本，Critic 是否减少重复挑错与过度现实主义，局部修订是否保护原有优点，以及标准角色/场景参考与真实媒体绑定是否提高连贯性。文档、Prompt 和结构化测试只证明可执行性。

## 何时重新规划

出现以下情况时，应产生新的 PlanRevision，而不是硬着头皮执行旧计划：

- 用户改变目标、锁定范围、内容类型或交付规格；
- 最新工件版本使旧计划引用失效；
- Provider 能力或任务状态与原假设不同；
- 检查结果证明问题责任层判断错误；
- 候选已经满足目标，可以提前停止后续生成；
- 需要新资产、拆镜、换路线或新增专业 Agent 才能继续；
- 当前计划步骤已经完成，但整个用户目标尚未完成。

重新规划只替换下一步工作假设，不覆盖历史工件。若问题反复出现，把当前错误、工件、已尝试方法和可替代路线一起交给 Agent 判断；它可以继续、改变输入/路线或请求用户协助，不通过固定重试次数、指纹或时间阈值自动决定停机。

## 用户局部修改时的默认处理

```text
读取完整当前项目
  → 理解用户修改的语义对象
  → change.preview 得到受影响分支
  → 用户或 Agent 确认修改意图无歧义
  → change.apply 创建新版本并只失效相关下游
  → 只为受影响资产/镜头重新规划
  → 复用无关 Take
  → 重建时间线并做整集边界复核
```

这条局部路径和从零制片使用同一组 Tools。区别只在当前目标、已有工件和计划范围，不需要另一套“修改工作流”。
