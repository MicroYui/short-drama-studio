# Agent Tool / Artifact 就绪度核对

> 状态：当前实现事实
> 更新日期：2026-09-04
> 范围：实现核对 `backend/short_drama`，未调用任何付费 Provider；后端回归只使用 Fake Provider 和本地媒体

本文回答两个问题：当前 Agent 实际能调用什么，以及已保存的 MiniMax Design Workflow 需要的工件和执行能力是否真正接通。上游 Markdown 已复制进仓库不等于相应 Tool 已实现。

## 1. 已确认的 Provider 边界

- 不启用、不集成、不分发 LTX 模型权重，不把 LTX 作为本地视频 Provider。
- LTX Desktop 只复用不依赖 LTX 模型的桌面应用、媒体管理、多轨编辑交互、Take 切换和 FFmpeg 导出能力。
- 短剧运行时的 `ProductionExecutionTarget` 目前只有 `MOCK` 和 `REAL_H3`，并不存在 LTX target。因此这一决定不需要下线任何已存在的真实短剧 Provider Tool。
- 真实视频执行使用 `REAL_H3`。未来编辑器中的“Retake / Extend”操作若需要模型生成，必须编译成 H3 镜头重生成或其他已明确接入的 Provider 能力，不得调用 LTX 模型后端。

## 2. 当前 Tool Registry

默认 Registry 实际注册 **87 个 Tool**；仅在显式注入 `AgentDelegator` 时才增加可选的 `agent.delegate`，成为 88 个。当前 Agent Team 使用 `PlanStep(DELEGATION) → WorkOrder`，不依赖该可选 Tool。

| 分类 | 数量 | 主要能力 |
| --- | ---: | --- |
| artifact | 18 | ProductionSpec、通用制片文档、资产、版本、来源、素材库和 ReferencePack |
| route | 15 | GenerationPlan、H3 能力、Prompt、路线计划、执行、对账和 Take 处理 |
| image | 8 | 图像任务生命周期与本地参考板拼接 |
| video | 7 | Take 检查、比较、选择及 Mock 视频操作 |
| shot | 10 | 完整 Storyboard 创建/替换/局部修改、ReferencePlan、关键帧 brief 和 Director 局部处理 |
| task | 6 | 任务推进、取消、重试、恢复和对账 |
| qa | 6 | 资产、关键帧、输入兼容性和整集观察；它们是 Agent 可选诊断，不是必经审批门 |
| audio | 5 | Master Audio、Timed Text、音乐分析、音频检查和声线参考绑定 |
| project | 6 | 创建、读取和列出项目，以及读取和换绑稳定输出槽位 |
| change | 2 | 语义修改预览与局部应用 |
| timeline | 2 | 顺序 TimelineManifest 重建和 FFmpeg 导出 |
| planning | 1 | 可恢复的内容规划单步推进 |
| script | 1 | 已有剧本行的局部修订 |

Tool Effect 分布为 `WRITE=41`、`EXTERNAL=27`、`READ=19`。`EXTERNAL` 表示最大副作用边界，其中也包含本地 FFmpeg 和音乐分析，不等于 27 个远程 Provider 调用。

以下 6 个 Tool 只是确定性 Mock，不是 LTX，也不是真实 H3 能力：

- `video.enqueue`
- `video.retake`
- `video.extend`
- `video.gap_fill`
- `task.drain`
- `task.reconcile`

`REAL_H3` AgentRun 已从 Planner / Worker 的可见 Tool Catalog 中隐藏它们。全局 capability API 仍返回全部 87 个 Tool，因此未来界面显示能力时应按 execution target 投影，避免把 Mock 显示为生产能力。

## 3. 当前 Artifact 真相

### 3.1 内容真相

`ProjectDocument` 当前聚合：

- ScriptVersion 历史、ProjectBibleVersion 历史；
- Asset / AssetVersion、ReferencePack 和 VoiceReference；
- 当前 ShotPlan、ExecutionPlan 和 ReferencePlan；
- TimelineManifest 历史；
- Director repair history；
- 通用版本化 ProductionDocument；
- 受管的 ProductionSpec、Storyboard 和 GenerationPlan ProductionDocument 指针；
- 稳定逻辑 `ProjectOutputSlot`，包含当前引用、历史引用、revision 和 producer 信息；
- Master Audio、Audio Structure Map 和 Timed Text 文档指针。

