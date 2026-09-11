# 视频制片 Tool Catalog

本目录是短剧 Agent 可调用能力的统一目录。领域事实和副作用仍由现有 service、repository 与 Provider adapter 负责；每个 Tool 只做三件事：校验类型化输入、调用已有领域能力、返回类型化结果与工件引用。

## Tool 不是固定 workflow

Tool 表示“现在可以执行的一项能力”，不是“所有项目都必须经过的节点”。完整制片方法由共享流程、所选格式 Skill、当前项目工件和用户目标共同提供，Production Manager 再为本轮生成可替换的 `PlanRevision`。

因此 Agent 可以：

- 对一句话需求从剧本和资产开始，对已有成片直接从检查或时间线开始；
- 跳过已经完成且仍有效的阶段，合并轻量步骤，并行处理互不依赖的镜头；
- 在用户只改一句台词时，仅更新受影响的剧本、镜头和时间线分支；
- 根据真实检查结果换 Tool、换输入路线、委派专业 Agent 或重新规划，而不是强行走完预设顺序。

四种视频格式共享同一套 Tool Catalog。格式 Skill 只改变创作重点和角色判断，不复制或预过滤 Tool。`agent.delegate` 仅在 AgentRuntime 注入了 `AgentDelegator` 时注册；基础服务不会假装委派能力已经存在。`agent.get_tool_attempt` 是内建的只读控制面能力，只能读取 Leader 已显式放入当前 WorkOrder 的 `ToolAttemptRef`。

## Agent 如何选择 Tool

每次行动前先判断：

1. 当前用户目标是创建、读取、修改、生成、检查还是导出？
2. `ProjectDocument` 中最新有效的剧本、Bible、ProductionSpec、Storyboard、AssetPlan、GenerationPlan、资产版本、Take 和时间线分别是什么？对于跨 Agent 交付，先解析稳定 output slot，再读取其精确 ArtifactRef。
3. 下一步缺的是专业内容判断，还是已有明确输入的确定性执行？前者可委派专业 Agent，后者调用 Tool。
4. 调用会产生 `READ`、`WRITE` 还是 `EXTERNAL` 效果？外部任务状态不确定时应先对账，不重复提交。

Tool 的 Pydantic Schema 是参数真相；Skill 中的建议不能覆盖 Schema、Provider capability 或当前工件状态。`READ`、`WRITE`、`EXTERNAL` 只描述一次调用可能产生的最广泛效果，不是另一套审批流程。

## 按生产阶段选择能力

下表是能力导航，不是必须逐行执行的状态机。

