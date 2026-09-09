# Phase 30：核心资产计划与多参考闭环

> 状态：调研结论、后端合同与离线回归已落地；真实 AgentRun 语义规划已通过 `PASSED_PLAN_ONLY` 验证。当前改动已补齐 Azure GPT Image 2 的文生图、多参考首版生成、派生编辑、确定性文字板、源图语义分配和实际图后审看合同；经授权的真实 Agent 媒体实跑正在验证，尚不得写成已通过完整媒体闭环。本阶段不接入 LTX 模型，不移植 ComfyUI 前端。

## 1. 结论

当前后端的 `Asset` / `AssetVersion` / `CoreAssetBrief` / `ReferencePack` / `ReferencePlan` / Image Task 原子能力不需推倒重写。本轮已在其上补齐三个成熟制片语义：

1. 在最终剧本与分镜之后，先产生一份版本化 `AssetPlan`，显式记录哪些对象要建锚点、哪些保留为文本、哪些复用已有媒体，以及每个生成工作项的依赖、最终 prompt、有序参考和下游用途。
2. 将“语义资产可以拥有多张有效参考”与“一次 Provider 调用只选最少足够的子集”分开。参考板是人工审阅或单图 Provider 的可选降级形态，不是多视图资产的唯一机器表达。
3. 让独立 ASSET_PLANNER 依据当前项目的 Method Skill 和完整分镜语义自主选择轻量图、短剧角色参考卡、六视图卡、场景 coverage、道具 master 或不建卡，再由任务切片化的 ASSET_BUILDER 执行、ASSET_VERIFIER 观察实际媒体。这些形态由结构化 brief/plan 显式声明，不由题材关键词、正则或固定镜头数路由。

## 2. 成熟实现中直接采用的部分

### MiniMax Design 短剧 Workflow

本地保存的 MiniMax Design 3.0.11 `drama-series` Workflow 已经给出可直接迁移的资产方法：

- 只为跨镜复用、身份/空间/状态连续性确实需要的对象建锚点；一次性路人、群像或普通背景物可保留为 text-only。
- 短剧持续角色默认为一张 16:9 双区图：左侧 50% 大幅正脸特写，右侧 50% 为正面/侧面/背面全身三列；同一人、同一造型与比例，中灰 `#7c7c7c` 背景、柔和棚拍光、无场景、文字、标签和水印。
- 复用场景默认为一张 16:9 的 2×2 四向图：正面/反面/左侧/右侧，四格必须是同一空间的布局、家具、材质、光线、时刻和尺度，且是空场景。
- 关键道具先生成单物、单角度、中性背景的 master，再让持有/穿戴角色状态和破损/掉落状态引用它。
- 同脸换装/伤势/人格变体、道具生命周期和场景不可逆状态使用 base-first 依赖链；互不依赖的 base 可并行。
- Planner 定义稳定 Work Item ID、最终 prompt、有序 refs 和依赖；媒体执行工具只执行明确请求，不在 Tool 内重新发明创意。

这些是安装包中可观察 Workflow 配置，不是 Design Agent Team 的开源实现。当前项目可直接使用其 Markdown 方法，但编排仍落在已有 `AgentRun / ProductionManager / WorkOrder / ToolRegistry`。

### MiniMax-H3 公开方法

