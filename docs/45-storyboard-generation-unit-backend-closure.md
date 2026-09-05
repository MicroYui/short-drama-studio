# 从完整分镜到 H3 生成单元的后端闭环

> 状态：本轮后端实现合同
> 更新日期：2026-09-05
> 验证边界：不启用 LTX 模型，不调用真实付费 Provider；自动化回归使用 Fake Provider 和本地媒体

本文定义“完整剧本已存在”之后，Agent 如何形成可修改的完整分镜，将编辑镜头组合为一次 H3 生成，再把同一段生成媒体按原始镜头边界送入时间线。它是普通产品工程中的数据与执行合同，不代替 Agent 的具体创作选择。

## 1. 闭环概览

```text
ScriptVersion + ProjectBibleVersion
  ↓
ProductionSpec（版本化 ProductionDocument）
  ↓
StoryboardDraft（可恢复的内部逐组写作工件）
  ├─ begin(source lock)
  ├─ semantic ledger segments → complete ledger
  ├─ semantic outline segments → complete outline + timing audit
  └─ ordered detailed-group append → semantic verify
       ├─ 镜头组内容问题：replace group 后重新 verify
       └─ ledger/分组边界/时长问题：重新编译全集 draft
  ↓ finalize
Storyboard（版本化 ProductionDocument）
├─ Scene index：单份分镜文档内的场次章节与连续性聚合
  ├─ ShotPlan：编辑、台词和修改的最小边界
  └─ ClipGroup：连续生成的镜头组合
  ↓ 确定性编译
GenerationPlan（版本化 ProductionDocument）
  └─ GenerationUnit + ordered shot source windows
  ↓
H3 RoutePlan → 一个 GenerationTask / H3ProviderRun → 一个 Take
  ↓
Take 按每个 Shot 的 source window 投影为 TimelineClip
```

`ProjectDocument` 仍是唯一项目聚合；三个正式内容文档使用稳定 document ID 和不可变 version 保留历史。`storyboard.draft` 是同一份版本化、可恢复的私有编译工件，依次处于 `LEDGER_BUILDING`、`OUTLINE_BUILDING`、`GROUP_AUTHORING` 三个阶段。增量片段只是模型运行的持久化检查点，不会变成多份用户文档或 ComfyUI 节点。只在 finalize 后生成并绑定唯一的 `storyboard.current`，不是第二套输出真相。`ProjectDocument.shots` 是当前 Storyboard 的兼容投影。

## 2. 三类版本化内容工件

### 2.1 ProductionSpec

`kind=production.spec` 固定本项目已知的制作意图，包括语言、画幅、时长意图、类型和背景、视觉与声音方向、用户约束、Agent 决策和 Provider 偏好。Provider 字段只表示制作偏好，不等于获得远程调用授权。

它为后续 Agent 提供共享决策，而不把某种短剧风格硬编码进通用 Story Architect。格式和创作特化仍由当前装载的 Workflow / Skill 与 Agent 语义判断决定。

### 2.2 Storyboard

`kind=storyboard` 保存完整 `ShotPlan[]` 和 `ClipGroup[]`，并精确引用 Script、Bible 和 ProductionSpec 版本。结构合同保证：

- 始终只有一份 canonical Storyboard；Scene index 只把它按剧本场次组织为章节，不创建多份分镜文档；
- Shot 序号连续，台词行在 Shot 中按原剧本顺序且恰好覆盖一次；
- ClipGroup 按分镜顺序恰好覆盖每个 Shot 一次；
- ClipGroup 时长等于组内 Shot 时长之和，Storyboard 时长等于全部 Shot 时长之和。

Agent 根据动作连续性、镜头转场、Provider 能力与编辑需求自主决定组边界和组内镜头数量。

### 2.2.1 增量写作与 MiniMax Design / ComfyUI 映射

MiniMax Design `drama-series` 的原始 Workflow 明确把每个 episode/scope 的「分镜列表」交付为一个 canvas document node，不按场次拆文件。其内部每个 `clip_group_id` 后续唯一对应一个宫格 work item（如启用）和一个 visual-gen work item。

本地因此使用“私有计划可分段，正式分镜仍原子”的边界：