| 生产阶段 | Agent 的选择依据 | 主要 Tool ID | 典型输入与产物 | 失败后的处理 |
| --- | --- | --- | --- | --- |
| 1. 读取目标与项目上下文 | 先确认是新项目、已有项目续作还是局部修改；已有项目按本次工作范围读取当前内容和相关逻辑输出，不凭旧对话猜状态 | `project.list`、`project.get`、`project.create`、`project.list_output_slots`、`project.get_output_slot`、`project.bind_output_slot` | 输入项目 ID、创建请求或 slot ID；产出唯一 ProjectDocument，以及逻辑输出的当前/历史 ArtifactRef | 项目不存在时重新发现或澄清目标；换绑前解析实际工件；创建中断时先读取项目状态，避免重复创建 |
| 2. 内容方案、剧本与制作规格 | 长内容或从零创作使用可恢复规划；已有完整剧本收到创意修改时启动同项目修订任务，由 Leader 结合原始要求、当前剧本和新意见语义决定修少数场、逐场修全稿或重构故事；只改已明确选定的台词时使用局部剧本 Tool | `planning.start_script_revision`、`planning.advance`、`script.revise_lines`、`change.preview`、`change.apply`、`artifact.create_production_spec`、`artifact.replace_production_spec`、`artifact.get_production_spec` | 输入完整用户修改意见、当前 ScriptVersion、Content Planning Job 游标、选定台词或语义补丁；产出新 ScriptVersion、ProductionSpec 版本和受影响分支 | 修订启动中断时先对账项目中已持久化的修订 Job，不重复创建；不要为一句话修改重写整部剧本；ProductionSpec 的 Provider 偏好不等于允许调用远程 Provider |
| 3. 完整分镜、生成组与连续性 | 根据剧本作用、相邻镜头状态和可生成性建立 Storyboard；Shot 保留编辑边界，Agent 自主判断哪些连续 Shot 组成 ClipGroup | `shot.create_storyboard`、`shot.replace_storyboard`、`shot.patch_storyboard`、`shot.get_storyboard`、`shot.preview_reference_plan`、`shot.update_reference_plan`、`shot.compile_keyframe_brief`、`shot.director_advice` | 输入完整分镜或指定 Shot revision 的局部替换；产出版本化 Storyboard、Shot/ClipGroup refs、ReferencePlan 和连续性决策 | 结构性重拆使用 replace，只改指定镜头使用 patch；不靠增加随机候选掩盖错误镜头设计 |
| 4. AssetPlan 与资产取舍 | Planner 用一次去重 Context 读取当前 Script/Bible/ProductionSpec/Storyboard；已有计划时 Leader 先读轻量目录按稳定 ID 分派，Builder/Verifier 再读取任务切片 | `artifact.get_asset_planning_context`、`artifact.create_asset_plan`、`artifact.retry_asset_plan_attempt`、`artifact.get_asset_plan_index`、`artifact.get_asset_work_context`、`artifact.reconcile_asset_plan_execution_contract`、`artifact.patch_asset_plan`、`artifact.get_asset_plan`、`artifact.replace_asset_plan` | 规划 Context 按需携带已存在的上游正文各一份，source intake 仅带当前选用的精确版本，已有资产和 AssetPlan 仅带轻量索引；任务切片使用互斥 `scope`：单个执行簇传 `WORK_ITEM + work_item_ids`，整个资产项传 `ITEM + item_ids`，再按需携带相关 Shot/ClipGroup；跨项 canonical/lineage/混合实体与文字载体迁移提交语义意图，由协调 Tool 编译联动字段；纯 TEXT_PLATE 的独立 SCREEN_CONTENT_PLATE 已经分层，不创建假载体；任意 work 的尺寸/输出布局改用 `execution_overrides`，COMPOSE intent 自动同步 | 局部变更由服务端合并后执行完整语义校验；旧式顶层 selector 或混合 scope 会被拒绝；协调 `notes` 不代表结构化修改，成功摘要只回报实际落盘值；未落盘的失败候选按尝试引用局部重试；只有全局盘点、顺序或依赖网络重构才另外读取和替换完整计划；计划不调用 Provider |
| 5. 资产建档、图像执行、语义检查与参考包 | 结合已有 AssetPlan 和当前缺项推进；源图由 Agent 声明用途并按需观察，随后读当前图像能力、复用或导入，只为确实缺失的身份、空间、构图或状态生成图像；精确文字改用本地图版；根据实际问题观察候选并选择可用结果 | `qa.analyze_source_image`、`image.capability`、`artifact.create_asset`、`artifact.update_brief`、`artifact.search_catalog`、`artifact.import_catalog`、`artifact.search_stock`、`artifact.import_stock`、`artifact.import_local_version`、`artifact.import_url_version`、`artifact.inspect_coverage`、`image.enqueue`、`image.list`、`image.advance`、`image.cancel`、`image.retry`、`image.resolve_not_submitted`、`image.recover_output`、`image.compose_reference_sheet`、`image.compose_text_plate`、`qa.inspect_asset_version`、`artifact.review_version`、`artifact.add_reference_entry` | 输入精确 source AssetVersion、结构化 assignment/brief、显式 `layout_profile`、最终 prompt 和带语义职责的有序参考，或与 `required_visible_text` 完整一致的文字图版请求；失败任务返回结构化 `retry_disposition`；产出源图覆盖建议、Asset、AssetVersion、ImageTask、检查结论与 ReferencePack 条目 | 源图分析不替 Agent 路由或审批；`image.capability` 只读本地配置与适配器约束，不校验远程密钥；`artifact.create_asset` 只建档不生图；`image.compose_text_plate` 不调用 Provider；`review_version` 只写决策；拼板只组合已有视图且输出仍为 DRAFT；只有 `RETRY_SAME_REQUEST` 允许 `image.retry`，语义失败修订后新建 `image.enqueue`，外部状态未知时先对账 |
| 6. 生成计划、路线与输入检查 | 先将 ClipGroup 确定性编译为带 Shot source windows 的 GenerationUnit，再按该单元的风险选择纯文本、关键帧或全参考路线 | `route.compile_generation_plan`、`route.get_generation_plan`、`qa.rerank_reference_visuals`、`qa.compare_keyframes`、`qa.check_input_compatibility`、`route.h3_capability_profile`、`route.plan_h3`、`route.list_h3_plans`、`route.update_h3_asset_preparation` | 输入当前 Storyboard/ProductionSpec 版本、候选参考和关键帧；产出 GenerationPlan/GenerationUnit refs、视觉比较报告、H3 RoutePlan 与可恢复的媒体准备状态 | 上游版本变化时重新编译；缺关键帧时由图像 Worker 产出可用 AssetVersion，按实际问题检查；输入冲突时修资产、换关键帧、拆组或换路线 |
| 7. Prompt 编译与视频任务提交 | 镜头事实和路线已经确定后，才翻译成 Provider 请求；单 Shot 和多 Shot GenerationUnit 均由同一 H3 路线层消费 | REAL_H3：`route.compile_h3_prompt`、`route.preview_h3_probe`、`route.enqueue_h3_probe`、`route.execute_h3_plan`；MOCK：`video.enqueue` | 输入 Shot/ReferencePlan 或 GenerationUnit、媒体绑定、时长和 Agent 本次明确决定的候选数；产出最终 Prompt、Provider 请求、GenerationTask 或 H3 run，并保留组内 Shot source windows | 请求不合法时改路线或输入；提交结果不确定时进入对账，不重复 POST；内容事实错误应回上游工件修正 |
| 8. 外部任务推进与恢复 | REAL_H3 用 `task.advance` 单步推进，MOCK 可用有步数上限的 `task.drain`；取消、失败、提交未知和下载中断使用各自恢复动作 | 共用：`task.advance`、`task.cancel`、`task.retry`、`task.recover_output`；MOCK：`task.drain`、`task.reconcile`；H3 专属：`route.list_h3_runs`、`route.list_h3_reconciliation`、`route.resolve_h3_submission`、`route.resolve_h3_not_submitted` | 输入 task/run ID；产出持久化任务状态、Provider 对账结果和 Take | 先区分“明确失败”“明确未提交”“已提交但本地未知”“输出已生成但未落盘”；重试前必须确认原输入仍是当前版本 |
| 9. Take 检查、比较、选择与局部返工 | 先检查技术可读性和镜头职责，再比较同镜候选；相对最好不等于可用，允许全部拒绝 | `video.inspect_take`、`video.compare_takes`、`video.select_take`、`route.review_h3_take`、`shot.director_advice`、`shot.apply_director_repair`、`shot.run_director_loop` | 输入 Take、检查维度或候选集合；产出 inspection、comparison、选择结果、RouteEvidence 和修复建议 | 把问题路由到最小责任层：故事回剧本，构图/动作回镜头，身份/空间回资产，随机执行缺陷才同路线再生成 |
| 10. 局部视频修复 | 只有当前镜头或局部时间段失败时，优先保留合格内容并产生新候选 | MOCK：`video.retake`、`video.extend`、`video.gap_fill`；REAL_H3 回到 H3 路线计划 | 输入项目、镜头和局部返工请求；产出新的 GenerationTask，旧 Take 继续保留 | 局部操作不支持或边界状态不成立时，换路线、拆镜或回到关键帧；不要自动覆盖当前选片 |
| 11. 音频、字幕与时间线 | 对话型项目可使用 Take 原生音轨；音频驱动项目可由 Agent 选择绑定 Master Audio、分析其结构、锁定精确文字，再重建时间线 | `audio.update_voice_reference`、`audio.bind_master`、`audio.analyze_music`、`audio.update_timed_text_lock`、`audio.align_subtitles`、`timeline.rebuild`、`audio.inspect_manifest` | 输入当前选用音频版本、音频窗口、精确时间文字或已保存的 Studio 剪辑；产出版本化 MasterAudioBinding、AudioStructureMap、TimedTextLock、TimelineManifest 和音频检查报告 | 分析维度不可用时保留可验证基础元数据并明确留空，不伪造节拍或段落；声音来源、对白或字幕时间错误时修对应音频/文本/时间线层 |
| 12. 整集验收与导出 | 从整集承诺、连续性、节奏、音画和交付规格判断是否完成；Agent 根据用户目标和实际结果决定必要修正或直接导出 | `qa.review_episode`、`timeline.export` | 输入项目、审看请求或导出请求；产出 EpisodeReview、ExportRecord 和成片文件 | 实际问题通过相应 Tool 局部修复并检查，保留当前可用结果；可选打磨项不应伪装成失败 |
| 跨阶段：Method 结构化工件 | 品牌事实、文案锁、教学结构、参考拆解、音乐结构等需要跨 Agent/阶段复用，但尚不需要独立领域表时 | `artifact.create_production_document`、`artifact.revise_production_document`、`artifact.get_production_document`、`artifact.list_production_documents` | 输入 kind、title、结构化 payload、精确来源 ArtifactRef 和来源角色；产出稳定 document ID 与不可变版本 | 修订必须基于当前 base version；引用或结构错误时修正工件，不把自由文本白板当作第二份项目真相 |
| 跨阶段：稳定输出交接 | Main Agent 希望 Worker 交付某个逻辑结果，且该结果会继续修订或重生时 | `project.list_output_slots`、`project.get_output_slot`、`project.bind_output_slot` | WorkOrder 使用 `expected_output_slot_ids` 表达交付意图；Tool 将当前 ArtifactRef 绑到 ProjectOutputSlot，并保留旧引用 | 上游变化时使当前绑定失效但不删除历史产物；工件不可解析或已过期时先修正工件 |
| 跨阶段：失败候选交接 | 上一个 Worker 的 Tool 参数中已有完整候选，但因合同或领域校验失败，新 Worker 需要定点修复时 | `artifact.retry_asset_plan_attempt`、`agent.get_tool_attempt` | AssetPlan 修复默认只传小型 `ToolAttemptRef` 和按稳定 item/work-item ID 定位的 RFC 6902 补丁，运行时恢复并重试原候选；只有错误摘要不足时，Worker 才按任务读取 `ARGUMENTS`、`RESULT` 或 `BOTH` | 不把大型失败 payload 注入每轮 prompt，不根据摘要重建；引用不属于本 AgentRun 或未显式交给当前 WorkOrder 时拒绝使用 |
| 跨阶段：专业委派 | 当前步骤需要编剧、导演、资产审核、路线规划或成片审校的专业判断，且目标与工件范围可以明确时 | 可选 `agent.delegate`（当前基础服务未注册） | 输入角色、有限目标、ArtifactRef、约束和期望输出；产出可跟踪的 delegation/AgentResult | 委派范围过大时缩小 WorkOrder；返回缺少证据时让专业 Agent 读取共享工件，不复制第二份项目状态 |