[H3 公开仓库](https://github.com/MiniMax-AI/MiniMax-H3) 和 [3D Animation Short Generator Skill](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/3d-animation-short-generator/SKILL.md) 验证了“项目简报 → 剧情 → 角色卡 → 场景卡 → 镜头表/分镜 → 单段视频 → 合成”的交接顺序。角色卡承担 identity lock，场景卡只包含环境和固定地标，视频段必须绑定实际批准的卡片，不使用名称或 `@` 别名冒充媒体。

H3 的 Ref2VA 语义还要求区分：用于人物、场景、服饰、道具一致性的媒体是 Subject reference；只有图片本身定义了起始帧、结束帧或镜头构图时才是 Picture/keyframe。角色卡不应自动被当成首帧。

### LTX、Runway、Veo、Kling 与 FLUX.2 的共同启发

- LTX Studio 的 Elements 是可复用语义主体，可保存多张角度、近景、全身、表情和换装参考；Shot 通过 Element 绑定它。
- [Runway Gen-4 References](https://help.runwayml.com/hc/en-us/articles/40042718905875-Creating-with-Gen-4-Image-References)、[Veo API](https://ai.google.dev/gemini-api/docs/veo) 与 [Kling Multi-image Reference](https://ir.kuaishou.com/news-releases/news-release-details/kuaishou-kling-ai-unveils-multi-image-reference-feature-further/) 都不要求“所有信息塞进一张四视图”；它们允许人物、衣服、道具、场景或风格由不同参考承担，但单次调用有模型容量限制。
- [FLUX.2 Prompt Guide](https://docs.bfl.ai/guides/prompting_guide_flux2) 要求说明每张图的职责，例如图 1 锁人物、图 2 锁服装、图 3 锁背景，而不是传递无语义 ID 列表。
- LTX 开源推理层的多关键帧是带时间位置的 `conditioning_media_paths + conditioning_start_frames`，说明时间型关键帧与身份/空间 Subject reference 应继续保持不同合同。

## 3. 本地实现映射

```text
ScriptVersion + ProjectBibleVersion + ProductionSpec + Storyboard
  → ASSET_PLANNER / AssetPlan（决定资产深度、形态、复用、依赖与最终 prompt 后结束）
  → ASSET_BUILDER（按稳定 work-item ID 执行切片）
  → ASSET_VERIFIER（观察实际媒体并局部回写）
  → Asset / CoreAssetBrief / AssetVersion
  → ReferencePack (一个语义资产的多张可用媒体)
  → Shot ReferencePlan (每镜所需职责与 Provider 预算)
  → H3 Subject/Picture 最少充分输入包
```

- `ProjectDocument + versioned Artifacts` 仍是唯一内容真相。
- `asset.plan.current` 是稳定逻辑输出指针，其内容在版本化 `ProductionDocument` 中。
- ASSET_PLANNER 产生计划与创意 prompt 后结束；Leader 依据 index 委派 ASSET_BUILDER 执行生成/编辑/拼板，再把实际媒体交给独立 ASSET_VERIFIER 记录批准与 ReferencePack 职责。
- 每个 Image reference 显式携带 identity、wardrobe、pose、location、prop、style 或 composition 等职责；Provider adapter 可投影为自己支持的有序图片列表与 prompt 标签。
- 分镜局部修改后，Leader 根据真实语义影响修订 `AssetPlan`；不因计划内的无关项目而重做全部资产。

### 3.1 已落地的图片资产执行合同

- `drama-series` 方法已显式落到结构化 layout profile：持续角色可使用 16:9 左侧大正脸、右侧正/侧/背全身的双区角色卡；重用场景可使用 16:9 2×2 正/反/左/右空场景卡；关键道具先产生中性背景的物体 master，再用 base-first 关系派生持有、穿戴、破损或其他状态。这些是 Agent 可选的成熟方法，不是所有人物和场景的固定模板。
- Image Task 区分 `GENERATE` / `REFERENCE_GENERATE` / `EDIT`：纯文字首版使用 `GENERATE`；从用户源图或其他精确外部参考创建首个标准资产使用 `REFERENCE_GENERATE`；从目标资产已有 base 派生角度、造型或状态使用 `EDIT`。`image.capability` 向 Agent 报告当前本机配置、可用 operation、参考数量与尺寸约束，不暴露凭证，也不通过网络探测代替真实执行。
- Azure GPT Image 2 适配器已将参考生成和编辑投影为 multipart `/images/edits`，保持有序多参考输入并使用高输入保真。每张图必须声明如人物身份、服饰、场景、道具、风格、版式或构图等职责，以及 `AUTHORITATIVE` / `SUPPORTING` / `GUIDE_ONLY` 权威性。人物权威身份参考不得被版式模板或风格图反向覆盖。
- 菜价、金额、姓名、文件条款或屏幕文案等确切文字不交给图像模型重写。`SCREEN_CONTENT_PLATE` + `TEXT_PLATE` + `image.compose_text_plate` 已能在本地按 `required_visible_text` 确定性排版 PNG；物理外壳、纸张或屏幕造型与可读文字保持独立资产。

### 3.2 用户上传图片的 Agent-native 处理

用户上传图片不会被全局自动绑定成主角、风格或首帧。Agent 结合当前对话、AssetPlan 和已有工件作语义分配，并可使用 `qa.analyze_source_image` 观察真实可见覆盖：

- 用户明确“这就是主角”时，该图是 `SUBJECT_IDENTITY + AUTHORITATIVE` 的身份事实，派生四视图、双区卡、侧面、背面或全身图时必须保持同一人，不得平均成新人或重新设计。
- 已经是完整合格角色卡的源图可直接复用为目标 master；只有单张脸部、半身或局部视角时，应以它为权威参考派生标准资产；只承担服饰、风格、构图或版式职责的图不扩散为身份事实。
- 用户没有分配用途、且 Agent 判断对当前近期工作无帮助的图可以暂不使用。这是基于完整语义的制片选择，不是文件名、正则或关键词路由。

### 3.3 实际图后审看、批准与镜头绑定

图像 Provider 返回的图片只是新的 `AssetVersion` 候选，不会因请求成功就自动成为 canonical。ASSET_BUILDER 持久化候选后结束当前制作交接；独立 ASSET_VERIFIER 再让视觉审看 Tool 查看实际输出，核对当前 brief、布局、身份/场景/道具责任和精确参考，并由运行时附着真实证据版本。Verifier 基于结果决定批准、退回局部 work item 或保留为补充参考。

Agent 明确批准的图才按对应 role 进入 `ReferencePack`，视觉审看是可被 Agent 结合完整证据覆盖的建议，不是新增用户审批门。Shot 不默认携带整个参考库，而是依据镜头真正出现的人物、服饰、场景、道具或构图选择“最少充分”子集；Subject reference 与定义起始/结束画面的 Picture/keyframe 继续分离。

### 3.4 Artifact handoff 的上下文隔离

本次真实实跑暴露的不是“视频任务天然需要超长上下文”，而是 Harness 把几种不同用途的状态混在了一起。资产实跑 run 007 的单次 Worker 输入曾达到约 99 万字符，其中约 69 万来自历史 Tool 结果、10 万来自追加进度；普通轮询响应又嵌入完整 `specialist_turns`，使保存的原始响应累积至约 2 GB。这些都是重复回放，不是当前决策真正需要的信息。

当前按以下职责拆分状态：

1. **内容真相层**：`ProjectDocument + versioned Artifacts` 保留完整项目内容，仍由精确 `ArtifactRef` 和显式 Artifact Tool 读取；不通过模型会话历史充当数据库。
2. **运行记录层**：`specialist_turns` 和 append-only progress 继续完整持久化，用于恢复、Trace 和显式调试；它们不再默认进入下一次模型输入。
3. **模型工作集层**：每次 Worker 只收到当前 WorkOrder、当前 `production_prompt_stage + role` 需要的 Skill 片段、去重的 ContextPack，以及由历史折叠得到的 `SpecialistWorkingSet`。Working Set 保留最新精确 handoff、按只读 Tool 身份去重的最新成功观察事实板、按稳定实体 ID 替换的最新状态、每个 Tool 的最新 checkpoint、去重 ArtifactRefs 和折叠进度通道。因此 AssetPlan 与 Provider capability 等独立只读事实可在同一轮合并判断，不会因只有一个 `latest_handoff` 而往返重读；每份观察保留 `source_sequence` 供 Agent 在后续写入后语义判断是否需要刷新。不使用 last-N 或截断整段 JSON 来掩盖结构问题。
4. **资产角色与任务切片层**：全局 `ASSET_PLANNER` 只读取完整 Script/Bible/ProductionSpec/Storyboard 和必要 AssetPlan，创建或修订计划并交回 ArtifactRef 后结束 WorkOrder。Leader 下一轮用 `artifact.get_asset_plan_index` 读取版本身份、稳定 ID、状态、依赖、服务镜头/生成组和完成引用，再按自然依赖簇分别委派 `ASSET_BUILDER` 与媒体产生后的 `ASSET_VERIFIER`；两者用 `artifact.get_asset_work_context` 读取所选工作项、依赖闭包、关联 Shot/ClipGroup、精确 ProductionSpec、用户原始要求和 source intake，不叠加完整项目工件。该 Tool 的 v2 调用合同使用互斥 scope：`WORK_ITEM` 仅接受 `work_item_ids` 并补齐真实依赖，不展开兄弟 work；`ITEM` 仅接受 `item_ids` 并返回整个 item。顶层旧式 selector 和混合 scope 都不再接受。`artifact.patch_asset_plan` 只提交受影响的 item/work-item upsert/removal；只有新的 Planner WorkOrder 在整体盘点、顺序或依赖网络重构时才读取并 replace 完整计划。这同时消除“一个 Worker 把全局规划、付费生成和验收串成超长会话”“Leader 为分派读取完整计划”和“修一个 completed reference 但重传整份 AssetPlan”三类重复。
5. **公开轮询层**：普通 AgentRun get/list/advance 只返回 `AgentWorkOrderSummary`，包含状态、范围、Stage、turn 数和最新 Tool 摘要；只有显式 WorkOrder 详情接口返回完整 Trace。

媒体生成与批准之间还有一项必须持久化的事实：每个 AssetPlan work 的 `candidate_asset_id/candidate_asset_version_id`。Builder 每次得到真实候选后，通过现有局部 patch 写入这对精确引用；轻量 index 显示 `CANDIDATE_AVAILABLE`，后续 Verifier 读取当前候选再判断。候选可以是 DRAFT，不能代替只接受 CURRENT、APPROVED 媒体的 `completed_*`，也不能解锁下游依赖。引用仍保存在同一份版本化 AssetPlan 内，不通过最近几条 Action 或完整历史回放来维持生成进度。

候选 index 同时从对应 AssetVersion 派生 `candidate_approval_status/candidate_freshness`。首轮验收后的续跑发现，仅保留候选 ID 仍会让 Leader 在旧 Action 摘要退出上下文后重复委派验收；索引必须让当前批准、拒绝或过期事实可见。审核意见仍只存于 AssetVersion，需修复时按精确版本读取，不把长评语和完整检查过程灌入 index。回归覆盖 AssetPlan 版本不变、资产审核变化及服务重启后的两种索引一致性。

`reference_pack_intent.subject_item_id` 通过主体 item 的 `completed_asset_id` 映射到真实 `ReferencePack.subject_asset_id`。这里 item 级的 `completed_*` 是已经批准的 canonical 主体引用，不表示全部衍生 work 已完成；整体 item 的索引状态仍取决于全部 work。首次完成 canonical 母图时，应在同一个局部 patch 中写入 item 与 canonical work 的引用。真实续跑曾因字段和错误描述不清，让两个 Worker 误认为必须迁移 ReferencePack；接口现已说明这条映射，回归证明原有 API 可以合法原子写入，衍生 work 仅解锁而不会被误标完成。

纯语言传输中断保留同一 WorkOrder 的 `WORKER_DECISION` 所有权及已持久化 Tool 结果。它没有产生新的完整模型决策，因此恢复时继续原 Worker，不仅因为断流就重新规划制作任务；协议或交付合同错误仍使用原有语义反馈路径。

委派中的少量结构化任务数据使用 `PlanStep.arguments.task_inputs → WorkOrder.task_inputs` 精确持久化并进入 Worker 输入。旧计划中直接放在 arguments 内的非控制字段也保留到 task_inputs，不能静默丢弃；why_now、constraints、交付合同等控制字段继续走原有位置，不重复注入。大工件仍按 ArtifactRef 读取。run034 的恢复任务曾在计划中携带完整候选映射，却在创建 WorkOrder 时丢掉 `candidate_bindings`，这一通用交接问题已补上跨重启、17 项映射规模的回归。

执行器也已区分“需要 Agent 做语义决策”与“只是任务状态推进”。图片任务处于 `QUEUED` / `SUBMITTING` 时，Executor 直接持久化并执行下一个 `image.advance`，不为单纯 poll 再调一次 LLM；失败、状态不明、输出缺失或需要改变方案时才交回 Agent 判断。完整 Tool Trace 仍保留。

这与 MiniMax Design 本地保存 Workflow 的做法一致：Planner 一次只 author 当前 Stage，完整脚本和 Brief 作为 source/文档指针保留，Executor 只读 selected stage detail 和它的稳定 work items。离线回归已验证：重复 80 个决策周期、240 个 Tool turn 后，模型输入不再随历史线性增长。真实运行的字符观测见下节；目前不能把字符数当作 Provider token 或完整成本比较。

## 4. 本阶段验收

当前合同与离线 Fake Provider 回归覆盖以下内容；真实 Provider 与 AgentRun 仍需完成对应的媒体验收：

1. Agent 从精确 Script/Bible/ProductionSpec/Storyboard 自主建立版本化 AssetPlan，包含明确的 anchor / text-only / reuse 决定。
2. 重要高中生角色能选择短剧双区 16:9 角色卡；食堂能选择同一空间 2×2 coverage；普通菜盆、群像和一次性物件可不建重资产。这些只是当前剧情下的语义决定，不是全局规则。
3. base-first 依赖可恢复；互不依赖的角色/场景 base 可并行，状态变体不先于 base 执行。
4. 一个角色可同时保留正脸、全身、侧面或换装等多张已检查参考；更换 canonical master 不会无条件清空全部有效补充视图。
5. 短剧角色卡和场景卡的结构、画幅和无标签设定有确定性测试；拼板不是必经路径。
6. 每个 Shot 只选择当前镜头需要的参考，角色/场景/道具 Subject 与首尾帧 Picture 不混淆，不超过当前 Provider 能力。
7. 最终 trace 能显示方法 Skill、AssetPlan 版本、WorkOrder 依赖、Tool 调用、AssetVersion、审看决定与镜头绑定；不增加 Critic 循环、固定重试次数、审批门或关键词路由。

### 4.1 AgentRun 真实语义规划结果

2026-09-06 使用已经验收的 2 分钟剧本与 canonical Storyboard，在全新运行数据目录、尚无 AssetPlan 和资产的项目状态下启动 AgentRun，结果为 `PASSED_PLAN_ONLY`：

- 总耗时 370.423 秒，约 6 分 10 秒；Agent 只装载 `method.minimax_design.drama_series`，先读取精确 Script、Bible、ProductionSpec 与 Storyboard，再创建并绑定版本化 `AssetPlan`。
- Agent 自主选择 9 个 `ANCHOR`、6 个 `TEXT_ONLY`、0 个 `REUSE_EXISTING`，形成 11 个带稳定 ID 和最终 Prompt 的媒体工作项：9 个独立 base 可并行，2 个 base-first 派生项同时具有有序参考、dependency 和 lineage root。
- 角色、食堂场景及关键证据道具采用锚点；群像、一次性考场与普通物件等保留为文本；协议签字态和录取通知书展开态采用 base-first 补充状态，证明了轻重资产与状态依赖由 Agent 根据完整语义判断，而非固定路由。
- 本次运行未取得或验证可用的图像执行能力，因此 Agent 如实停止在计划闭环，没有伪造图片、审看结论、ReferencePack 或 Shot 绑定。媒体闭环尚未通过，后续须在用户明确选择并授权图像执行方式后单独验证。
- Trace 保留了一次 `artifact.get_production_spec -> INVALID_ARGUMENTS` 及随后的成功自我修正，该过程未被美化或从记录中删除。
- 验证产物保存在 `output/agent-asset-validation/math-2min-plan-20260906-002/run/`，其中 `trace.md`、`asset_plan.md` 与对应 JSON 可复现本次结论。

首轮运行还暴露并修复了两处真实 Harness/合同问题：

1. Specialist 在完整 Tool request 后多输出一个尾随 `}`。运行时现在只对字段形状严格匹配 `SpecialistToolRequest` 的这一种冗余尾括号做窄恢复，不将宽松 JSON 修补扩展成通用猜测。
2. 复杂 AssetPlan schema 原先没有把 `review_purpose=CANONICAL_MASTER` 与 `reference_pack_intent.role=CANONICAL_MASTER` 的跨字段关系充分呈现给 Agent。合同描述已补齐，Specialist 在首次调用复杂、嵌套或批量版本化写 Tool 前也会先加载精确 schema；这属于提高工具可理解性，不是新增门禁或关键词路由。

内容复核还发现当前 AssetPlan v1 在进入媒体执行前需要一次局部修订：留校协议母版同时要求首页条款与末页未签栏，而已签派生项又用首页母版声称只修改末页，单张单视图无法稳定执行。计算器、价牌与食堂附属物的服务 Shot/ClipGroup 索引也有少量过宽和不一致。这些不否定版本化计划、依赖和 Agent 停止语义的闭环，但必须在图片生成前由 Agent 产生 AssetPlan v2，不应直接执行 v1。

对应的通用能力已补进 Asset Worker 与 end-to-end 方法：遵循 MiniMax Design 已有的 deterministic postprocess/still-card 原则，将实体外形与带 `required_visible_text` 的 `SCREEN_CONTENT_PLATE` 分开；多页文件按真实可见页面拆分；服务 Shot 与 ClipGroup 只记真正使用范围。当前后端已实现 `SCREEN_CONTENT_PLATE` 的结构化简报、Prompt、审看语义和 `image.compose_text_plate` 本地确定性排版；剩余缺口是让 Agent 在真实运行中正确重排 AssetPlan、执行媒体、审看实际图并完成逐镜引用，而不是继续代调 Tool 伪造闭环。

### 4.2 真实媒体实跑的当前边界

2026-09-07 的 run034（`agent-run-52da5fdb84fb4677bbeaf15d8d4cea5a`）已通过真实 Azure GPT Image 2 生成 3 张角色身份母图、2 张场景卡、5 张物理道具母版，并完成 7 项本地确定性文字板。它们是候选媒体；完成独立审看、批准、ReferencePack 和 Shot 最少充分引用前，不能宣称真实媒体闭环已验收。

恢复会话时重建的角色、场景、道具和文字板 Worker 下一次输入约 8.5 万至 10.4 万字符（含系统指令，不含后续 schema discovery 和图片字节）。从旧会话日志恢复的 25 个实际模型调用样本，完整文本输入峰值为 269,889 字符，其中用户输入 249,105、系统指令 20,784；这是完整规划阶段的峰值。记录仍不足以给出全程 token/费用或严格 A/B 加速结论。

此次继续运行暴露了恢复缺口：断流后重新派工产生了两份重复的本地文字板；此后 index 只检查 `completed_*`，把已经生成的角色、场景和道具仍显示为 READY，Leader 开始重复制作。已停止该调度并保留媒体，补齐候选引用和纯传输中断的原 Worker 恢复。修复结构化委派输入后，Agent 已通过局部 patch 将 17 项已有媒体恢复为 AssetPlan v3 的精确候选；该恢复没有新增图片生成。它属于同一运行的修复续跑，不能记为从空项目一次无干预通过。

首轮独立看图验收得到 11 项批准、6 项拒绝：7 项本地确定性文字板均通过内容检查；林一、秦校长正脸及计算器、空屏手机母版可用。刘阿姨的食堂职业服饰、两个场景卡的四向空间一致性，以及菜价牌比例、协议夹材质、录取封套装饰分别需要修正。这说明“生成了图片”和“能承担指定资产职责”必须分别报告，2×2 排版也不保证四向空间一致。主体引用说明澄清后，Agent 已建立 4 份 ReferencePack，并通过原有局部 patch 写入 AssetPlan v4 的 4 个主体与 11 个 work 完成引用；其余 work 没有被误标完成。随后开始角色派生，仍未完成全部媒体闭环。

截至上述续跑阶段，重启日志中完整文本输入峰值为 177,025 字符，出现在完成合同修复；包含 12 项道具和文字板的验收 Worker 完成 25 步 Tool 后，输入约 10 万至 11.3 万字符。集中写回 11 项资产时曾达到约 17.3 万字符，略高于普通 Worker 预期范围；拆分诊断显示主要由工作切片、Tool 合同与修复反馈构成，未回灌完整历史。实际角色派生 Worker 的输入约 5.7 万至 8.7 万字符，内容过滤后的后续决策仍约 7 万。上述字符数包含系统指令，不是 token 数，也不包含图片字节。另有一次规划在约 6.9 万输入字符时触及原 7000 token 输出上限；现为规划 JSON 提供 12000 token 余量，并要求简洁地展开当前可执行任务，不增加输入上下文或规定创意任务数量。

林一正面全身随后生成成功，但侧面请求多次返回 `moderation_blocked`；另一份在中断时处于提交阶段的任务恢复为 `SUBMIT_UNKNOWN`，没有作为成功媒体使用，也不自动重提。用户提供了根据既有正面图在 GPT 中生成的 1448×1086 四视图。操作侧已保留原件、去掉标签、裁出四张 322–348×976 原生切片，并复用本地拼板器，以已有 1536×1536 正脸母图加正面、右侧面、背面组成 2048×1152 双区角色卡。1024×1536 单视图是留边与插值版本，不增加原始细节。当前视觉检查支持其作为身体与服装参考，保留中性灰背景及轻微脚下阴影；精确脸部身份仍取高清母图。接入通过既有本地媒体导入、候选版本、补充参考核验和 ReferencePack 路线继续，不将用户提供的图记作 Azure 输出，也不据这一次成功推断两个入口的模型或审核机制差异。

用户随后明确：Provider 内容过滤不属于本次 Agent 问题，不围绕过滤反复修订提示词，也不扩展用户自带生图模型功能；缺图记明 work/视图与参考后交给用户用 GPT 补充，Agent 继续其他可执行工作。三张林一切片已各导入一次并绑定为 AssetPlan v8 候选。接续原 Worker 时还发现探针漏判 `IN_PROGRESS + WORKER_DECISION/WORKER_TOOL`：前一轮 advance 返回 WAITING，board 已看到 Worker 继续执行，探针却要求人工介入。已将这两种本地执行状态纳入可继续推进范围；分镜与资产探针回归45项通过，实际用户输入仍会阻断 unattended probe。

同主体多视图核验还暴露了一个压缩粒度错误：`qa.inspect_asset_version` 的结论虽然包含 version ID，工作集中的 `asset_review` 却按 Asset ID 覆盖，正面和侧面都属于林一，因而来回覆盖结论并重复看图。现在按精确 AssetVersion ID 保存最新结论，已有不同视图的证据可以同时进入一次决策；同一版本的重复检查仍覆盖旧结果。回归用同主体三个版本分别保留不同结论，重复80轮后工作集大小保持稳定；真实持久化记录重建也同时保留了正面与侧面结论。它修正 Agent 对已取得事实的保留，不改变图像验收标准，也不增加看图轮次。

修复后真实 Worker 随即读取背面并完成三图判断，结束原先的交替读取；相关 Production Manager、持久化与探针回归175项通过。背面获批，正侧面此前因身份一致性分歧未批准。操作助手一度将用户“先不管过滤问题”误读成暂缓林一资产，并将错误解释带入重规划；用户随后明确纠正：林一四视图已提供，应继续使用，停止的是过滤排查，任何缺图工作都应保留并向用户索图，收到后接续，不能让实验 Agent 跳过资产与依赖。现已通过最新明确决定撤回该误读，重新纳入林一闭环：高清正脸为身份母图，现有四视图为身体、服饰和方向补充参考。

外部 `replan.reason` 只进入当次 Planner。`AgentRunReplanRequest.confirmed_decisions` 现在可将明确的用户补充写入现有 Run `DecisionMemory`，普通诊断 reason 不自动成为决定，也不将自动重规划原因冒充用户意见。重复提交不重复记录当前同一决定；重启后下一次自动 Planner 能读取该决定的回归已通过。这个持久化能力不能防止操作助手误解用户，错误决定应显式纠正，不能当作真实用户要求。林一误派返工中已提交的一次正面请求仍遭内容过滤，终态结果保留，不继续重试。

继续处理供图时又发现同一类状态问题：工作集中的 `asset_version` 快照和 `asset_version` 引用仍按所属 Asset 合并，导致不同视图的批准状态互相覆盖，Worker 反复写入正面与侧面的审核。现将版本状态与引用都按精确 AssetVersion 保留；同一版本的重复写入仍只保留最新状态，不回灌旧请求正文。新增回归覆盖三视图混合批准/拒绝、序列化恢复、真实尺寸与引用保留，以及80轮重复写入后工作集保持稳定；工作集相关11项测试和 Pyright 通过。

该修复后原 Worker 完成，林一供图三视图与本地双区角色卡均获批。后续刘阿姨侧面实际为四分之三角度，普通质量返工又暴露 `get_asset_work_context` 只有候选 ID、没有该版本已保存的拒绝理由；后续 Worker 因此回头寻找证据。现增加任务切片内的 `media_versions` 只读投影，提供所选候选、完成版本和显式输入的审核状态、`review_notes`、尺寸与来源关系，排除无关历史版本，仍以 ProjectDocument 中的 AssetVersion 为真相。回归验证跨服务重启读取批准/拒绝理由、候选与引用精确对应、同一资产的未选版本不进入上下文；资产计划37项通过，Python/TypeScript检查通过。另在 Tool 参数说明中明确 `version_id` 是生产文档版本 ID，可省略读当前计划，并澄清计划草稿重试 Tool 与图像质量返工的用途。

真实 Worker 随后收到包含2个所选版本与审核理由的10,838字符任务切片，并完成清楚正侧位的新候选。另一次背面验收把正常遮挡的围裙胸片误判为结构缺失；视觉检查提示已补充跨视角可见面与真实结构变化的区别。25项图像检查测试通过，其中旧角色标题断言已与当前兼容角色的职责交接边界同步。上述仍是带人工纠正、代码修复和用户供图的续跑，不能表述为未受干预的一次全自动成功。

再次断网恢复后，三角色标准卡及菜价牌、留校协议、录取封套的可用版本均已批准。菜价牌与录取封套的复核保留真实物理结构、无字区域和完整边缘，干净灰色背景的轻微渐变与地影记录为合成限制，不再单独触发重生成。AssetPlan v22 已写入27/29个 work 的精确完成引用，未完成的仍是食堂与考场四向场景卡。集成 Worker 曾在仍可执行时提前返回 PARTIAL，完成补丁也曾误写只读的 `/status`；修正后仅写入 `completed_asset_id/completed_asset_version_id`，未改变状态派生方式。较大收尾任务的实际文本输入峰值达到188,388字符，仍是含系统指令、不含图片字节的字符统计，不是 token 数。

ReferencePack 的正确结构是9个合法主体包包含27个完成版本，六张独立 `REFERENCE` 类型文字板作为对应物理 `PROP` 载体包内的 `PROP_STATE` 成员；不能为 `REFERENCE` 资产创建独立主体包，也没有 `SCREEN_CONTENT` 这个 ReferenceRole 枚举。旧实跑探针只识别包主体，会把这类已打包文字层误报为缺失。现将已批准、当前且血缘有效的成员 Asset 纳入包覆盖，并在 Shot requirement 的用途角色和必需标签匹配后解析成员所属 Asset。方向和景别只是选择偏好，不是资格过滤条件；探针与实际 ReferencePlanner 保持一致。新增验收用例覆盖匹配成员、非偏好方向、标签不匹配、缺少条目、旧血缘，资产与分镜探针合计50项测试通过。探针的可满足性检查不等同最终选择：仍用现有 `shot.preview_reference_plan` 检查实际 selected，确认不同文字需求没有复用同一文字层，且物理载体与内容层分别选中。

逐镜收尾又发现 `get_asset_work_context` 虽包含计划意图，却缺少实际保存的素材包字段和当前镜头引用，Worker 因此猜测 role、subject 或标签，写出了无法选图的 requirements。该 Tool 现按所选媒体的主体或成员关系返回 `reference_packs`，与实际选择器共用当前批准、血缘有效条目的过滤逻辑；同时仅为显式请求的 Shot 返回 `shot_reference_plans`，便于合并后更新。它们均是 ProjectDocument 的只读切片，不新增内容状态。跨重启回归覆盖仅选文字板时找到其物理载体包、保留实际大小写标签、排除无关包与拒绝条目、保留已有镜头计划，以及读取不修改原数据。第一版修复的104项相关测试和完整 Pyright 通过，真实 Worker 读取的59,884字符切片包含5个包、16个条目和4个当前镜头计划。

真实续跑同时揭示下一层问题：Shot 写入使混合读取因修订变化整体过期，原先包内信息随之丢失；`shot.update_reference_plan` 又只返回 ProjectAggregate，压缩后没有当前引用明细。现在依照既有工作集机制按 ReferencePack ID 和 Shot ID 保留最新独立实体；同一主体写入先移除旧实体，再由精确结果更新。Tool v2 返回单个已保存 `reference_plan` 与 Shot 修订，不再返回完整项目，服务与 REST 的原有持久化行为不变。复用原6轮记录重建后仍保留5包16条目及真实标签，工作集为25,108字符；这是只读重建证据，不冒充新的模型成功执行。回归覆盖失效的混合读取被移除、未修改包和另一Shot继续可用、已写Shot保留新计划、包修改替换旧条目，以及其他Shot修改没有引用明细时不保留旧计划。ProductionManager、资产计划、引用选择、Tool registry/覆盖和两类实跑探针合计238项测试通过，完整 Pyright 无错误。

随后真实 Worker 的写入和标签已正确，但预览仍只按 Tool 保留最后一次，001b/002b 因互相覆盖而反复读取。现在将预览也按 Shot ID 保留为 `reference_plan_preview`；Shot 写入只失效对应预览，素材包或资产写入使旧选择失效。补充回归验证两个Shot的不同预览结果共存及写入后的失效行为，12项工作集测试和完整 Pyright 通过。原Worker虽然又因多个JSON尾随字符提前交回，其实际写入仍保留；14:27 UTC 通过现有preview逐版本核对，前7个Shot的必要非场景引用均正确选中。剩余14Shot继续从原Run收尾，不将协议失败掩饰为无干预成功。后续实际镜头选择继续由既有 preview 检查，媒体完成指针不能代替引用闭环。

14:40 UTC，revision49 的三个执行 Worker 已分别用9、18、9次 Tool 完成剩余4、6、4个Shot；校长核验 Worker 的四个最新预览同时保留，没有再交替覆盖。对全部21个Shot执行实际preview后，各必要非场景媒体均被选中，缺失requirement和缺少的非场景anchor均为空。27个完成版本全部APPROVED/CURRENT、文件存在且已打包；15个非场景ANCHOR的逐镜引用已完成。Run现为 `WAITING`、`goal_complete=false`，只有最后 `USER_INPUT` 等食堂与考场原图，没有活跃Worker；无人值守探针遇到该输入节点按原语义退出，不是新的生成失败。最近续跑累计文本输入峰值为190,813字符；整轮仍记录为包含用户供图和多次修复的续跑，待两张场景图接入后才能继续完整媒体验收。

场景补图安排在后续自动重规划中一度丢失，食堂重制在操作侧取消前已经提交并成功返回一次额外输出；该结果保留为未绑定的 DRAFT，操作侧查看后仍不足四向覆盖，未冒充可用场景。截至这次恢复，累计25个图像任务成功、12个失败、1个 `SUBMIT_UNKNOWN`，成功图像费用本地估算5.862217美元，不含未知的语言费用或未报告用量。后续计划明确保留最后的 `USER_INPUT`，待当前用户提供食堂与考场两张原图，再沿原 work 接回导入、验收和引用。补图包 `scene-input-request.zip` 包含现有正向参考和 GPT 提示词；停止的是重复 Provider 尝试，不是这两项工作，也不是全Run已完成。

15:27 UTC，用户返回食堂、考场两张 GPT 四向原图，并明确要求“就这吧，直接通过得了”。原 Agent 使用本地导入与审核 Tool 将两张图记录为用户确认的 `CANONICAL_MASTER`，没有重新调用视觉检查或图像生成。食堂新版本为 `asset-version-1476d1902dc5`，考场为 `asset-version-2d692b3e6dc8`；AssetPlan v24 的29项 work 均已完成、29个完成版本均为 APPROVED/CURRENT，11个 ReferencePack 包含全部29条媒体。此时后续三个 Worker 正在将场景引用合并进原21个 Shot，媒体完成不单独代表 Run 已完成。

两张供图的实际尺寸均为1672×941，保持上传原图。接入指令曾误把该实际尺寸写入要求精确16:9的生成目标 `work.size`，导致一次候选绑定失败；恢复时复用已经导入的食堂版本，保留原生成目标2048×1152，并由 AssetVersion 元数据和说明记录实际1672×941，继续完成接入。没有为适配计划字段而缩放或重生成，也没有把目标尺寸当作媒体原生尺寸。本次视觉批准依据是用户决定，不计作自动视觉验收通过。

15:42 UTC，三个场景引用 Worker 完成全部21个Shot，原Run在revision55进入 `COMPLETED`、`goal_complete=true`，真实探针返回 `PASSED_MEDIA_CLOSURE`，errors与media_gaps均为空。只读实际preview检查确认21/21个Shot均选中必要场景和非场景媒体，食堂新图覆盖20Shot、考场新图覆盖1Shot；29个完成版本均有效且已进入11包29条ReferencePack，17个ANCHOR全部完成逐镜引用。记录分别为 `actual-all-shot-reference-audit.json`、`current-closure-audit.json`、`trace.json` 与 `final-completed-agent-run-snapshot.json`。从本次用户场景接入开始记录的130次语言调用均成功，文本输入为34,727至144,522字符，中位数87,704；统计包含系统指令、不含图片字节，不等同token数。此次没有新增图像任务，累计图像任务和成功图费用估算保持25成功、12失败、1未知及5.862217美元。完整结果仍属于修复续跑及用户供图、用户确认参与下的核心资产闭环，不是从头无人干预的一次成功，也不包含视频、音频、时间线或导出。

产物与复现记录位于 `output/agent-asset-validation/math-2min-asset-media-context-v17-20260907-034/run/`，包括原始 trace、恢复前数据库快照、候选映射恢复请求、上下文快照及压缩图片总览。完整可交付包为 `core-assets-complete.zip`，包含29份实际完成媒体、三张缩略总览、实际尺寸与版本清单以及21Shot引用检查；ZIP共35个文件、38,109,021字节，完整性检查通过。林一用户供图及标准化文件另在 `user-supplied-lin-yi/`，单独压缩包为 `lin-yi-standard-core-assets.zip`。本次两张场景原图、预览和用户批准来源在 `user-approved-scenes/`，接续请求为 `user-approved-scenes-size-semantics-replan-request.json`。候选引用的既有离线回归覆盖 DRAFT 持久化、重启、索引与工作切片一致性、无效媒体拒绝及不提前解锁依赖；相关资产与 Harness 回归 266 项通过，Pyright 无错误。

## 5. 后续 ComfyUI 衔接边界

开源 ComfyUI 可以后续承担确定性拆图、裁切、放大、去背景和高级生成图。[ComfyUI 官方文档](https://docs.comfy.org/custom-nodes/overview) 区分用于编辑的 workflow JSON 和用于 API 执行的 prompt graph。未来衔接使用稳定模板输入名，不让 Agent 直接操作节点坐标：

```text
AssetPlanWorkItem + AssetVersion
  → typed workflow binding manifest
  → ComfyUI API-format template
  → prompt_id / history
  → output AssetVersion
```

MiniMax Design ComfyUI plugin 中的拆图/裁切 blueprint 可在处理 GPL-3.0 许可边界后迁移；它不包含 Design 的 Asset Planner 或 Agent Team 源码。LTX 开源生成 Workflow 本阶段不接入，LTX Desktop 仍只作为后续编辑器与时间线底座。