- Planner 先检查当前 Tool history；若没有当前精确 ScriptVersion 与 ProjectBibleVersion 正文，第一个 Tool 请求是 `project.get`。ArtifactRef、进度摘要和 Leader 转述不等于通读原文。获得精确上下文后，`shot.begin_storyboard_draft` 只写入源 Script/Bible/ProductionSpec 版本和场次身份、顺序、连续性意图。剧本行和场次源字段由服务端确定性回填；begin 不再要求一次模型回答承载全片 JSON；
- 同一 Planner WorkOrder 使用 `shot.append_storyboard_ledger_segment` 按剧情语义自主选择连续范围，逐段持久化 timing ledger。每个对白或可见动作条目记录剧本来源、对白量、语速、最低口播时长、非语言动作预留、实算时长、自然切点与连续性状态。每个场次只有最后一个 timing entry 显式设置 `completes_scene=true`；纯动作场次或对白后仍有可见动作时，由最后的动作条目完成该场。片段边界只是可恢复检查点，不是 Scene、ClipGroup、Shot 或 ComfyUI 节点，不设固定字数、条数、场次数或调用次数；
- 账本仅能覆盖当前剧本的连续前缀。在首次封结前，Planner 必须基于精确 `allocated_duration_seconds` 前瞻确认：每个场次都存在连续、完整、不跨场的分区，每组之和精确为 4–15 秒整数。不可分时只拆分或调整确实可分的动作、反应和停顿预留，不改台词、事件或自然表演总意图，不从 120 秒等目标总时长倒推。不得先封存账本，再在 outline 阶段发现无法装箱；
- 完整覆盖且上述分区可行后，Planner 才调用 `shot.complete_storyboard_ledger` 封结账本并进入 `OUTLINE_BUILDING`。这是确定性阶段迁移，不是用户审批、QA 门或 Critic 循环；
- 账本封结后，同一 Planner WorkOrder 必须通过 `artifact.get_production_document` 读取最新 `storyboard_draft` 精确版本，再基于其完整 ledger 规划 outline；不从进度摘要或 Leader 转述重建账本。完整 outline 的全局核对、详细镜头组创作和 Verifier 审阅也都先解析各自当前的最新精确 draft version；
- Planner 基于完整 ledger 进行全局二次重装箱，用 `shot.append_storyboard_outline_segment` 持久化稳定的大纲前缀。每个最终 ClipGroup 为 4–15 秒整数、不跨场次，内部 Shot 可使用小数剪辑时长；所有剧本来源与 ledger 条目恰好覆盖一次，组时长等于所含 ledger 时长之和；
- 完整 outline 稳定后，Planner 用 `shot.complete_storyboard_outline` 提交全局 timing audit 并进入 `GROUP_AUTHORING`。用户或剧本声明的总时长只用于对比，不反向平均分配到各组；
- begin、ledger 片段、ledger 完成、outline 片段和 outline 完成都由同一 Planner WorkOrder 沿最新 draft version 继续。Tool 结果会回到同一 Specialist，其中的真实 draft document/version ID 可直接作为下一次 Tool 请求的输入；不得只为拿到 begin 生成的 ID 而结束 WorkOrder 或让 Leader 重规划。如果 append-only ledger 在精确分配后仍不可行，这仍是同一编译职责：Planner 在同一 WorkOrder 内重新 `shot.begin_storyboard_draft` 开启新草稿分支，并完整重编译 ledger/outline；不得仅为重启草稿返回 Leader；
- 后续使用 `shot.append_storyboard_group` 严格按 outline 顺序逐组落盘。一次 append 只提交一个 ClipGroup，但同一个语义 WorkOrder 可以连续完成多组，无需为每组重新拆任务。outline 不绑定未来 WorkOrder，实际提交的 group 保留它的 WorkOrder 来源；
- 相邻 ledger 条目与 ClipGroup 使用结构化 `continuity_in / visible_changes / continuity_out` 记录可见事实，不由 Leader 摘要取代精确工件。同场次相邻组的人物、道具、站位、动作和声音需要语义可承接，但不要为字符串相等而复制措辞。跨场次时，上一场的 `continuity_out` 记录退场/结束状态，下一场的 `continuity_in` 记录入场/起始状态，并明确时间或空间转换；两者不应被错误要求为同一文本；
- 全部计划组提交后，一个语义 Verifier 通读当前完整 draft，一次性核对剧本覆盖、节奏、可拍性和时长；对同场次边界判断可见状态与动作是否自然承接，对跨场次边界判断退场、入场与时空转换是否清楚，而不是对 continuity 文本做逐字相等比较。结论用 `shot.record_storyboard_draft_verification` 记录为 `READY` 或 `NEEDS_REVISION`；
- 若只是某组的镜头表达问题，使用 `shot.replace_storyboard_draft_group` 保留原 group ID、来源范围、时长和连续性边界进行局部替换，然后再做一次整体语义核对。若问题来自 ledger、ClipGroup 边界或时长分配，则基于全剧重新编译新 draft，不用局部替换修补错误的整体计划；
- `shot.finalize_storyboard` 确定性装配为一份完整 Storyboard，内部保留 `Scene → ordered ClipGroups → ordered Shots` 索引。