原生 Take 音轨使用 `audio.align_subtitles` 时，传入当前精确 `manifest_id`。工具合并同一来源的连续音频窗口，以本地 ASR attention token 时间戳对齐现有剧本字幕，只创建字幕时序修正后的新 TimelineManifest，不改变台词正文、视频或音轨窗口。观察报告保留 ASR 原文、字符时间与未匹配警告，并创建 `NATIVE_SUBTITLE_ALIGNMENT` ProductionDocument；交接给另一 Worker 时携带返回的精确 document/version refs，以 `artifact.get_production_document` 读取全部逐cue调整、ASR和媒体不变量，不依赖截断摘要或本地文件路径。该报告不等于声纹或口型检查。之后检查返回的新 manifest，并在 `timeline.export` 的 request 中传入该 `manifest_id`。Agent Tool v2 只渲染已有当前版本，`rebuild_manifest` 仅接受 `false`；重建由 `timeline.rebuild` 单独执行，避免导出覆盖字幕编辑。现有 REST 的显式重建能力保留。Master Audio 继续使用 `audio.update_timed_text_lock` 的独立时间文字合同。

## 核心资产的 Agent 调用语义

MiniMax-H3 公开内容提供的是角色卡、场景卡和 H3 参考语义，不是一套可直接替换的核心资产 Tool 源码。当前后端因此保持四层：

