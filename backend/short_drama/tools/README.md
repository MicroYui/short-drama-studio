# 视频制片 Tool Catalog

本目录是短剧 Agent 可调用能力的统一目录。领域事实和副作用仍由现有 service、repository 与 Provider adapter 负责；每个 Tool 只做三件事：校验类型化输入、调用已有领域能力、返回类型化结果与工件引用。

## Tool 不是固定 workflow

Tool 表示“现在可以执行的一项能力”，不是“所有项目都必须经过的节点”。完整制片方法由共享流程、所选格式 Skill、当前项目工件和用户目标共同提供，Production Manager 再为本轮生成可替换的 `PlanRevision`。

因此 Agent 可以：

- 对一句话需求从剧本和资产开始，对已有成片直接从检查或时间线开始；
- 跳过已经完成且仍有效的阶段，合并轻量步骤，并行处理互不依赖的镜头；
- 在用户只改一句台词时，仅更新受影响的剧本、镜头和时间线分支；
- 根据真实检查结果换 Tool、换输入路线、委派专业 Agent 或重新规划，而不是强行走完预设顺序。

四种视频格式共享同一套 Tool Catalog。格式 Skill 只改变创作重点和角色判断，不复制或预过滤 Tool。`agent.delegate` 仅在 AgentRuntime 注入了 `AgentDelegator` 时注册；基础服务不会假装委派能力已经存在。

## Agent 如何选择 Tool

每次行动前先判断：

1. 当前用户目标是创建、读取、修改、生成、检查还是导出？
2. `ProjectDocument` 中最新有效的剧本、Bible、资产版本、Shot、Take 和时间线分别是什么？
3. 下一步缺的是专业内容判断，还是已有明确输入的确定性执行？前者可委派专业 Agent，后者调用 Tool。
4. 调用会产生 `READ`、`WRITE` 还是 `EXTERNAL` 效果？外部任务状态不确定时应先对账，不重复提交。

Tool 的 Pydantic Schema 是参数真相；Skill 中的建议不能覆盖 Schema、Provider capability 或当前工件状态。`READ`、`WRITE`、`EXTERNAL` 只描述一次调用可能产生的最广泛效果，不是另一套审批流程。

## 按生产阶段选择能力

下表是能力导航，不是必须逐行执行的状态机。