开源 MiniMax Design ComfyUI plugin 只公开了通用 graph node、subgraph、workflow/prompt 快照和视觉 `groups`，没有公开 Scene/Shot/Storyboard 领域模型。后续迁移时，canonical Storyboard 仍是内容真相，图只是可重建的投影：一个 ClipGroup 对应一个可选的宫格工作项、一个视觉生成工作项，并投影成一个 H3 节点或子图。`scene_id` 用于筛选、排版和连续性阅读，Comfy 的 `groups` 仅是排版矩形，不是内容或执行边界。

### 2.3 GenerationPlan

`kind=video.generation-plan` 是 Storyboard 到 Provider 任务之间的版本化编译结果。每个 ClipGroup 确定性编译为一个 GenerationUnit，携带：

- 组内有序 Shot ID 和每个 Shot revision；
- 每个 Shot 在生成媒体内的 `[source_in, source_out)` 时间窗；
- 生成意图、转场意图、入场连续性、可见变化和出场连续性；
- Provider / model / resolution 偏好；
- 稳定的 `video.generation-unit.<clip_group_id>` 输出槽位。

因此“一次生成多个编辑镜头”不再需要把多个 Shot 合并成一个假 Shot。

## 3. 稳定逻辑输出槽位

`ProjectOutputSlot` 表示“这个逻辑目的当前产物是什么”，不存放产物内容。已定义的核心槽位包括：

- `production.spec.current`；
- `storyboard.current`；
- `video.generation-plan.current`；
- `video.generation-unit.<clip_group_id>`。

槽位换绑时增加 revision，旧 `ArtifactRef` 进入 `previous_artifact_refs`；上游变化导致下游不再可用时，当前引用被清空，但历史引用和原文档版本仍然保留。这让 Main Agent、Worker 和 Verifier 可以围绕稳定名称交接，不需要把某一次运行产生的随机 ID 写入 Workflow。
每次发生真实换绑或失效时，还会保留上一个槽位 revision 的不可变快照。因此已持久化 WorkOrder 中的精确 `output_slot@revision` 在槽位继续推进后仍可解析；当前槽位则仍是可变的逻辑指针，不复制实际产物内容。

`ContextManifest` 和 `WorkOrder` 支持 `expected_output_slot_ids`，用于表达任务交接意图，不改变现有 Tool 授权语义。Worker 仍通过精确 `ArtifactRef` 读取实际输入，通过 Tool 产生新版本并换绑相应槽位。

## 4. Agent-facing 创建与修改能力