1. Asset Worker 在首次规划时通读当前精确 Script/Bible/ProductionSpec/Storyboard，基于复用、连续性风险、现有素材、已选 Method 和下游职责，将 anchor/text-only/reuse 取舍写入版本化 AssetPlan；后续按语义任务读取精确切片，局部完成回写使用 patch。
2. `asset.plan.current` 将稳定 work item ID、最终 prompt、有序 AssetVersion refs、真实依赖与期望输出交给执行 Worker；状态、换装、伤势、昼夜和关键道具从已批准 base/master 派生。
3. 通用 Image Tool 执行生成/编辑/拼板，`qa.inspect_asset_version` 观察实际图片，`artifact.review_version` 记录 Agent 决策。母版审核使用 `CANONICAL_MASTER`；正脸、侧面、服饰或空间局部等额外图使用 `SUPPLEMENTAL_REFERENCE`，不替换当前母版。从 canonical 派生的 ReferencePack 条目显式记录 `lineage_root_version_id`；独立来源可不属于该系谱。
4. ReferencePack 可保留同一语义资产的多个有效视图/状态；单次 Provider 调用只选择当前 Shot/ClipGroup 的最少充分子集，并用 purpose/alias 说明每张图的职责。

同一角色或场景的多视图可在各自检查后拼成一份紧凑参考卡，供需要整体身份锁的下游使用；也可按已选 Method 直接生成指定卡片布局。拼板不是必经路径。若已传递拼板，不再无差别重复绑定全部源图；只在某张单图另外承担精确角度、状态或构图职责时保留它。