ProductionDocument 已有稳定身份、不可变版本、父版本与来源 ArtifactRef，适合作为 Workflow 中间文档的通用容器。ProductionSpec、Storyboard、GenerationPlan 和三类受管音频文档已有专用 schema 和消费逻辑；其他自由 `kind/payload` 仍只表示可版本化存储，不代表已存在专用下游编译器。

### 3.2 运行时与媒体记录

SQLite 独立保存 GenerationTask、ImageTask、Take、Export、H3ProviderRun、RouteEvidence、H3RoutePlan、PlanningRun 及各类观察报告。AgentRun、PlanRevision、Action、WorkOrder、AgentResult 和 Memory 也是独立运行时记录，通过 ArtifactRef 指向内容真相，不复制 ProjectDocument。

ArtifactResolver 现在还能解析 `production_spec`、`storyboard`、`clip_group`、`generation_plan`、`generation_unit` 和 `output_slot`，包括历史 Storyboard 版本中的 Shot；运行时可通过精确 ArtifactRef 交接，不需要复制第二份项目状态。

## 4. MiniMax Design 需求与当前能力映射

| 能力 | 状态 | 当前事实 |
| --- | --- | --- |
| Leader / Worker / Verifier、动态计划、上下文隔离、Trace | 已实现 | AgentRun / PlanRevision / WorkOrder / ContextManifest / AgentResult / ProductionBoard 已形成单机可恢复控制面 |
| 剧本、Bible 和分场 | 已实现主链 | `planning.advance` 可逐步生成并持久化；`script.revise_lines` 和 `change.apply` 支持局部修改 |
| Workflow 中间文档 | 存储已实现，语义部分实现 | ProductionDocument 可版本化保存 Brief、制作计划、站位记忆、品牌事实、分镜文档等；但缺少多数专用 schema 和消费逻辑 |
| 结构化角色、场景、道具与多视图 | 已实现 | CoreAssetBrief 包含身份、气质、外观、服装/材质、背景、连续性和视图策略；可生成/导入各视图并拼成 2–9 格参考板 |
| 资产来源、父版本、批准版本和镜头绑定 | 已实现 | AssetVersion / SourceRecord / ReferencePack / ReferencePlan 能保留系谱与精确输入 |
| 稳定 Stage work item 与旧结果保留 | 已实现 | `ProjectOutputSlot` 保存当前与历史 ArtifactRef；`ContextManifest/WorkOrder.expected_output_slot_ids` 表达交付目标 |
| 项目制作锁项 | 已实现基础合同 | 版本化 ProductionSpec 贯通语言、画幅、时长意图、视听方向、用户约束、Agent 决策和 Provider 偏好；更细的特化字段可随实际 Workflow 扩展 |
| 完整分镜写入/替换 | 已实现 | Agent 可创建、整体替换、局部 patch 和读取版本化 Storyboard，并交接 Shot / ClipGroup ArtifactRef |
| 站位记忆和动作连续性 | 已进入生成合同 | ClipGroup/GenerationUnit 携带 continuity-in、visible-changes、continuity-out、转场和参考说明，同时保留现有 Shot 内的 ActionBeat 和空间约束 |
| `clip_group_id → ordered shot_ids` | 已实现 | ClipGroup 是 Storyboard 内的类型化实体，GenerationPlan 编译为带 source windows 的 GenerationUnit |
| 一组可选参考板、一组一段 H3 视频 | 已实现生成单元主链 | H3 RoutePlan、GenerationTask、ProviderRun 和 Take 可绑定 GenerationUnit；一次生成保留组内 Shot source windows；参考板由 Agent 按资产和镜头需求选择，不是每组强制前置 |
| 单 Shot H3 Prompt、有序图/视频/音频参考、生成与 Take | 已实现 | H3 路线计划、媒体绑定、任务对账、Take 检查/比较/选择均已有真实实现 |
| 画幅、分辨率和导出规格贯通 | 未完成 | 项目能保存 `aspect_ratio`，但创建请求未提供该值，H3 部分路径和字幕/导出仍固定 9:16 / 1080×1920 |
| H3 原生对白、环境声和镜头音轨 | 已实现基础路线 | TimelineManifest 可引用 Take 的原生音轨，适合不要求独立后期音轨的短剧 PoC |
| 上传 Master Audio、声线参考和音乐节拍/能量分析 | 已实现 | 能绑定已批准本地音频、锁定 Timed Text 并生成 Audio Structure Map |
| TTS、声线准备、音乐/SFX 生成、提取、trim/fade/stem | 缺失 | 当前 Audio Tool 只做绑定、分析和检查 |
| 多轨音频混合 | 缺失 | 绑定 Master Audio 后是整体替换原生音轨，还不能在保留对白/SFX 的同时混入 BGM |
| 字幕文件和基础烧录 | 部分实现 | 可生成 SRT/ASS 并烧录；没有 ASR、字体资产、安全区、动态大字和通用准确文字 Overlay Tool，当前普通剧本还会自动建立字幕 cue |
| 基础顺序拼接和 FFmpeg 导出 | 已实现 | 能做规格统一、源区间裁切、concat、字幕与技术检查 |
| 转场、叠层画面、Logo/CTA/文字、多轨、可编辑 EditorialTimeline | 缺失或仅有字段 | TimelineManifest 是自动顺序编排；transition 字段尚未被 exporter 消费，也无 timeline patch Tool |
| WorkflowArtifact / ComfyUI 执行图和语义画布 | 未实现 | 按产品里程碑后置；ProductionBoard 只是当前后端派生进度投影 |