| 语义 | Tool | 结果 |
| --- | --- | --- |
| 建立/替换/读取制作规格 | `artifact.create_production_spec` / `artifact.replace_production_spec` / `artifact.get_production_spec` | ProductionSpec 新版本及当前槽位 |
| 建立增量分镜草稿 | `shot.begin_storyboard_draft` | 锁定源版本和场次连续性意图，进入 `LEDGER_BUILDING` |
| 持久化/完成时长账本 | `shot.append_storyboard_ledger_segment` / `shot.complete_storyboard_ledger` | 按源顺序追加语义片段；完整覆盖且已前瞻证明每场可分为 4–15 秒整数组后进入 `OUTLINE_BUILDING` |
| 持久化/完成 ClipGroup 大纲 | `shot.append_storyboard_outline_segment` / `shot.complete_storyboard_outline` | 按 ledger 前缀追加稳定分组；全局 timing audit 通过后进入 `GROUP_AUTHORING` |
| 持久化一个生成镜头组 | `shot.append_storyboard_group` | 严格提交下一个已规划的 4–15 秒整数 ClipGroup 及其 Shots，同一 WorkOrder 可连续调用 |
| 替换已提交镜头组 | `shot.replace_storyboard_draft_group` | 保留规划的 group ID、来源、时长和连续性边界，只替换组内详细镜头 |
| 记录完整草稿核对 | `shot.record_storyboard_draft_verification` | 将一个语义 Verifier 对当前精确 draft version 的结论和定位到 group 的 findings 落盘 |
| 装配完整分镜 | `shot.finalize_storyboard` | 唯一 Storyboard、Scene index、Shot 和 ClipGroup refs |
| 导入旧完整分镜 | `shot.create_storyboard` | 兼容旧调用，不是新 Agent 默认写作路径 |
| 结构性替换分镜 | `shot.replace_storyboard` | 新 Storyboard version，旧 version 可追溯 |
| 局部修改分镜 | `shot.patch_storyboard` | 按 `expected_revision` 替换指定 Shot，必要时同步更新 ClipGroup |
| 读取当前或历史分镜 | `shot.get_storyboard` | 指定 document/version 的完整分镜 |
| 编译/读取生成计划 | `route.compile_generation_plan` / `route.get_generation_plan` | GenerationPlan 与 GenerationUnit refs |
| 读取/换绑逻辑输出 | `project.list_output_slots` / `project.get_output_slot` / `project.bind_output_slot` | 当前、历史和 producer 信息 |

ArtifactResolver 能解析 `production_spec`、阶段感知的 `storyboard_draft` 进度索引、`storyboard`、`clip_group`、`generation_plan`、`generation_unit` 和 `output_slot`，包括历史版本中的 Shot。`storyboard_draft` 摘要始终给出 phase、ledger/outline/已提交数量、下一源游标和最新精确 `continuity_out`；在 `OUTLINE_BUILDING` 阶段给出完整 ledger 以便全局重装箱，仅在存在时给出 timing audit。当 Specialist 使用 `project.get` 时，持久化 Tool 结果仍完整，但后续模型历史只回放当前精确 Script（包含场次动作正文）、当前 Bible 和必要的制作引用，不重放全部历史版本、Task、Take 和媒体载荷。`artifact.get_production_document` 的模型历史投影只保留 document 元数据与当前所选 immutable version 的一份精确 payload，删除 `document.versions` 全历史；同一 WorkOrder 多次精确读取时，仅最新读取保留完整 payload，旧读取只保留阶段和源版本标识，避免增量 draft 历史二次膨胀。Agent Runtime 还可按 WorkOrder 读取已持久化的 AgentResult 摘要。

## 5. H3 与时间线语义

H3 Route Planner 同时保留旧的单 Shot 路线和新的 GenerationPlan 路线。GenerationUnit 路线中：

1. Agent 向 RoutePlan 提交 GenerationPlan document/version，可选择其中的 unit；
2. 每个 unit 生成一个 H3 决策，Prompt 以组内绝对时间窗表达每个 Shot 的镜头、动作、台词、情绪和连续性；
3. 一个 unit 只建立一个 GenerationTask / H3ProviderRun，下载后形成一个 Take；
4. Task、Run 和 Take 都保留 Storyboard/GenerationPlan 版本、GenerationUnit、ClipGroup、输出槽位和全部 Shot source windows；
5. Take 被选中后，时间线按 source window 为每个 Shot 建立独立 clip，因而用户仍可按原 Shot 边界裁切、换 Take 或局部重做。

这个建模区分了两种真实边界：GenerationUnit 是 Provider 执行边界，Shot 是叙事和编辑边界。

## 6. 变更、失效和重跑

