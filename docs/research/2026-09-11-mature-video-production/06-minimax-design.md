# MiniMax Design：职责边界、阶段契约、制片知识和本项目的具体差距

调研日期：2026-09-11。本文接续 [03 开源视频实现](03-open-source-video.md)，重点重新读取本仓保存的 Design 3.0.11 Workflow，以及当前 Short Drama Studio 的实际加载、编译和执行代码。没有启动 Design、检查私有安装包代码、运行外来代码或调用收费 Provider。来源追溯从 [37 既有研究](/Users/liyifan/Documents/ChatGPT/ai短剧调研/docs/37-phase26-platform-paper-open-source-low-retry-research.md)、[41 Skill 研究](/Users/liyifan/Documents/ChatGPT/ai短剧调研/docs/41-production-skill-research-and-enrichment.md) 和 [43 Design 蓝图](/Users/liyifan/Documents/ChatGPT/ai短剧调研/docs/43-minimax-design-agent-team-product-blueprint.md) 开始；旧文档中的实现结论以下文重新核对的源码为准。

最值得借鉴的是：**把创意写作、输入编译、媒体执行和任务恢复分开；用正式工件和可执行工作项传递事实；按当前产物加载专业知识。** 当前项目已经有相当完整的骨架，但“拥有字段和 Skill”不等于执行时避免了反复读取、重复发现 Tool、重复解释冲突规则。尤其是已经发现的两套 H3 Prompt 骨架同时进入真实请求，比再增加一个导演角色更值得先解决。

## 1. 哪些是公开事实，哪些仍没有运行证明