## 5. Workflow Markdown 的实际可达性

`drama-series`、`ad-tvc` 和 `mv` 适配器已能按 Agent 职责装载其显式列出的精确章节。但 Loader 不会因为一篇上游文档链接了另一篇 Markdown，就自动递归读取。

因此，已保存的 `_shared/stage-execution-plan.md`、`asset-pipeline.md`、`audio-pipeline.md`、`subtitle-pipeline.md` 和 `video-merge.md` 目前没有全部进入三个方法适配器的角色 Prompt。广告方法同时漏载部分产品调研与音频策略章节。后续应显式选择当前角色真正需要的共享章节，不需要把整个目录塞进每次 Prompt。

## 6. PoC 就绪度结论

### 6.1 当前能跑的最小链路

不启用 LTX 模型不会阻塞现有 H3 PoC。当前可以在以下边界内运行：

- `execution_target=REAL_H3`；
- 9:16 为已真正贯通的主规格；
- Storyboard 中的一个 ClipGroup 对应一个 GenerationUnit 和一段 H3 生成媒体，同时保留组内 Shot 编辑边界；旧的单 Shot 路线仍兼容；
- 使用 H3 原生对白/环境声，或使用一条上传的 Master Audio；
- 不承诺独立 BGM/TTS/SFX 生成和多轨混音；
- 使用基础顺序拼接、现有字幕行为和 FFmpeg 导出。

本轮只用 Fake Provider 验证上述状态与工件交接，没有提交真实付费 H3 生成；因此不声称已验证真实成片质量、耗时和费用。

### 6.2 后续 PoC 工作的优先顺序

1. 用 Agent 独立跑通 ProductionSpec → Storyboard → GenerationPlan → H3 Route → Take → Timeline，保留可读 Trace；
2. 基于一个几分钟项目验证核心资产、引用图、声音与 ClipGroup 的实际组合；
3. 在用户明确授权后再进行受控的真实 H3 成片验证；
4. PoC 跑通后再接入 ComfyUI、语义画布和 LTX 编辑器，同时按产品需要补 TTS/音乐/SFX、多轨混音和 EditorialTimeline。

详细后端合同和验收项见 [从完整分镜到 H3 生成单元的后端闭环](45-storyboard-generation-unit-backend-closure.md)。这些是普通产品工程的能力验证顺序，不新增门禁、固定次数或额外 Critic 链路。