- 增量草稿中的详细镜头表达可按 ClipGroup 局部替换；替换必须留在原 planned outline 槽位内，不改变 group ID、场次顺序、剧本来源范围、时长或连续性边界。
- 若变更必须改动 timing ledger、ClipGroup 分组边界或时长分配，Agent 创建一份基于全剧重新编译的 draft，保留旧 draft version 供追溯。
- append 或 replace 后，之前的 Verifier 结论自动清空，避免将旧 draft 的语义结论当作当前事实。
- 分镜局部修改产生 Storyboard 新版本，不覆写旧版本；只有语义改变的 Shot 增加 revision。
- 受影响的 Take 和时间线投影失效；未受影响的内容仍可继续使用。
- Storyboard 或其上游版本变化后，已编译的 GenerationPlan 当前槽位失效，Agent 基于新版本重新编译。
- 新任务失败、中断或尚未被选用时，已确认的历史产物仍保留；不用失败候选覆盖当前输出。
- 远程提交结果不明时沿用已有 H3 对账语义，确认原任务事实后再决定是否重跑。

这些只是版本与副作用的最小正确性保障。

## 7. 本轮验收标准

| 验收项 | 可观察结果 |
| --- | --- |
| 模型与迁移兼容 | 旧 ProjectDocument、单 Shot GenerationTask/Take 仍能读取；新字段有兼容默认值 |
| Agent Tool 可发现 | 默认 Registry 可列出 ProductionSpec、Storyboard、GenerationPlan 和 output-slot Tools，权限仍由现有 Agent Runtime 决定 |
| SCRIPT_ONLY 到完整计划 | 同一 Planner WorkOrder 从轻量 begin 开始，按语义自主大小增量落盘 ledger，完整后再增量落盘 outline 和 timing audit；计算时长来自对白与可见动作而非平均分配 |
| 长内容可恢复 | begin、ledger append/complete、outline append/complete 之间每次都产生新 draft version；没有固定片段数或大小，也无需一次模型输出全片计划 JSON |
| 计划到完整分镜 | Agent 按 outline 顺序逐组落盘，同一 WorkOrder 可提交多组；全部 group 完整覆盖剧本行，由 Verifier 语义核对同场承接与跨场时空转换，最后 finalize 为一份 Storyboard |
| 语义核对和修改 | Verifier 对当前完整 draft 记录结论；组内问题可局部替换，ledger、分组边界或时长问题使用全剧重新编译的 draft |
| 局部修改可追溯 | patch 产生新 Storyboard version，更改 Shot revision，旧 ArtifactRef 仍可解析 |
| 生成边界与编辑边界分离 | 一个多 Shot ClipGroup 编译为一个 GenerationUnit，并建立一个 H3 task/take |
| 时间线保留 Shot 边界 | 同一 Take 被多个 TimelineClip 引用，每个 clip 使用对应 source window |
| 失败不破坏已确认产物 | 失败/重跑保留历史 Task、Take 和槽位引用，只有新结果被明确选用后才进入当前时间线 |
| 普通工程检查 | 相关 pytest、Pyright 与 `git diff --check` 通过 |

建议的本地检查顺序：

```bash
cd backend
.venv/bin/pytest -q tests/test_storyboard_contracts.py tests/test_storyboard_incremental_contracts.py tests/test_storyboard_incremental_service.py tests/test_storyboard_agent_interfaces.py tests/test_storyboard_clip_group_invalidation.py tests/test_storyboard_generation_pipeline.py tests/test_h3_generation_unit_prompt.py tests/test_h3_generation_unit_worker.py
cd ..
./scripts/pnpm-project.sh typecheck:py
git diff --check
```

根据实际集成测试再补充相关 H3 worker / timeline 用例；不为达成“流程完整”而增加新的 QA 产品或盲审环节。

## 8. 本轮明确未验证

- 没有使用真实 MiniMax-H3 凭据提交付费生成；
- Fake Provider 可验证请求、状态、工件和时间线交接，不代表已验证真实 H3 的成片质量、耗时或费用；
- 未启用 LTX 模型，也不把 LTX generation / Retake / Extend / IC-LoRA 声称为当前 Agent 能力；
- 未移植 ComfyUI plugin、语义画布或 LTX 编辑器双向桥接；这些仍按 PRD 放在几分钟成片 PoC 之后。
- 本轮对 ComfyUI 的 Scene/ClipGroup/Shot 表达是为未来迁移保留的稳定领域映射，不代表公开插件已提供这些领域节点。