| 来源 | 本次确认的内容 | 不能据此声称什么 |
| --- | --- | --- |
| [Design 官网](https://design.minimax.io/) | 公开产品将 brief 分解、模型选择、脚本/分镜/视频/音频/剪辑、Skills、局部检查和本地素材放在同一桌面体验中；宣传多个模态 Agent 并行 | 没有给出长期完成率、重试成本、断点恢复测试或每个模态一个独立常驻 Agent 的源码证明 |
| [MiniMax Agent Team 官方文章，2026-05-27](https://www.minimax.io/blog/minimax-agent-team-long-running-1779893953) | 讨论 Leader/Worker/Verifier、持久任务状态、主动消息与按需查询、路径加摘要的交接、共享白板，以及团队拆分和合并成本 | 这是 MiniMax 通用 Agent Team 的技术说明，不能直接等同于 Design 3.0.11 每一条制片链；文章说将来开源，本次未找到 Design Team Engine 的公开仓库 |
| 本仓保存的 [Stage contract](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/_shared/stage-execution-plan.md:12) 和 [drama workflow](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/drama-series/workflow.md:44) | 可以逐条阅读 Planner/Executor 输入、输出、refs、依赖、局部修改和编译职责；版本是已保存的 3.0.11，不宣称是官网目前最新安装包 | Markdown 中“validator 会校验”“最多并发 10”等是该版本作者的执行约定；没有私有运行时或本次实测来证明全部生效 |
| [MiniMax-H3 官方仓库](https://github.com/MiniMax-AI/MiniMax-H3/tree/d21241f0a4b3acbb34c97dae47fa417b7065e438) | 公开 Prompt IR 指南、任务 Skills、推理模型与工作流；本次源码快照 d21241f，2026-08-15 | README 明确 H3-Context-IR 内部多阶段托管系统没有开源；H3 权重开源不意味着完整 Design 应用开源 |
| [公开 Design ComfyUI 插件](https://github.com/MiniMax-AI/minimax-desgin-plugin) | GPL-3.0 的 ComfyUI 执行界面及后端快照，包含真实 H3 Context IR、生成与增强节点 | 它不包含 Main Agent、Planner、Team Engine 或角色卡规划器；节点依赖 Design Gateway 的部分不能直接当作独立公开 API 服务 |
| [MiniMax-AI/skills](https://github.com/MiniMax-AI/skills) | 另一个 MIT、beta 的通用 Skill 仓库，覆盖开发、文档和媒体 | 不能把这个仓库的 MIT 自动套到 H3 仓库或本地 Design Workflow；也不能把这些通用 Skills 统称为 Design 私有 Prompt 源码 |

官方 Agent Team 的有用经验是：任务会话应独立于当前模型窗口；大资料用文件/工件加摘要交接，通知只传变化；不同 Worker 应具有不同的工具、上下文、经验和交付协议；短而确定的操作可由单 Agent 或普通代码完成。文章同时承认拆分、验证和汇总会增加时间与 token，未承诺“更多 Agent 必然更快”。以上是公开设计说明，本项目应验证具体收益。[官方文章](https://www.minimax.io/blog/minimax-agent-team-long-running-1779893953)

本仓 [NOTICES:12–16](/Users/liyifan/Documents/ChatGPT/ai短剧调研/NOTICES.md:12) 已明确：保存的 Design Workflow 没有确认公开再分发授权。本地研究可以据其改进方法；正式分发需要许可，或把有效方法独立改写为可分发的项目知识。下文“借鉴”不表示可以直接把安装包原文作为本产品公开资产。H3 Community License、通用 skills 的 MIT 和 ComfyUI 插件 GPL-3.0 分别处理。

## 2. Design 真正值得吸收的执行结构

旧 [43:192–210](/Users/liyifan/Documents/ChatGPT/ai短剧调研/docs/43-minimax-design-agent-team-product-blueprint.md:192) 记录过 `media-agent → router → planner → executor(stage)` 的安装包观察。本次没有重新检查安装包，不能升级为最新运行事实。当前保存的 Workflow 本身可以独立支持以下责任边界：

```text
用户目标 + 本次来源 + 已有有效结果
  → 规划当前可交付产物，读取该产物所需知识
  → 正式文档 / 可执行 work_items
      prompt + ordered refs + source range + duration + dependencies
  → 执行器解析真实媒体、调用供应商、保存输出和失败
  → 只有创意或来源变化才回到规划者；网络/参数错误局部处理
```

这不是建议把本产品改成固定七阶段。用户只要剧本就以剧本为完成边界；用户没有要求审阅就持续执行。本项目继续用已有 WorkOrder 和领域工具承载这些职责。

| 机制 | 保存原文的精确要求 | 本项目应该怎样用 |
| --- | --- | --- |
| 当前阶段独立执行 | Planner 完成知识编译；Executor 只取指定 stage detail，不再读全量 chat、画布、workflow 来补足任务。[contract:12,31–40](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/_shared/stage-execution-plan.md:12) | 保留语义 WorkOrder；其中调用媒体生成的那一步必须已经有最终输入。Builder 可以做必要选择，但不应重新解释剧本、重造当前组分镜或在网络重试时重写创意 |
| 逐步物化 | 先写阶段轮廓及当前阶段，不提前制造尚无真实来源的下游 prompt；未来依赖用计划表达，不用假路径。[contract:24–36](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/_shared/stage-execution-plan.md:24) | 保留现有按场次/组增量保存；不要为了完整计划让 Leader 一次把所有资产和视频请求写完。必要的独立内容可以并行，不照搬“整个阶段未接受就禁止一切下游”的产品门 |
| 有序 refs 与作用 | 每个输入有明确锁定贡献；prompt 中图/音/视频槽和实际 refs 严格一致；执行器不排序、去重或偷偷补图。[contract:38–43,125](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/_shared/stage-execution-plan.md:38) | 让 ShotReferenceCue、媒体输入和 Prompt 绑定共享同一映射。视觉上选到了图，不足以证明正确传给 Provider；必须检查实际请求顺序和用途 |
| 稳定输出身份 | 重做同一输出保持 work_item ID，当前输出指针替换；重试范围不等于完整物料表，不删掉已成功项来表示只重试失败项。[contract:42,133–134](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/_shared/stage-execution-plan.md:133) | 沿用现有 AssetPlanWorkItem、Shot 和 Take 当前绑定；避免加内容版本选择界面。局部失败清单绝不能被下一轮 Worker 当成所有可用资产 |
| 上游依赖 | 消费的 refs 必须来自明确来源、当前 capsule 或直接依赖；资产基础图先生成，变体消费真实基础图。[contract:43,134](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/_shared/stage-execution-plan.md:134) | 现有 AssetPlan dependency_ids 继续用；“同一个人”必须体现在共享基础图和状态变体引用中，不能只靠重复姓名 |
| 并行按媒体任务计算 | 独立 clip 默认并行；按真实模型 task_concurrency 滑动补位，H3 示例上限 10；真正共享状态才串行。[contract:46–53](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/_shared/stage-execution-plan.md:46) | 区分语言 Worker 并发、Provider 在途媒体数和本地渲染资源。本项目默认 4 个 Worker 不是 H3 官方限流；也不能仅凭原文把账户并发硬改成 10 |
| 非创意失败不改内容 | 缺字段、超时、网络错误只改点名字段，原 prompt、refs 顺序和镜头段数复用；来源或用户意图变才重新编译。[H3 reference:89](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/drama-series/reference/h3-video-prompt.md:89) | 继续通过 ToolAttemptRef / 已持久化请求恢复。补齐 `reason → revision_instructions` 等明确编译反馈通道，不能把外层 Agent 的解释当作内层模型已经收到 |
| 局部修改与重规划 | 修改当前产物同时 patch contract 和受影响正文，只重做稳定 ID 的受影响项；保留有效前缀。[workflow:38,65](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/drama-series/workflow.md:65)、[contract:138–146](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/_shared/stage-execution-plan.md:138) | 映射到当前 ProjectDocument 和内容修改，不复刻另一份 Stage Plan。用户换某镜参考图后重算本组输入及实际受影响项，保留其他可用结果 |

一个值得改进上游的细节：保存的资产参考把 base-first 子链写成整个 Stage 的串行 policy，[assets-blocking:194–199](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/drama-series/reference/assets-blocking.md:194)。本项目已经有更细的工作项依赖，可以让角色 A 的基础/变体与无关场景、角色 B 并行；只序列化真实依赖边。复制上游整阶段串行反而可能变慢。

## 3. 专业知识应留下什么

这里的价值来自可执行的输入和判断方法，不是“你是世界顶级导演”这样的身份描述。

| 专业方法 | 有效内容和用途 | 移植时要改的部分 |
| --- | --- | --- |
| 角色/场景/道具锚点 | 持续角色需要身份覆盖；同脸换装/受伤图从基础脸派生；生命周期道具从主卡派生；一次性短场景不必为它额外建四向卡。[assets-blocking:6–21,57–62,162–199](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/drama-series/reference/assets-blocking.md:6) | 卡片布局服从本项目实际采用的四视图和用户图；不要给每个一次性小物件扩成完整资产家族 |
| 世界状态与机位分离 | 人物相对场景锚物的位置、身体朝向、视线、道具持有人属于世界事实；画面左右和前景肩属于当前镜头投影。正反打不能自动交换人物世界位置。[assets-blocking:82–160](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/drama-series/reference/assets-blocking.md:82) | 用已有场次、Shot continuity 和组级来源保存；简单双人静态场面不需要额外文档。复杂 A→B→A 返回需要读取 A 上次末态，不能仅取全片上一镜 |
| 按镜头生成输入 | 每个来源镜头保留一个完整叙事段：起点与机位 → 触发与行动 → 逐字对白/表演 → 听者反应与声音 → 末态；重要角色不说话时仍有在场位置。[h3-video-prompt:66–82](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/drama-series/reference/h3-video-prompt.md:66) | 保留信息结构，去掉“一切真人都 f1.8 长焦浅景深、必须少中景”的固定审美；视觉风格属于项目 |
| 声音与表演 | 声线签名用于跨组人物一致性；每句即时语气由刺激、呼吸、语速、停顿和反应落实；原生视频有音轨，不代表必须再生成 TTS。[h3-video-prompt:60–64,84–89](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/drama-series/reference/h3-video-prompt.md:60) | 音频判断来自听审/音频分析；不能从静态抽帧证明对白节奏和音色通过 |
| 完整参考名单 | 先按本组全体在场角色、场景和核心道具建立需求，再对完整可用资产表匹配；不能从局部重试列表推断“其他图不存在”。[h3-video-prompt:44–52](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/drama-series/reference/h3-video-prompt.md:44) | Provider 限制放在适配层；先考虑实际贡献、状态和重复图，不能盲目把所有匹配资产塞满，也不能静默丢弃用户选图 |
| 确定性后期 | timeline 直接引用真实已落盘媒体；字幕依次来自实际时间信息/转写、格式化、渲染，通用合并优先专用工具。[contract:59–61,130](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/_shared/stage-execution-plan.md:59) | 延续本项目 LTX/FFmpeg 和实际音轨对齐，不让 Agent 用字幕末尾时间替代媒体真实长度 |

建议将方法改写为短小的可执行章节，例如以下**本项目建议措辞，非 MiniMax 原文**：

```text
输入：当前生成组的正式镜头、逐字对白、组起始状态、有序媒体绑定、项目声音与视觉方向。
逐镜写出动作和反应的因果承接；保留说话人及原台词，不添加来源未有的情节。
相机变化只改变构图；没有明确人物动作时，不改变人物、道具的世界状态。
只引用实际传入的媒体槽位，并说明每个槽保留的身份、空间、状态或声音。
输出格式、时长、画幅和媒体上限由当前 Provider 契约提供。
若失败只涉及网络或请求字段，返回该字段的修复；不要重写已有效的创意内容。
```

这样把方法知识留在 method，把 H3 的英文六字段、标签和技术参数留在 Provider compiler，把工具选择与任务恢复留在 runtime。它对应保存的 [AUTHORING:7–12](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/AUTHORING.md:7)“规则只保留在最窄唯一层级”的意图。

## 4. 当前实际差距：有代码、也有真实输入证据

### 4.1 H3 的两套互斥骨架仍同时进入编译器

当前 [manifest:85–93](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/methods/drama_series/manifest.json:85) 给 `VIDEO_PROMPT_COMPILER` 注入了 Design 的“单一来源与编译边界”“H3 唯一可执行填空骨架”和全部自检。其来源要求保留中文固定首尾、章节顺序及 Mode A 摄影句。随后当前 [_compiler_instructions:291–305](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/providers/h3_language_prompt.py:291) 加载公开 H3 Prompt IR，并再解释中文固定骨架不属于当前 API、必须忽略。

这已经在真实请求出现：准备案例的 [00304/instructions.md:39](/Users/liyifan/Documents/ChatGPT/ai短剧调研/output/agent-video-preparation/math-2min-pre-h3-20260908-001/model_calls/00304/instructions.md:39) 要求逐字骨架，`:43–45` 给中文骨架和 f1.8；同一请求的 [:209](/Users/liyifan/Documents/ChatGPT/ai短剧调研/output/agent-video-preparation/math-2min-pre-h3-20260908-001/model_calls/00304/instructions.md:209) 给英文六字段 IR，[:434](/Users/liyifan/Documents/ChatGPT/ai短剧调研/output/agent-video-preparation/math-2min-pre-h3-20260908-001/model_calls/00304/instructions.md:434) 再否定前述中文输出协议。该记录来自 2026-09-08 准备实验，不能冒充今天的新实验；但今天静态调用链仍保留同一冲突组织。旧记录里的 4500–6000 字符写作配额今天已移除，不把它列为当前问题。

**结论边界：**可以确认“互斥协议同包注入”，不能仅据此把某次编译失败或全部延迟归因于它。现有末尾优先级说明可能成功消解冲突；问题是每次请求仍承担无必要的解释和选择负担。

**建议修改：**保留来源原文；为 `VISUAL_GEN.VIDEO_PROMPT_COMPILER` 增加本地适配摘编，只保留动作、空间、表演、声音和参考贡献知识。Provider 格式只注入一次。资产侧也有类似组织：Design 双区模板与本地 [footer:11](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/shared/official_task_method_footer.md:11) 四视图策略靠后置覆盖协调，适合一起将执行文本整理为单一当前规范。收益需通过同一已有组的输入检查和编译表现验证，不预报百分比。

### 4.2 H3 生成组主链明确仅支持 9:16，这是待扩展能力

当前 [ProductionSpecContent.aspect_ratio:117](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/production_contracts.py:117) 是非空字符串；[通用视频 Skill:14](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/formats/general_video/SKILL.md:14) 将画幅视为作品选择；产品界面说明展示当前画幅并按项目预填导出。这些通用层允许表达更多画幅，不等于当前 H3 路线已经支持它们。

完整读取调用链后可确认：[_h3_generation_unit_sources:10842–10846](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/service.py:10842) 在组上下文读取和语言编译之前显式拒绝非 `9:16`，并说明其他画幅需要更新 Provider adapter。因此后续 [h3_language_prompt:513](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/providers/h3_language_prompt.py:513) 传入 `production_spec.aspect_ratio`、[validate_h3_generation_unit_prompt_ir:323](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/providers/h3_prompt_skill.py:323) 校验 `9:16`、[compile_h3_request:201–206](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/providers/minimax_h3_compiler.py:201) 非首尾帧路线发送 `9:16`，在该主链上是一致的能力边界。**不能把这些下游常量单独写成“横屏被悄悄改成竖屏”的活跃生成 bug。**

保存的 Design [contract:128](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/_shared/stage-execution-plan.md:128) 把画幅和分辨率作为当前阶段共同事实，角色卡和视频可以不同画幅。可借鉴的是让 Agent 在选路线时就获得准确能力，并在扩展时把画幅贯穿编译、校验与请求。当前差距是通用创作层与具体路线的能力范围不同、限制分布在多个入口；本次未确认另一个接受横屏项目规范后仍错误提交竖屏的真实可达入口。**横屏支持应列为待扩展能力，不是校园案例返工原因；当前竖屏主链的提前拒绝已有实现。**

### 4.3 Tool discovery 没有随 WorkOrder 持久保持

[WorkOrder:513–550](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/agent_runtime/models.py:513) 保存任务输入、工作笔记、scope、期望输出、历史回合等；当前 [LanguageSpecialistAgent.execute:4640–4666](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/production_manager.py:4640) 每次从修复状态和协议错误重建 loaded_schema_ids。正常 schema 发现只在本次函数 while 内 `extend`；[4740](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/production_manager.py:4740) 返回一次 Tool 请求后没有把普通已发现 schema 写回 WorkOrder。下一次决策很可能再花一轮请求相同 schema，和 [:4511](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/production_manager.py:4511)“已加载 schema 不重复请求”不一致。

这是本项目实现问题，不是可以从未公开的 Design 源码直接获得的修复。Design 提供的是“当前执行器拥有完整执行契约”的设计参照。可直接参考开源 [ViMax 的循环内 schema 保留](https://github.com/HKUDS/ViMax/blob/05a48943878312d88fe5a016c12a9654940ecc43/agent_runtime/loop.py#L44)：在本项目按 WorkOrder 持久保留已发现集合，任务职责变化再更新；不要一次塞入全注册表。保存 schema 引用/版本即可，不必每个历史回合复制全文。

### 4.4 好的 context 边界已经存在，但还需执行一致

本项目并非只能把完整项目喂给 Worker：[get_work_order:6256–6293](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/production_manager.py:6256) 按选定 role/stage 编译 prompt；[get_asset_work_context:6872](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/service.py:6872) 按对象和工作项读切片并解析真实依赖；[build_generation_unit_language_prompt_input:442–537](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/providers/h3_language_prompt.py:442) 从当前正式组取得全部镜头、逐字对白、媒体绑定和前态。这些边界比添加一个新的全局共享白板更有用。

下一步应检查重复读取发生在什么地方、读完是否保留、组内 source 是否足够，而不是再提高上下文上限。特别是 [_previous_shot_terminal_state:262–281](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/providers/h3_language_prompt.py:262) 只选全片前一镜；对 A→B→A，A 的末态应来自 A 上次出现的位置。当前镜头自身 continuity 可能已经补足，因此这是**需要用回场样例检查的覆盖风险**，不能仅据函数认定成片穿帮。Design 的组级完整快照方法提供了具体检查项。

### 4.5 并发和停止点不要抄成更重的流程

当前 [LocalTeamExecutor:100](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/team_executor.py:100) 默认 4 个 Worker，[:145–170](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/team_executor.py:145) 并行推进，[:482–484](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/team_executor.py:482) 仅 `WRITE` 进入项目写通道；Provider IO 依靠短保存边界，不再整个项目串行。文件开头旧描述比真实条件宽，不据注释错误判断 `EXTERNAL` 都串行。

Design 的滑动媒体并发可以作为调度目标，但当前每批等待已选 WorkOrder 返回是否成为关键路径，还需要真实任务时间线来确定。先分清 Provider 等待、语言决策、写入和本地处理占时，再决定是否对独立媒体任务补位；不凭“4 小于 10”宣称瓶颈已证。

Design drama workflow 有剧本、资产、分镜、视频前后多次确认。[AUTHORING:43–48](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/AUTHORING.md:43) 中“已有信息不重复问、同阶段问题合并、问题不是审批”值得吸收；[:49–63](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/AUTHORING.md:49) 的强制阶段审阅与本用户已授权的自主执行不同，不能移植为默认暂停点。信息不足、用户主动指定确认、目标已经完成、技术失败四种情况必须分别表达。

## 5. H3-Context-IR 是值得比较的可选工具，不是 Agent 上下文管理器

[官方 H3 README:89–104](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/README.zh-CN.md#L89) 描述 H3-Context-IR 对文本、图片、音频和视频的关联理解，然后产生 H3-Base 需要的提示词；它的内部实现不在开源包里。公开 [h3-prompt-writing/SKILL.md](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/SKILL.md#L9) 则指导用户自建：按模式选 base/ref 指南，保留字段、时序和参考标签。现有 Studio 已走自建编译器路线。

[官方公开 API](https://platform.minimax.io/docs/api-reference/video-generation-v2-h3-context-ir) 是 `POST /v2/h3_context_ir`，返回 task_id，成功后从任务查询的 `content.prompt` 取增强结果；它不会直接创建视频。这个可用接口与通用 Agent 的 ContextPack、记忆或压缩不是同一种东西。**本次在 Studio providers/tools 未找到 `h3_context_ir` 调用实现**，但这不证明“没有它就一定效果差”。

公开插件确有节点实装：[nodes_minimax_h3.py:285–367](https://github.com/MiniMax-AI/minimax-desgin-plugin/blob/bef9de2101e9d873619c83553b8f03ebb0c10663/comfyui-plugin/backend/comfy_api_nodes/nodes_minimax_h3.py#L285) 检查 keyframe/reference 互斥和 9/3/3 输入数量、上传实际媒体、提交、确认 task_id、poll 并拒绝空 prompt。它使用的是 [Design Gateway 私有路由及登录配置](https://github.com/MiniMax-AI/minimax-desgin-plugin/blob/bef9de2101e9d873619c83553b8f03ebb0c10663/comfyui-plugin/backend/comfy_api_nodes/nodes_minimax_h3.py#L36)，本产品接入时应写官方 API adapter，复用本项目已有外部任务保存和恢复能力。该单节点函数内的 submit→poll 并不能证明应用重启后完整恢复。

合适的用途是挑已有复杂组做小范围比较：现有编译器与官方 Context-IR 接收相同的正式分镜、媒体顺序、画幅和时长，比较遗漏事实、对白和身份绑定、编译耗时及最终视频。不要把两个编译器默认串联；重复扩写可能再次改变意图、增加 token 和延迟。先验证文本产物是否保留契约，再决定是否值得真实媒体调用。本次只完成接口与源码调研，未进行收费试验。

## 6. 建议落地次序与最小验证

| 次序 | 具体改动 | 为什么先做 | 足够的验证 |
| --- | --- | --- | --- |
| P0 | WorkOrder 持久保持已发现 Tool schemas | 直接减少协议往返，避免靠模型记住实际已经丢失的知识 | 同一 WorkOrder 连续两次正常 Tool 调用，第二次不用重新发现；切换工具、协议修复和重启后集合正确 |
| P0 | 重写实际注入的 Design 适配文本，移除互斥骨架和后置否定堆叠 | 已有真实输入证明冲突组织，改动范围集中 | 从同一已有生成组构建输入，确认仅一种输出协议；逐镜事实、原台词、参考贡献仍齐全；不靠角色名或目录数量验收 |
| P1 | 在路线选择和工具能力说明中统一呈现当前 H3 仅支持9:16 | 通用 ProductionSpec 可表达更多画幅，当前生成组入口已明确拒绝；避免规划到最后才发现路线不支持 | 现有非9:16输入在读取组上下文/编译前明确报能力限制，Agent提前读到同一限制；不将该拒绝算作错误生成 |
| P2 | 按产品需要扩展 H3 横屏适配，将画幅贯穿编译和请求 | 新增已有通用创作层可表达但当前路线尚不支持的能力 | 扩展完成后一个离线16:9用例从输入、校验到请求保持16:9；现有竖屏和首尾帧规则继续正确 |
| P1 | 以真实依赖驱动资产和媒体就绪执行 | 减少无关串行，不增加 Agent 讨论 | 角色 base→variant 保序，无关资产并行；已提交 task 不因恢复重发；只重做受影响项 |
| P1 | 将组级完整前/后态作为当前组的精确上下文 | 降低沉默角色丢失、道具重置、回场跳位 | 使用一个多人回场和一个换装/道具传递样例，检查源事实→编译输入→生成引用，不新建固定审校循环 |
| P2 | 可选官方 H3-Context-IR adapter | 可能补充现有纯文本编译不足的跨模态关联，收益尚未知 | 同一组的忠实性和耗时比较后再决定是否纳入默认链；任务状态、超时和空结果复用已有恢复协议 |

本轮判断不支持“整套换成 MiniMax Design 或再加一套 Agent 框架就能成熟”。更具体的方向是：先把现有系统的工作项真正做成一次可执行输入，让已加载工具和已确定事实在同一任务中保留下来，只有当前问题才回到相应负责人。专业知识必须帮助镜头和媒体变好，不能成为每一次读取、执行和重试都重复解释的长篇制度。