| 生产阶段 | Agent 的选择依据 | 主要 Tool ID | 典型输入与产物 | 失败后的处理 |
| --- | --- | --- | --- | --- |
| 1. 读取目标与项目上下文 | 先确认是新项目、已有项目续作还是局部修改；已有项目优先读取完整聚合，不凭旧对话猜状态 | `project.list`、`project.get`、`project.create` | 输入项目 ID 或创建请求；产出项目聚合、初始 Script/PlanningRun 等权威工件 | 项目不存在时重新发现或澄清目标；创建中断时先读取项目状态，避免重复创建 |
| 2. 内容方案与剧本 | 长内容或从零创作使用可恢复规划；只改选定台词时使用局部剧本 Tool；涉及下游语义时先做影响预览 | `planning.advance`、`script.revise_lines`、`change.preview`、`change.apply` | 输入 PlanningRun、选定台词或语义补丁；产出 ScriptVersion、修订结果和受影响分支 | 内容判断失败时委派编剧/审校或重新规划；参数错误按 Schema 修正；不要为一句话修改重写整部剧本 |
| 3. Bible、角色、场景、道具与风格资产 | 先检索和复用已批准资产，再检查当前镜头真正缺什么；用户已有素材时直接导入版本 | `artifact.create_asset`、`artifact.search_catalog`、`artifact.import_catalog`、`artifact.search_stock`、`artifact.import_stock`、`artifact.import_local_version`、`artifact.import_url_version`、`artifact.inspect_coverage` | 输入资产描述、检索条件或媒体位置；产出 Asset、AssetVersion、候选和覆盖缺口 | 检索为空时再生成或请求素材；导入失败先修正媒体位置/格式；不要把可选三视图或九宫格升级为固定前置 |
| 4. 图像生成、审核与参考包 | 只为当前镜头缺失的身份、空间、构图或状态生成图像；候选需审核后才能进入参考包 | `image.enqueue`、`image.list`、`image.advance`、`image.cancel`、`image.retry`、`image.recover_output`、`artifact.review_version`、`artifact.add_reference_entry` | 输入资产、图像任务请求和审核结论；产出 ImageTask、AssetVersion、审核状态和 ReferencePack 条目 | 外部状态未知时先恢复/查询，不盲目重提；图像内容错误时修图或换版本，不把错误起点交给视频 Prompt 补救 |
| 5. 场次、镜头与连续性计划 | 根据剧本作用、相邻镜头状态和可生成性建立 Shot/ReferencePlan；只需建议时不必直接写入 | `shot.preview_reference_plan`、`shot.update_reference_plan`、`shot.compile_keyframe_brief`、`shot.director_advice` | 输入项目、镜头、参考绑定和动作关系；产出解析预览、ReferencePlan、关键帧 brief 或导演建议 | 镜头粒度、动作或空间关系不成立时回到 ShotPlan/ReferencePlan；不要靠增加随机候选掩盖错误镜头设计 |
| 6. 生成路线与输入检查 | 按镜头风险选择纯文本、首帧、首尾帧或全参考路线；Provider 能力和实际媒体绑定决定可执行组合 | `qa.rerank_reference_visuals`、`qa.compare_keyframes`、`qa.check_input_compatibility`、`qa.list_visual_input_reports`、`route.h3_capability_profile`、`route.plan_h3`、`route.list_h3_plans` | 输入镜头意图、候选参考、关键帧和项目能力证据；产出视觉比较报告、兼容性报告和 H3 RoutePlan | 若输入冲突或全部不可用，修资产、换关键帧、拆镜或换路线；不要把 Provider 易变限制写成格式规则 |
| 7. Prompt 编译与视频任务提交 | 镜头事实和路线已经确定后，才翻译成 Provider 请求；需要检查最终请求时先预览 | REAL_H3：`route.compile_h3_prompt`、`route.preview_h3_probe`、`route.enqueue_h3_probe`、`route.execute_h3_plan`；MOCK：`video.enqueue` | 输入 Shot/ReferencePlan、媒体绑定、时长和候选策略；产出最终 Prompt、Provider 请求、GenerationTask 或 H3 run | 请求不合法时改路线或输入；提交结果不确定时进入对账，不重复 POST；内容事实错误应回上游工件修正 |
| 8. 外部任务推进与恢复 | REAL_H3 用 `task.advance` 单步推进，MOCK 可用有步数上限的 `task.drain`；取消、失败、提交未知和下载中断使用各自恢复动作 | 共用：`task.advance`、`task.cancel`、`task.retry`、`task.recover_output`；MOCK：`task.drain`、`task.reconcile`；H3 专属：`route.list_h3_runs`、`route.list_h3_reconciliation`、`route.resolve_h3_submission`、`route.resolve_h3_not_submitted` | 输入 task/run ID；产出持久化任务状态、Provider 对账结果和 Take | 先区分“明确失败”“明确未提交”“已提交但本地未知”“输出已生成但未落盘”；重试前必须确认原输入仍是当前版本 |
| 9. Take 检查、比较、选择与局部返工 | 先检查技术可读性和镜头职责，再比较同镜候选；相对最好不等于可用，允许全部拒绝 | `video.inspect_take`、`video.compare_takes`、`video.select_take`、`route.review_h3_take`、`shot.director_advice`、`shot.apply_director_repair`、`shot.run_director_loop` | 输入 Take、检查维度或候选集合；产出 inspection、comparison、选择结果、RouteEvidence 和修复建议 | 把问题路由到最小责任层：故事回剧本，构图/动作回镜头，身份/空间回资产，随机执行缺陷才同路线再生成 |
| 10. 局部视频修复 | 只有当前镜头或局部时间段失败时，优先保留合格内容并产生新候选 | MOCK：`video.retake`、`video.extend`、`video.gap_fill`；REAL_H3 回到 H3 路线计划 | 输入项目、镜头和局部返工请求；产出新的 GenerationTask，旧 Take 继续保留 | 局部操作不支持或边界状态不成立时，换路线、拆镜或回到关键帧；不要自动覆盖当前选片 |
| 11. 音频、字幕与时间线 | 有选定 Take 后重建时间线；指定声线时更新角色绑定；先检查音画与字幕关系再导出 | `audio.update_voice_reference`、`timeline.rebuild`、`audio.inspect_manifest` | 输入角色声线版本或项目 ID；产出 VoiceReference、TimelineManifest 和音频检查报告 | 声音来源、对白或字幕时间错误时修对应音频/文本/时间线层；未注册的配音或口型能力必须明确列为缺口 |
| 12. 整集验收与导出 | 从整集承诺、连续性、节奏、音画和交付规格判断是否完成；只有当前时间线通过后导出 | `qa.review_episode`、`timeline.export` | 输入项目、审看请求或导出请求；产出 EpisodeReview、ExportRecord 和成片文件 | 阻断问题通过相应 Tool 建立最小返工分支，修复后重建并复核时间线；可选打磨项不应伪装成失败 |
| 跨阶段：专业委派 | 当前步骤需要编剧、导演、资产审核、路线规划或成片审校的专业判断，且目标与工件范围可以明确时 | 可选 `agent.delegate`（当前基础服务未注册） | 输入角色、有限目标、ArtifactRef、约束和期望输出；产出可跟踪的 delegation/AgentResult | 委派范围过大时缩小 WorkOrder；返回缺少证据时让专业 Agent 读取共享工件，不复制第二份项目状态 |

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