角色/场景/风格参考用于定义身份和环境时，应作为 H3 Subject/reference media；只有某张图本身确实定义镜头开场、结束或构图时，才把它用作首帧、尾帧或关键帧。不把角色卡自动当首帧，也不把真正的开场构图只当普通角色参考。

## Tool Result 与通用故障处理

所有调用都经统一 `ToolResult` 返回：

- `SUCCEEDED`：使用 `output` 和 `artifact_refs` 更新下一轮上下文；成功不等于整个用户目标已经完成。
- `INVALID_ARGUMENTS`：对照 Tool 的 `input_schema` 修正参数，不通过自然语言猜字段。
- `NOT_FOUND`：重新读取项目或任务聚合，确认 ID、版本和项目范围；不要把其他项目的同名工件顶替进来。
- `FAILED`：读取错误类型和领域状态，判断应修输入、换能力、对账、重试还是重新规划。

对于图像、视频和其他外部任务，网络错误或本地中断不能自动推导为“远端未提交”。Mock 任务用 `task.reconcile`，H3 用专属对账 Tool，已完成但未落盘的任务按原 Provider 使用 `task.recover_output`，恢复事实后再决定是否重试。

## 目录与扩展约定

- Tool ID 使用稳定的 `<category>.<action>` 格式，与视频类型无关。
- 输入输出是严格 Pydantic 模型；Provider client、锁、repository 和私有 helper 保持在 Tool 后面。
- `backend/short_drama/tools/defaults.py` 负责把现有领域能力注册进默认 Catalog。
- 新的格式中立能力可以在 Registry 注入 `ShortDramaService` 前注册 `ToolSpec`，也可以在服务构建后注册到 `service.tool_registry`。
- 新能力应保持单一、可组合和可观察；不要在一个 Tool 内重新实现完整制片流程，也不要让 Tool Registry 依赖某个格式 Skill。

当前分类为：`project`、`planning`、`artifact`、`script`、`shot`、`image`、`video`、`audio`、`timeline`、`qa`、`task`、`route`、`change` 和可选的 `agent`。

当前 `audio.inspect_manifest` 优先读取已保存的 Studio 剪辑，按当前裁切、原声、字幕、变速、倒放、静音与音量检查；报告标明所用剪辑修订，不重建或覆盖内容。仅没有已保存的 Studio 剪辑时，兼容读取旧 TimelineManifest。旧名称保留 API 兼容，不构成剧本更新后的制作门槛。
