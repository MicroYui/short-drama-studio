# 决策账本

本文件用于逐项确认 AI 短剧平台的全部未决事项。每次只讨论一个决策；确认后记录结论、理由和对后续设计的影响，再进入下一项。新发现的待办先登记，不在别处形成隐藏决定。

## 状态

- `LOCKED`：用户已明确确认，除非新证据推翻，否则不再反复讨论；
- `TO_CONFIRM`：可以通过产品或工程判断立即确认；
- `EXPERIMENT_REQUIRED`：现在只确认实验与判定规则，最终结论由固定输入结果决定；
- `LATER`：依赖更早的决定或实验结果。

## 已锁定边界

| ID | 状态 | 决策 |
| --- | --- | --- |
| D001 | `LOCKED` | 成片质量、连续性和可控性优先；预算只记录，不主导路线选择。 |
| D002 | `LOCKED` | 即梦、可灵、LTX Studio、纳逗 Pro 只用于学习，不调用、不依赖。 |
| D003 | `LOCKED` | GPT Image 2、MiniMax-H3、Azure Speech 都是能力池；有质量或控制增益就使用，不为凑技术栈强制使用。 |
| D004 | `LOCKED` | 可使用 GPT-5.6-sol 负责规划、结构化制片、提示编译和复盘，但领域工件不绑定模型名。 |
| D005 | `LOCKED` | 入口同时支持无输入自动创作、仅给若干图片、一句话扩写和已有剧本；已有剧本保留原文与执行文本，不静默改写。 |
| D006 | `LOCKED` | 首版是单用户、受信环境；不做注册、登录、RBAC、多租户、团队与分享权限。 |
| D007 | `LOCKED` | 内容批准状态属于生产流程，不属于用户访问权限。 |
| D008 | `LOCKED` | 视觉资产来源中立：可来自素材库、用户上传、GPT Image 生成或 GPT Image 对已有素材的修整，不把“资产包”定义为全部 AI 自生成。 |

## A. 产品与首个验收样片

| ID | 状态 | 待确认事项 | 依赖 |
| --- | --- | --- | --- |
| P001 | `LOCKED` | 首个质量标杆采用真人都市短剧；以真人脸、表演、服装、空间、声线、口型和跨镜连续性验证质量上限 | 无 |
| P002 | `SUPERSEDED` | 历史首版验收曾只使用真人都市短剧验证质量上限；当前范围已由 A014 覆盖，AI 原生短剧不预设真人或动画 Style，各形态继续共用制片主线 | P001、A014 |
| P003 | `LOCKED` | 首版验收单集完整闭环；角色、场景、服装、道具和可选声线仍作为可跨集复用的版本化资产，但跨集连续性不列入首版通过条件 | P001 |
| P004 | `LOCKED` | 首个验收片目标 45–60 秒、8–10 个最终剪辑镜头；固定实验样片先按约 50 秒、8 镜设计 | P001、P003 |
| P005 | `LOCKED` | 采用真人都市悬疑短剧《别相信我》作为首个固定测试剧本：林夏、周衡、雨夜便利店、红色录音笔、五句锁定台词与八镜结构 | P001、P004 |
| P006 | `LOCKED` | 首版内容生成、声音、口型、字幕与 QA 只正式支持并验收普通话中文；schema 保留 `language/locale`，暂不承诺方言和多语言质量 | P003 |
| P007 | `LOCKED` | 默认 `AUTO_DRAFT` 从无输入、图片、一句话或剧本自动运行到完整可观看成片：内部自动完成规划、资产补全、选 Take、QA、导演修复、定向重试和时间线组装，默认只交付一个或少量完整最终版，不要求用户操作中间节点。`GUIDED` 才显示可忽略的建议和停靠点；只有标记正式通过需要人工确认，草稿生成与导出不受阻 | P002、P003 |
| P008 | `LOCKED` | 首版提供“短剧生产时间线”：Take 切换、重生成、Retake/Extend/Gap Fill、拆分/裁切/排序、视频/音频/字幕轨、音量、基础转场、字幕与 9:16 导出；高级调色、蒙版、复杂特效、关键帧曲线和多机位后续研究 | P007 |
| P009 | `LOCKED` | 首版只负责项目生产与导出 9:16 MP4、SRT/ASS、TimelineManifest、项目素材和 QA 报告；不接抖音账号、自动发布或运营管理 | P008 |
| P010 | `LOCKED` | 资产工作台支持本地上传、外部素材库资源导入、GPT 新生成及 GPT 修整已有资产；内置多素材库搜索/下载连接器不作为固定样片实验前置，可在后续按实际库逐个接入 | P003、P007 |
| P011 | `LATER` | 调研并确定首批内置素材库：按角色/服装/场景/道具/图片/视频/音乐/音效分类，核对 API、可商用范围与署名要求；不阻塞固定样片和核心闭环 | P010 |

## B. 底座与系统架构

| ID | 状态 | 待确认事项 | 依赖 |
| --- | --- | --- | --- |
| A001 | `LOCKED` | 不做双底座并行 Spike；只做 LTX Desktop 的供应商中立 Mock 垂直切片，验证 SQLite/API 项目域、可重启任务和 Take→时间线→9:16 导出；仅在出现结构性阻碍时回退 Jellyfish | P008、A004 |
| A002 | `LOCKED` | LTX Desktop 作为首选代码底座与未来 fork 来源；Jellyfish 只借鉴项目域、准备度和持久化任务语义，并在 LTX 结构性失败时作为回退；LumenX 不进入底座实现，仅作模型目录/候选工作台参考 | A001 |
| A003 | `LOCKED` | 保留并扩展 Electron/FastAPI 壳、Gen Space、多 Take、Retake/Extend/Gap Fill、Source/Program Monitor、时间线、媒体导入、SRT/FCP、FFmpeg 框架与类型/性能基础；重构或复用整合 SQLite/API 项目域、持久化任务、供应商中立 Adapter、短剧领域/QA/TimelineManifest 和预览导出一致性；可直接参考或移植其他已审查项目的成熟实现，现有 LTX/fal/Gemini 只作可选 Adapter | A002 |
| A004 | `LOCKED` | 首版本地桌面应用优先；项目、数据库与媒体保存在本机，云端 Provider 仍通过 API 调用；后端保留清晰 API/Worker 边界以便未来迁移，但首版不兼容或双维护服务器/浏览器部署 | P008 |
| A005 | `LOCKED` | SQLite WAL 保存项目/任务元数据，本地项目目录保存媒体，Electron 管理独立本地 Python Worker 从 SQLite 任务表取任务；本地 GPU 默认单任务、云端并发按 Provider 配置；首版不引入 Redis/Celery/MySQL/S3/Kubernetes | A002、A004 |
| A006 | `LOCKED` | 首版延续并扩展 LTX Desktop 工作台：项目、剧本/ProjectBible、资产、分镜/连续性、Gen Space/Take/QA、短剧时间线和 Provider 设置；自动流程用阶段进度与问题提示呈现，不做节点连线画布 | P008、A002 |
| A007 | `LOCKED` | 首版不预接 ComfyUI 或通用节点系统；真实样片出现明确的局部修复需求，且对应窄工作流经实验确认能提升成片质量时再接入 | M012 |
| A008 | `LOCKED` | 采用分离状态模型：产物版本 `DRAFT/APPROVED/SUPERSEDED`，依赖新鲜度 `CURRENT/STALE`，任务 `QUEUED→SUBMITTING→ACKNOWLEDGED→RUNNING→SUCCEEDED/FAILED/CANCELLED` 并处理 `SUBMIT_UNKNOWN/OUTPUT_MISSING`；Take 的可用性、QA 与入选状态彼此独立，TimelineManifest 精确引用版本；上游修改只失效受影响分支，旧产物保留可复现 | A003、P008 |
| A009 | `LOCKED` | Mock 阶段实现完整闭环当前必需的 schema、模块边界、API 与任务状态机；后续实验产生的新需求通过普通 SQLite migration 和接口类型变更演进。Provider 特有参数留在 Adapter 请求/响应记录中，不污染通用领域模型；不设计万能元数据系统，也不等待全部实验结束再冻结结构 | A001–A008、A010、M001–M012 |
| A010 | `LOCKED` | 当前只复用 LTX Desktop 的 Apache 应用代码、编辑壳和媒体能力，不集成、分发或评估 LTX 模型权重；视频生成只接 H3，未来若重新开放模型扩展再单独复核权重 License、硬件和质量 | A002、M001 |
| A011 | `LOCKED` | 视频探测、适应性抽帧、镜内时间序列和相邻镜头边界检查作为 Agent 可调用的通用工具；观察只报告可见证据，Director 给出可覆盖建议，不把建议变成硬门，也不新增单一样片或门场景专用字段 | A006、A008、P007 |
| A012 | `LOCKED` | 真人 `AUTO_DRAFT` 在同一 SQLite/API 核心上采用单步持久化推进和可恢复轮询，复用现有资产/H3 任务、Take、QA 证据和 TimelineManifest；配置缺失、提交结果未知或确有无法自主解决的外部状态时进入 `NEEDS_ATTENTION`，应用重启后可继续。失败次数本身不触发停止 | A005、A008、P007 |
| A013 | `LOCKED` | 视觉资产以 Agent 管理、Tool 执行：结构化分镜需求驱动目录搜索、覆盖检查、同源派生、语义审查、登记和逐镜 ReferencePlan；场景先建 Master Wide，必要时再派生局部视图，三视图不是固定前置。这些是可忽略的 Agent 选择，不是用户硬审批门 | A006、A008、P007、P010 |
| A014 | `LOCKED` | 项目级 `GENERAL_VIDEO / AI_SHORT_DRAMA / DIGITAL_HUMAN / ANIME` 是宽粒度媒介/生产 Profile，共用项目、剧本、资产、H3、QA、时间线与导出实现。Story Architect、Writer、Critic、Director 等先加载中文共享角色，再叠加当前格式重点和 Agent 语义选中的 Narrative Skills；格式 Profile 不枚举爽剧、喜剧、悬疑等题材。`ProjectBible.style` 只保存项目视觉/摄影/声音/材质表达，不代替 `StoryIntent`。证据观察、Schema 和 Provider 技术合同保持品类中立，不硬限定镜头数或信息节拍 | A006、A011–A013 |
| A015 | `LOCKED` | 新项目时长由用户自由填写，AI 短剧默认 5 分钟且 3/5/10/15 分钟仅为快捷值。配置语言 Provider 的 Agent-owned 内容无论时长都共用 `Story Orientation → Story Architect → Scene Writer → 一次完整 Critic → Repair/逐场修订 → 增强 Verifier → 按需 Scene Director`。Critic 的 owner 只形成下游 handoff，不串行调用空 Review Router Agent；增强 Verifier 同时做 Issue 修复验收与脱离修复说明的全稿自然冷读，并直接接受当前稿或继续聚焦修订。聚焦修订后回到 Verifier，不重跑完整 Critic；独立 `FINAL_COLD_READ` 只作旧运行和持久化兼容，不是现行角色阶段。全链路不设固定修订次数、严重度、问题数或超时门槛。`SCRIPT_ONLY` 在 Verifier 接受后停在 Scene Director 之前，不调用 GPT Image 或 H3 | A006、A014 |
| A016 | `LOCKED` | 只要系统负责原创或扩写且配置了语言 Provider，所属 AgentRun 无论目标时长都使用 Content Planning Job；当前 SQLite 与代码兼容名仍为 `PlanningRun`，只持久化内容规划游标和恢复所需中间工件。它一次推进 Story Orientation、Story Architect、一次完整 Critic、Repair、增强 Verifier 中的一个节点，或一场 Scene Writer/修订 Writer/Director；应用重启从当前节点继续。增强 Verifier 结合 Issue 修复证据和脱离修复说明的全稿自然冷读直接接受或继续聚焦修订；新计划不进入独立 `FINAL_COLD_READ`。`project.create` 先建立同一项目 ID 的 INPUT scaffold，FINALIZE 再原位更新唯一 ProjectDocument；不带 owner 的兼容创建只做本地 scaffold，语言 Provider 不再提供同步整稿入口。内容作业不设固定总超时或修订轮次上限；规划阶段不调用 GPT Image 或 H3 | A005、A012、A015、A020 |
| A017 | `SUPERSEDED` | 历史实现曾以问题数或 HIGH 增加条件触发 Revision Judge，也曾把脱离修复说明的通读拆成独立 `FINAL_COLD_READ`。当前 Critic Issue Ledger 跟踪同一语义问题的持续、变形、回归、修复证据与已接受取舍；完整 Critic 只运行一次，增强 Verifier 在同一阶段完成修复验收与全稿自然冷读，直接接受或继续聚焦修订。问题数、严重度和轮次不触发另一个审校 Agent；`FINAL_COLD_READ` 类型只作旧运行/持久化兼容 | A015、A016 |
| A018 | `SUPERSEDED` | Phase 19 曾由 `PlanningRun.FINALIZE` 建立 `AutonomousRun`；Phase 29 已由 AgentRun、动态 PlanRevision 与共享 Tools 取代并删除该旧控制面。2 分钟中途重启导出与 5 分钟 20 镜/40 候选离线验证仍是底层资产、H3、QA、Take、TimelineManifest 和导出能力的历史证据，不代表当前仍使用 AutonomousRun | A005、A012、A016、A020 |
| A019 | `LOCKED` | 多分钟 TimelineManifest 使用 Scene Reviewer → Episode Reviewer 分层汇总现有入选 Take、VideoInspection 与锁定剧本，并保存关联场次/镜头的可忽略报告。只有明确关联镜头的建议才交给现有 Director；应用修复后复用原 Route/H3/QA/Timeline 链路。Reviewer/Provider 缺失或失败时保留报告与错误证据，由 Agent 决定重试、换路线或带明确未完成项继续，不用固定失败次数代替判断。该阶段不自动运行声音 QA，也不增加整集评分门、固定节拍、答案键、场景专用字段、hash 冻结或第二运行时 | A011、A012、A018 |
| A020 | `LOCKED` | 产品控制面采用 Agent Harness + Markdown Production Skills + 可组合 Typed Tools。宽粒度 `ProductionProfile → Format Skill`、中文共享角色与开放目录 Narrative Skills 组成最小文件化 Skill 基础；Narrative Skill 可声明支持角色，由 Agent 显式语义选择，Loader 只校验 ID/角色支持，不保存题材枚举或自动触发字段。Skill 不保存项目案例经验，也不限制 Tool Catalog。通用视频、AI 短剧、数字人口播和动漫共享项目/任务/资产/Take/时间线核心；具体 Style 是可修改的 ProjectBible 工件。AgentRun 与 PlanRevision 负责总编排；Content Planning Job（兼容实现名 `PlanningRun`）和 Provider Task 只保留各自必要的可恢复游标，当前不抽象通用 `ChildJob`；旧 AutonomousRun 已删除 | A006、A008、A011–A019 |
| A021 | `LOCKED` | 语义修改采用 `ChangeIntent → 按需 ProjectIndex → ImpactPlan → 现有 Tools/任务执行`。LLM 理解目标和范围，确定性查询校验真实 ID 与依赖闭包，只重算受影响分支；不增加图数据库、第二状态源或换衣服等单例 workflow。只有歧义造成明显不同范围时才询问用户 | A008、A013、A020 |
| A022 | `LOCKED` | Prompt、参考绑定、镜头拆法、检查和重试策略若从实际制片中显示出清楚的质量或生成次数改善，可直接沉淀并继续，不要求逐项完整实验；只有默认路线不明、跨项目结论、显著成本或架构/Provider 变化才做受控比较 | A009、A011–A020 |
| A023 | `LOCKED` | 系统负责原创或扩写时，Story Orientation Agent 先从完整用户输入形成 `StorySourceContract` 和 `StoryIntent`，再语义选择概念放大、递进兑现、荒诞喜剧或其他 Narrative Skills。高概念、可见爽点、严谨逻辑或无厘头可以是具体项目选择，但都不是 AI 短剧 Profile 默认模板。用户给定题材、标志性反差和目标体验时优先保留；选择不使用关键词、正则、题材白名单或审美门控 | A014、A015、A020、A022 |
| A024 | `LOCKED` | 通用 schema/校验器不限制角色年龄、性别、关系、题材、结局或正常创作元素；只保留引用正确性、数据规模和 Provider 技术合约等工程边界。儿童、老人和题材需要的非人角色均可规划；Provider 特有限制由 Adapter 报告，不进入通用故事模型 | A009、A020、A023 |
| A025 | `LOCKED` | `ShotPlan` 是剪辑/叙事单元，时长可短于 4 秒或超过 15 秒；MiniMax-H3 只允许单次 4–15 秒整数请求。H3 Route Planner 后续把长镜头编译为连续生成段、把短镜头生成安全余量后由 Timeline 裁切，并对段间边界做连续性 QA。Provider 窗口不强迫 Director 切镜，拆镜/换机位仍是可忽略建议；真实视频阶段恢复前完成一镜多段执行与拼接 | A009、A011、A018、M001 |
| A026 | `LOCKED` | Script Critic 以目标观众身份对照 `StoryIntent`、`StorySourceContract` 和完整可读剧本做一次全稿诊断，为同一语义问题保留稳定 `issue_id`，并在 Critic Issue Ledger 中累积分类、状态、文本/修复证据、演变、已接受取舍和下游 handoff。`requires_revision` 是 Critic 对当前文本问题的语义判断，随后统一交给 Repair Planner 诊断实际修订范围；owner 只用于责任层交接，不再串行调用 Review Router Agent。增强 Verifier 验收修复并对全稿自然冷读，直接接受或继续聚焦修订。严重度、问题数、关键词和轮次都不构成代码门槛 | A014–A017、A020、A024 |
| A027 | `LOCKED` | `StorySourceContract` 保存用户承诺/禁区/开放选择/权威覆盖，`StoryIntent` 保存观众体验/戏剧引擎/推进策略/调性可信度/受保护元素/偏移风险，并随大纲和 ScriptVersion 版本化保存。`SCRIPT_LOCKED` 普通文本由 Story Orientation Agent 语义结构化为 `locked_dialogue`；调用方也可直接提交结构化条目。代码只校验逐字、顺序和调用方给定结构，不用正则或关键词推断 speaker/旁白。Story Repair Planner 不直接把 Critic 当答案，而是诊断来源概念缺口、当前剧本缺陷或 Critic 越界。Writer 只逐场执行应改项；增强 Revision Verifier 既检查修复证据、受保护承诺与新回归，也脱离修复说明自然通读当前全稿，随后直接接受或继续聚焦修订。用户后续指令作为 authoritative override。修订与 Verifier 不设固定次数、问题清零目标或超时阈值 | A015–A017、A020、A026 |
| A028 | `LOCKED` | 约 4 秒的单节点内容到数十分钟剧本共用 `Story Orientation → Global Blueprint → Scene Writer → 一次完整 Critic → Repair/逐场修订 → 增强 Verifier → 按需 Scene Director`，时长只改变场次节点数和上下文量。场景总数与跨场修订范围不设 40/12 这类低常数创作上限，长任务按蓝图、场次和修订单元持久化推进。Blueprint、单场写作、版本化剧本检索、修改范围建议、Scene Patch 和确定性合并均为可独立调用的 Tools；系统负责原创或扩写且配置了语言 Provider 时始终建立 Content Planning Job，AgentRun 决定何时推进，当前 `PlanningRun` 只保存顺序内容规划游标和恢复工件。Repair Planner 优先只修改关联场次；增强 Verifier 直接接受或带具体证据继续聚焦修订。全局契约真正变化时先更新 Blueprint 元数据，随后仍逐场持久化修订，用户可显式覆盖范围建议 | A020、A026、A027 |
| A029 | `LOCKED` | H3 最终提示由模型专属 `H3PromptCompilerSkill` 编译为官方 T2VA/I2VA/FL2VA/Ref2VA Prompt IR；可选 GPT 编译器只读取当前 Profile 的 Video Prompt Compiler rubric，并忠实翻译锁定镜头与媒体绑定，不作为第二个导演。Route Tool 暴露当前镜头技术上合法的全部候选和类型化 QA/观察证据，不以 top-k、`PASSED`、分数、候选序号或失败类别预先截断。Route Agent 显式选择候选 ID、参考媒体 ID 和本批生成意图，决定复用 Take、生成一个或多个候选、继续探索或回到 Director。`CompareTakesTool` 做相对比较并允许 `NONE_ACCEPTABLE`，其报告仍是 Agent 可采纳或覆盖的证据 | A011、A012、A020、A022、M001 |
| A030 | `LOCKED` | 视频质量与低重试采用“Agent 选择最小充分 H3 路线 → 生成前参考视觉重排/关键帧相对比较 → 检查实际候选 → 带时间证据的类型化诊断 → 改变最相关控制变量 → 只重算受影响分支”。三视图、场景九宫格、机位预演、reference video 和候选探索方式均由 Agent 按镜头决定，不成为固定 workflow 或硬门；只有 H3 时长、输入互斥、媒体数量等 Provider 技术合约阻止非法提交。通用失败经验进入可检索、带来源与置信度的 H3 Capability Playbook，保持用户可覆盖，不增加样片专用 schema、节点图或 hash 冻结 | A020–A022、A025、A029、M014 |
| A031 | `LOCKED` | 生成前视觉质量以三个独立 Agent Tool 实现：项目隔离的参考视觉重排、允许 `NONE_ACCEPTABLE` 的关键帧相对比较、以及“先独立观察再与镜头意图比较”的输入兼容性建议。三类报告持久化但不自动批准媒体、不修改 ReferencePlan、不选择 Route、不提交 H3，统一保持 `advisory_only/proceed_allowed`；候选窗口由 Agent 和上下文决定，单次多模态工程上限不解释为每镜固定生成数 | A013、A020–A022、A029、A030 |
| A032 | `LOCKED` | 角色 Prompt 按 `共享中文角色职责 + 宽粒度 Format Skill/Playbook + Agent 选中的 Narrative Skills + StoryIntent + 相关 ExperienceMemory + 当前任务数据` 组合，Planner 另外读取中文共享端到端流程和 Tool Catalog。编译器只做 Skill 存在性和角色支持校验，不代替 Agent 做叙事选择。专业委派的 `assigned_agent` 只表示接收者身份；Production Manager 依据完整任务语义显式选择 `production_skill_role`，编译器只按该字段装载职责，不从名称或文本关键词推断。项目执行经验不写入格式 Skill，而由 AgentRuntime `ExperienceMemory` 按 project/skill scope 与证据召回。Scene Director、资产、H3 Route/Prompt Compiler 和 Reviewer 按职责读取 Skill；视频抽帧、Take 比较和视觉输入观察保持中立。所有建议可覆盖，不增加第二状态源或固定 workflow | A014、A020、A026、A029–A031 |
| A033 | `LOCKED` | `StorySourceContract` 保存用户承诺/禁区/开放选择，`StoryIntent` 保存叙事体验与调性，`EndingContract` 保护本集最终状态、结尾意图与续集策略，`ProjectBible.style` 保存视觉/摄影/声音/材质表达；四者不相互代替。`EndingContract` 不是要求 Writer 把每个 closure 分别演成认可、授予、核验或交接仪式。四视图或群像轻量资产、场景 coverage、时间线分镜和 H3 真实 `@` 媒体绑定分别属于资产、导演与 Provider 层，不注入共享 Story Architect 作为剧情规则 | A013、A014、A020、A029–A032 |
| A034 | `LOCKED` | 专业 Agent WorkOrder 以 `specialist_turns` 持久化 Tool 请求、理由、状态和结果；一次外部 `execute` 只做一次模型决策或执行一个已持久化 Tool，不在请求内隐藏循环，也不设置固定 Tool 次数上限。重启时 `IN_PROGRESS` turn 标为 `INTERRUPTED`，WorkOrder 回到 `NEEDS_ATTENTION`；Agent 读取规范项目状态和中断证据后决定对账、重试或换路线，Harness 不自动重放 | A005、A008、A020、A032 |
| A035 | `LOCKED` | 蓝图场次秒数和总和只是规划分配，不是实际成稿能自然演完的证明。Writer 和修订 Writer 输出 `performance_timing_assessment`，一次完整 Critic 与增强 Verifier 按对白、动作、停顿、反应和观众读清信息的实际顺序复核；Verifier 的复核属于其脱离修复说明的全稿自然冷读。只有文本明确是同时发生、蒙太奇或可连续掠过时才按并行处理；证据交给 Agent 做取舍、修订或重规划判断，不转化为字数、语速、场次数或时长偏差的代码阈值 | A015、A016、A026–A028 |
| A036 | `LOCKED` | Writer、Critic、Repair 和增强 Verifier 逐场继承已建立的人物关系、群体态度、资源与权限。换地点、见证者、道具，或把口头认可换成文件、挂牌，不自动构成新升级；后续场需要产生新选择、代价或后果。`EndingContract` 保护本篇最终状态而非仪式清单；多个 closure 若只证明同一次状态变化，由最有力的决定性动作统一兑现。这是 Agent 的通用叙事判断，不是固定节拍或题材模板 | A023、A026–A028、A033、A035 |
| A037 | `LOCKED` | H3 Route Plan 的候选执行必须来自 Route Agent 或调用方的显式决定：Route Tool 暴露全部技术上合法的 Direct、Frame、Reference、Video Guide 和可复用 Take 候选，Agent 返回候选 ID、参考媒体 ID 与本批生成意图，可选一个或多个候选。同一 Route Plan 重复执行保持幂等；继续探索由 Agent 建立新 Route Plan，任务状态、QA、分数或重试谱系不会自动追加候选。无显式决定时保持 `NEEDS_AGENT_DECISION/PARTIAL`，技术基线不自动提交或计费 | A011、A012、A020、A029–A031、M001 |

## C. 图像、视频、音频与后期路线

| ID | 状态 | 待确认事项 | 依赖 |
| --- | --- | --- | --- |
| M001 | `LOCKED` | 当前视频生成模型只有 MiniMax-H3；本阶段不做视频模型候选、选型或横向比较，所有视频路线实验都在 H3 内进行 | P001 |
| M002 | `EXPERIMENT_REQUIRED` | GPT Image 2 是全量视觉资产层还是仅覆盖高风险镜头 | P001、E002 |
| M003 | `EXPERIMENT_REQUIRED` | H3 Direct、Frame route、Reference route 的适用镜头规则 | M001、E003 |
| M004 | `EXPERIMENT_REQUIRED` | 是否使用 H3 Context-IR，以及它相对自编译提示是否有增益 | M001、E003 |
| M005 | `LOCKED` | PoC 的所有 H3 视频请求固定使用 768P，不做 1080P/2K 生成或分辨率对照；单次请求严格遵守 4–15 秒整数技术合同。Route Agent 从 Tool 暴露的全部合法候选中显式选择 ID 和本批生成意图，根据镜头、已有结果和实际证据决定提交一个或多个、复用、换输入或换路线；代码不因“首条可用”、QA 状态或普通/高风险配额自动停止。无显式决定时保持 `NEEDS_AGENT_DECISION/PARTIAL` | M001、E003、A029、A037 |
| M006 | `EXPERIMENT_REQUIRED` | 原生人声、reference audio、真人录音和外部 TTS 的优先级 | M001、E004 |
| M007 | `LOCKED` | STT 默认用于原生/录制音频的台词 QA 与字幕时间候选，具体 Provider 可替换；最终以锁定台词、人工听审和画面共同判定 | E004 |
| M008 | `EXPERIMENT_REQUIRED` | 不预接外部口型模型；只有 H3 原生、声线参考及必要的外部主音频路线仍无法通过正脸/侧脸/多人对白时，才启动候选工具实验 | M006、E004 |
| M009 | `LOCKED` | 字幕计时优先级：可靠 Provider 对齐数据；外部 TTS 使用 word-boundary；其余原生/录制音频用 STT 对齐；不准时允许人工微调并回写 TimelineManifest | E004、M007 |
| M010 | `EXPERIMENT_REQUIRED` | 独立镜头与模型内多镜的分组规则 | M001、E005 |
| M011 | `LOCKED` | H3 源视频保持 768P；交付预设为 1080×1920 9:16 H.264 High/YUV420P/BT.709/CRF18 MP4、AAC-LC 48kHz 192kbps，默认 30fps CFR但避免损伤性改帧；输出带/无字幕 MP4、SRT、ASS、TimelineManifest，安全区上/左8%右16%下20%，约 -14 LUFS/-1 dBTP，并做 FFmpeg/ffprobe 技术 QA | P008、M005 |
| M012 | `LATER` | 局部图像修复、补帧、超分、降噪等后处理是否值得加入 | E003–E005 |
| M013 | `LATER` | LTX-2.x 或其他视频模型候选不进入当前范围；只有未来明确重新开放模型扩展时才讨论 | M001 |
| M014 | `LOCKED` | H3 `reference_video` 作为 Director/Route Agent 的可选工具，不是连续镜头默认前置。真实样本中，前序完整 Take 与尾窗会重演可见动作；主要在同动作、相近覆盖的续接镜头中候选。反打/换机位优先比较 Frame，机位预演优先从当前镜头批准首帧建立；二维预演不能保证人体走位或终态动作。用户可忽略建议，缺少 guide 不阻断生成 | M001、M003、M010、E010 |

## D. 固定实验与质量判定

| ID | 状态 | 待确认事项 | 依赖 |
| --- | --- | --- | --- |
| E001 | `LOCKED` | 固定使用《别相信我》的角色、雨夜便利店、红色录音笔、五句锁定台词和八镜表作为首轮实验输入 | P001–P005 |
| E002 | `LOCKED` | 资产来源不限定；GPT Image 实验比较独立生成、角色参考、角色+场景+上一镜状态、连续编辑四种方式，每条件至少 5 次并盲评；用镜头 2/3/5/8 验证下游 H3，只有动态质量不降且身份、空间、道具、构图或稳定率至少一项提高才通过 | E001 |
| E003 | `LOCKED` | 固定 768P 比较 H3 Direct、首帧、首尾帧和 Reference 合法路线；Frame/Reference 互斥并在提交前校验。先用镜头 1/2/5/8，再扩展八镜；对代表样本比较 GPT-5.6-sol 自编译提示与 Context-IR，最终按镜头类型记录路线；PoC 不做更高分辨率实验 | E001、M001 |
| E004 | `LOCKED` | 先验证 H3 原生音频的五句逐字正确、数字/否定词、跨镜声线、说话人和正/侧脸口型；声线漂移再测 reference audio，仍不合格再比较真人录音/外部 TTS。STT 只做 QA/时间候选，字幕按 Provider→TTS boundary→STT→人工微调取时；外部口型最后才评估 | E001、M001 |
| E005 | `LOCKED` | 比较 8 镜独立、相邻两镜 `1+2/3+4/5+6/7+8`、表演段落 `1/2+3/4+5/6+7/8`、独立镜头+连续性帧四组；多镜不超过 H3 15 秒，只有连续性/表演增益且不显著扩大严重错误与返工范围时才按组采用 | E001、M001 |
| E006 | `LOCKED` | 需要决定跨项目默认路线时，用足够的代表样本比较严重失败、可用率和对白正确性；接近或波动则继续采样，仍不稳定记 `UNKNOWN`。日常生产不等待固定样本数，也不默认“先生成一条”或“先取得两个通过候选”；Route Agent 基于当前镜头、路线、已有候选和实际证据显式选择本批生成一个或多个、继续探索或换控制变量。Take 的最终采用由 Agent 结合实际观察与当前 Production Skill 判断，不由 QA 布尔值或候选数量自动放行 | E002–E005、A029、A030、A037 |
| E007 | `LOCKED` | 在需要比较自动闭环或路线质量时，使用同输入、同模型与相同可控条件的独立运行，并可结合隐藏条件的成对/盲评和具体失败分析。样本量由当前不确定性决定；可播放时间线、完整 trace、实际观看体验及人工重建比例分别报告，不用固定运行次数、硬通过比例、评分中位数或问题清零作为产品运行门禁 | E006 |
| E008 | `EXPERIMENT_REQUIRED` | 各镜头类型的默认 RouteEvidence | E002–E007 |
| E009 | `LATER` | 视频模型横向比较不进入当前范围；未来开放 M013 时再设计 | M013 |
| E010 | `LOCKED` | 已在同一 7 秒反打镜头、同资产和 768P 下比较 Frame 基线、前序完整 Take、前序尾窗、前镜尾帧预演与当前镜头批准首帧预演。完整 Take/尾窗均重启行走；前镜尾帧预演重置距离；当前镜头首帧预演保住反打、人物、场景和道具且未重复前镜，但仍未完成结尾起步动作。结论只形成 Agent 可覆盖路线策略，不设固定前置 | M014、A011、A013 |
| E011 | `EXPERIMENT_REQUIRED` | 在后续正常质量积累中复测真正同动作/同覆盖的续接、长镜头 Extend、多人遮挡与快速手物接触，统计完整 Take、尾窗和当前镜头首帧预演的跨剧本稳定率；不为扩大样本而阻塞当前完整流程 | M014、E010 |

## E. 实施与验收

| ID | 状态 | 待确认事项 | 依赖 |
| --- | --- | --- | --- |
| I001 | `LOCKED` | 以 LTX Desktop Git 历史为工程根，不嵌套 vendor 仓库；保留 frontend/electron/backend 骨架、桌面生命周期、媒体、Gen Space/Take/Retake/Extend/Gap Fill、监视器、时间线、SRT/FCP、FFmpeg 和测试构建。新增 domain/application/db/adapters/workers、前端短剧 features 与固定 fixture；替换 localStorage、内存槽、LTX contract 和非 Manifest 导出，禁用 LTX/fal/Gemini、模型下载/LoRA 等无关入口，不保留第二项目格式或运行时 | A002、A003、A010、I002 |
| I002 | `LOCKED` | 基于固定 LTX Desktop 快照，用 SQLite WAL/API 取代 localStorage 项目真相源；以《别相信我》fixture 和可注入失败的 Mock Provider 走通最小领域、持久化任务/重启/对账、多 Take/QA、失效传播、Retake/Extend/Gap Fill、时间线及带/无字幕确定性导出。不上真实 Provider、素材库、权限或最终视觉；仅当统一状态源或 TimelineManifest 无法合理接管现有壳时回退 Jellyfish | A001–A008、E001 |
| I003 | `LOCKED` | 按九阶段实施：固定并验证 LTX Desktop 上游基线；建立 SQLite/API 领域核心与 fixture；实现持久化 Worker、Mock Provider、恢复和失效传播；接通现有 Gen Space/Take/Retake/Extend/Gap Fill/时间线/导出并通过 I002；再接唯一视频模型 H3 768P 并执行 E003/E005；随后接资产工作台和 GPT Image 执行 E002；验证 H3 原生声音后按需接参考音频、TTS/STT/外部口型并执行 E004；接 GPT-5.6-sol 自动规划、审片、路由和局部重试并完成三次 E007；最后才依据证据决定 ComfyUI、内置素材库等增强。每阶段通过闭环测试后再进入下一阶段，界面精修放在真实路线稳定之后 | M001–M012、E001–E008 |
| I004 | `LOCKED` | 六层验证：单元测试覆盖状态机、失效、版本、时间码、字幕、Prompt/路线和 Manifest；临时 SQLite 加真实 Worker 的集成测试覆盖成功、取消、重试、重启、SUBMIT_UNKNOWN、OUTPUT_MISSING、换 Take 与局部失效；固定 fixture 加 Mock Provider 的桌面 E2E 走完整八镜并中途重启；ffprobe/FFmpeg 检查交付规格及预览/Manifest/导出一致性，黑帧/冻帧/静音仅作诊断；离线 fixture 做 Provider 合约测试，各启用模式做最小真实冒烟；最终质量严格执行 E007。每次提交跑快速测试，阶段出口跑完整 Mock E2E 和媒体检查，真实 Provider/盲评独立运行；上游基线问题与新增回归分开记录 | I002、I003 |
| I005 | `LOCKED` | 产品、架构、技术设计、实验方案与实施计划按普通工程方式持续维护；Git 自然保留历史，不设置额外冻结层级、文档 SHA 锁定、审批门或证据链。只清楚标记已确认决定和待实验结论，实验完成后直接更新相应文档与实现 | 全部前置项 |

## 实施证据

- 2026-08-14：I001–I004 的供应商中立 Mock 部分已落地并通过 Phase 1 验收。SQLite/API、持久化任务与恢复、16 个 Take、局部失效、Retake/Extend/Gap Fill、50 秒 TimelineManifest、带/无字幕导出和桌面交互闭环均已验证；详见 [Phase 1 实施记录](10-phase1-mock-vertical-slice.md)。
- 2026-08-15：H3 768P Provider 工程与真实合同冒烟均已落地。Direct/Frame/Reference capability 校验、AceData Adapter、持久化提交/轮询/取消、`SUBMIT_UNKNOWN` 对账、`OUTPUT_MISSING` 恢复、原始 Take 下载、用量/价格、人工 QA 和桌面实验台通过验证。首条 5 秒 Direct 样本完成远端创建、下载和人工听看，RouteEvidence 为 `REVIEWED / HARD_FAIL / 3.0`：声音无明显问题，店招乱码且红色录音笔变成对讲机；详见 [Phase 2 实施记录](11-phase2-minimax-h3-provider.md)。
- 这项结果关闭的是 H3 Provider 与真实媒体闭环门，不改变 M002–M004、M006、M008、M010 和 E008 的 `EXPERIMENT_REQUIRED` 状态；合同冒烟不构成 Direct 质量通过，Frame/Reference、锁定对白、跨镜声线、口型和镜头分组仍必须由批准资产下的多样本实验决定。
- 2026-08-15：Phase 3 已批准林夏、周衡、便利店、红色录音笔及 S01/S02/S05/S08 镜头关键帧正式版本，并验证 Azure GPT Image 2 多图编辑链路。Phase 4 首轮人工审片得到 S01 F1 5/5、S02 Reference 5/5、S05 F1 5/5、S08 原 F1 3/5；前三者是候选首选路线，S08 原方案因动作完成度波动不能作为稳定默认。GPT-5.6-sol 已按 Toonflow、ViMax、LTX Desktop 的成熟机制接入可见资产、实际媒体绑定、上一镜终态、动作拍数和头尾安全区，八镜头均通过无提交 H3 768P 请求预检。E008 仍保持 `EXPERIMENT_REQUIRED`，直到候选路线达到样本门槛、其余镜头有通过 Take，并完成单集最终人审；详见 [Phase 4 实验记录](13-phase4-h3-visual-route-experiment.md)。
- 2026-08-17：S07 新 F1 已由用户完整播放通过并写入 `RouteEvidence`。S08 的多轮失败表明“门把手数量”不足以约束三维关系，交互计划已增加部件所属表面、刚性附着与持续接触；GPT Image 编辑新增可选 Azure PNG 蒙版，并在供应商返回后确定性恢复蒙版外主画布像素。新首帧与 v9 尾帧均已批准。结构化交互会进入 H3 提示词，但不再用“松手”等自然语言关键词作硬校验；语义疑点只作为 Agent 提醒，用户仍可编辑或跳过。
- 2026-08-17：S08 新 F2 自动技术门槛通过但人工硬门槛失败，用户指出人物僵硬、画面观感差且离店方向错误。复盘认定“推门”是本次导演计划文本错误，人物僵硬主要属于路线与首尾帧适配问题；不为此新增门或区域穿越专用字段。导演计划已改为“拉门、转身、跨向店外”，当前默认建议拆成两镜，仍允许用户修改或忽略。
- 2026-08-17：Phase 5 已完成四种输入归一化、当时的 `AutonomousRun`、适应性抽帧、镜内/镜间接触表、语义观察和观察结果到 Director Agent 的通用连接。观察结论是可忽略建议，不把单一样片的门、方向或动作词固化成底层领域字段。`AutonomousRun` 后续已由 A018/A020 的 AgentRun、动态 PlanRevision 与共享 Tools 取代；详见 [Phase 5 实施记录](15-phase5-autonomous-runner-and-video-observation.md)。
- 2026-08-17：Phase 6 已完成证据优先的 H3 制片路线。系统在不新增 H3 请求的情况下自动复用 9 条当前版本、非合同冒烟且人工通过的真实 Take，运行逐镜和镜间观察，重建 57.13 秒全 H3 `TimelineManifest`，并完成 1080×1920 带/无字幕技术导出。该结果证明复用型真实执行闭环，不代表 E007 已完成；仍需从零项目的三次独立 `AUTO_DRAFT`、整集人工观看和匿名盲评。详见 [Phase 6 实施记录](16-phase6-real-route-planner.md)。
- 2026-08-17：Phase 7 已完成可恢复的真人 `AUTO_DRAFT` 第一闭环。离线 Fake Provider 从空输入完成核心资产生成与语义检查、镜头关键帧、H3 异步候选、进程重建恢复、镜内/镜间观察、当时的自动候选裁判与 Director 有界修复、Take 选择、TimelineManifest 和导出；通用构图失败会使旧 Take 失效、自动修改镜头意图并重新规划，缺少配置时则持久化为 `NEEDS_ATTENTION`，不会回退 Mock。自动候选裁判和计数式 Director 修复是历史实现，已由 A029/A034/A037 的显式 Agent 选择与语义停止取代。后端全量 641 项测试、Pyright、TypeScript 和 Vite 生产构建通过。该结果是工程闭环证据，不是新项目真实画面、对白或声线质量证据；详见 [Phase 7 实施记录](17-phase7-real-autonomous-draft.md)。
- 2026-08-18：Phase 8 完成第一次真实从零真人 `AUTO_DRAFT`。锁定剧本在真实 GPT Image 2、GPT-5.6-sol 和 MiniMax-H3 768P 下自动得到 9/9 已选 Take、35 份视频观察、54.31 秒 `manifest-v3` 成片和全部通过的技术导出。真实运行修复了关键帧主参考选择、批准资产身份对照、生成/QA 参考分离、画外状态提示投影和画外证据判定等通用缺口；最终 S04 没有身份漂移或道具误入，仅保留中等级景别建议。用户已按本轮整体可用标准将成片暂时通过，从锁定剧本到最终人工验收的 Phase 8 流程因此闭环。E007 仍待逐字声音 QA、另外两次独立运行和三人盲评；详见 [Phase 8 实施记录](18-phase8-real-autonomous-validation.md)。
- 2026-08-18：Phase 9 已把音轨提取、信号诊断、字幕时间、锁定对白转写和跨镜声线比较实现为 Provider 中立的 `AudioInspection`，并接入 `AUTO_DRAFT`、SQLite、Product API、QA 工作台和 Director 建议。使用现有 Phase 8 `manifest-v3` 复检而未新增 H3 请求，得到 9/9 音轨技术通过、5/5 繁简体与标点归一化后逐字一致、林夏和周衡声线检查均通过，问题/告警为 0。该结果完成本片 E004 音频证据，但不把单次结果外推为跨剧本稳定率；E007 仍待另外两次独立运行和三人盲评。详见 [Phase 9 实施记录](19-phase9-audio-dialogue-qa.md)。
- 2026-08-20：Phase 10 新增固定前期制片包的独立重复入口，并以真实 GPT Image 2、GPT-5.6-sol 和 MiniMax-H3 768P 再执行 Run 2 / Run 3。两次都自动完成关键帧、H3 候选、长镜头/镜间抽帧、当时的 Director 有界修复、9/9 选 Take、`TimelineManifest`、音频 QA 和 54.31 秒 1080×1920/30fps 导出；五句锁定对白和林夏/周衡跨镜声线均通过自动检查。Director 有界修复只描述该次历史执行，当前已由 Agent 根据证据决定返工范围、路线与停止。加上 Phase 8 Run 1，三版已生成匿名盲评包；自动结果中至少 2/3 没有高严重度入选问题。E007 尚未最终通过，剩余至少三人匿名盲评与最终片选择。详见 [Phase 10 实施记录](20-phase10-independent-repeats-and-blind-review.md)。
- 2026-08-20：用户选择不填写数值 CSV，直接提交三版完整观看后的汇总定性结论：A 的首镜有竖向接缝造成局部空间不连续；B 的录音笔播放被错误表现为女主本人说话，属于声音来源语义失败；C 未发现明确严重问题，并选为当前最终版。本轮产品验收据此完成，至少 2/3 未被判为整片严重失败；没有收集三名独立评审、维度分数或继续观看意愿，因此不宣称完成三人评分 benchmark。B 的问题保留为通用已知缺口，不增加录音笔专用字段。详见 [Phase 10 实施记录](20-phase10-independent-repeats-and-blind-review.md)。
- 2026-08-20：Phase 11 将可上传或麦克风录制的 2–15 秒角色人声规范化为 H3 可用 WAV，并以项目级可选绑定接入资产工作台、独立重复和 Route Planner。实际有该角色台词的镜头可自动选择带 `reference_audio` 的 Reference 候选；未绑定时继续使用 H3 原生声音，Frame 修复路线也可因画面质量不采用声线输入。声线版本会使旧路线与旧任务产物失效。用户明确本阶段只支持输入，不新增声音 QA；详见 [Phase 11 实施记录](21-phase11-optional-character-voice-input.md)。
- 2026-08-20：Phase 12 已实现跨项目资产目录、分镜结构化 `reference_needs`、场景 Master Wide、同源 GPT Image 派生、母版系谱、资产覆盖检查和逐镜 `ReferencePlan`，并将搜索、导入和覆盖检查暴露为 Product API。真实《明天别来了》运行生成 4 个资产包、18 个登记视图和 36 个 H3 768P Take，Director 自动拆分高风险交接镜头，最终形成 8 个入选 Take、6 条锁定字幕和 58.00 秒成片。详见 [Phase 12 实施记录](22-phase12-cross-script-asset-agent.md)。
- 2026-08-21：Phase 13 已在项目创建、持久化、独立重复、Planner、Director 和桌面创建页贯通 `GENERAL_VIDEO / AI_SHORT_DRAMA`。两套策略共用同一 Agent Runtime；旧项目默认按抖音短剧读取。规划 Schema 移除固定 6–10 镜限制并允许无对白，仍保留 45–60 秒交付目标和 H3 单段 4–15 秒技术边界。本阶段只做本地/Fake Provider 工程验证，未调用付费生成资源。详见 [Phase 13 实施记录](23-phase13-production-profiles.md)。
- 2026-08-21：历史 Phase 14 将新项目改为 15 秒到 30 分钟任意目标时长，抖音短剧默认 5 分钟并保留可选快捷值。当时超过 90 秒才进入分层路径，Script Critic 还有最多两轮自动修订上限；这两项已被当前 A015–A017 的统一 Content Planning Job 和 Agent 语义收敛取代。两次 5 分钟真实语言探针只检查剧本，未调用 GPT Image 2 或 MiniMax-H3；第二次实际完成一轮审阅修订并在复审中继续暴露证物逻辑问题，因此没有把单样本写成稳定质量结论。详见 [Phase 14 实施记录](24-phase14-long-form-multi-agent-planning.md)。
- 2026-08-21：历史 Phase 15 已把长篇 Story Architect、Script Critic、当时的有界修订和逐场 Scene Director 从一次同步创建拆为 SQLite 持久化 `PlanningRun`。前端展示当前节点与场次进度，重启后自动发现未完成 run，错误只重试当前节点；短片同步创建当时保持兼容。有界修订与短片同步整稿入口后续已被 A015–A017 的统一 Content Planning Job 和 Agent 语义收敛取代。120 秒真实 GPT-5.6-sol 探针在第 1 场后重建 Service，仍完成 3 场、16 镜和 8 句原样锁定台词；最终 Critic 仍保留 6 项建议，因此只证明可恢复检查闭环，不宣称剧本质量通过。真实探针同时促使单场校验前移“参考需求必须引用整集资产契约”，并修复说明标题、说话动作和对话对象被误识别为说话人的问题。运行结束时图像、视频与 Provider task 均为 0，未调用 GPT Image 或 MiniMax-H3。详见 [Phase 15 实施记录](25-phase15-recoverable-long-form-planning.md)。
- 2026-08-21：Phase 16 已将每轮 `EpisodeOutline` 和 `ScriptReviewReport` 作为独立 PlanningRun artifact 持久化，新增单 run 详细历史和跨项目剧本评测 API，桌面剧本页显示三轮修订轨迹。数量变化只作描述，不作剧本分数或强制门；最新轮比上一轮报告更多问题时，Director Agent 只建议比较版本。220 秒 IDEA_EXPANSION、470 秒 AUTO_ORIGINAL 和同输入策略复跑的三个真实 GPT-5.6-sol 样本均保存 3 份大纲与 3 份审稿，并在 Scene Director 前停止，未调用 GPT Image 或 MiniMax-H3。结果显示修订报告可非单调变化，因此不声称“最新版必然最好”或稳定质量提升。详见 [Phase 16 实施记录](26-phase16-script-quality-history.md)。
- 2026-08-24：Phase 17 在非单调回退时加入独立 `REVISION_JUDGE` 节点，持久化推荐、依据、取舍和置信度，并明确保持 `advisory_only`。集成测试覆盖正常改善不调用、回退触发、跨重启保留、推荐旧稿但仍使用最新版，以及 Judge 失败不阻断。复用 Phase 16 两组真实快照的 GPT-5.6-sol 比较均推荐第 2 轮，同时列出新引入的矛盾；它只证明比较闭环成立，不是人类剧本质量 benchmark。运行后 Project、image task、generation task、provider run 和 H3 route plan 均为 0。后续另以 E010 验证 H3 reference video 与低保真运镜预演，不把它们写死为固定步骤。详见 [Phase 17 实施记录](27-phase17-revision-judge.md)。
- 2026-08-24：Phase 18 将前镜完整 Take、最后 4 秒尾窗和 4 秒低保真机位预演实现为可持久化 `H3VideoGuide`，并由 Route Agent 在 Direct、Frame 和 Reference 候选间可覆盖选择。同一 7 秒反打样本中，完整 Take/尾窗都重启了前镜行走，前镜尾帧预演重置了人物距离；改用当前镜头批准首帧作为预演基底后，反打、人物、走廊和工牌连续且未重复前镜，只剩结尾起步未完成。该结果已进入通用 Agent 策略和回归案例；它是单样本能力边界，不是全局默认，也没有增加创意门控、场景专用字段或 hash 冻结。详见 [Phase 18 实施记录](28-phase18-h3-video-reference-previs.md)。
- 2026-08-24：Phase 19 已把超过 90 秒的持久化 `PlanningRun` 直接衔接到现有 Asset Agent、H3 continuity wave、视频 QA/Director、TimelineManifest 和导出。2 分钟 10 镜测试在首批部分 Take 产生后重建 Service，仍完成 4 个互不相关镜头的局部返工、10/10 入选、120 秒 Manifest 与导出；5 分钟 20 镜测试用 40 个 Fake H3 候选完成 300 秒 Manifest，并验证逐场 Prompt 只传 compact handoff。该阶段曾把“每镜最多三次”作为自动修复上限；这一计数式停止规则已由 A012、A029、A034 的 Agent 语义决策取代。该阶段验证编排而非真实长片画质，因此没有提交付费 2–5 分钟 H3 长片；详见 [Phase 19 实施记录](29-phase19-long-form-autodraft.md)。
- 2026-08-24：Phase 20 已将 Scene Reviewer、Episode Reviewer 和关联 Director 修复接入原有 `AutonomousRun`。5 分钟 4 场/20 镜无问题测试执行了 4 次分场审阅和 1 次整集汇总；中途重启测试从持久化场次游标继续且只修复关联镜头。该阶段曾用 Reviewer/每镜三次上限控制停止，现已废止，保留这里只作为历史实验事实。Phase 8–10 三版真实语言探针找到了可见的道具/动作连续性，但漏掉 A 的空间竖缝且按设计不判断 B 的声音来源；一个通用空间清单对照只增加报告数量、未提高该召回，已撤回。报告不自动调用 STT、声纹或音频质量 QA，也不成为导出门；详见 [Phase 20 实施记录](30-phase20-episode-review-agent.md)。
- 2026-08-25：Phase 21 将“每场是高信息戏剧单元”、有效剧情时长、程序性过密和普通观众可见的权限—行为—后果沉淀到 Douyin Writer、Critic 与 Revision Policy，并提供 script-only 停点。最终 180 秒真人轻末日爽剧《假极寒舱变闷罐，她的山庄成了救命屋》为 5 场、22 句可说内容；运行停在 `SCENE_DIRECTOR` 入口，`project_id=null`，未调用 GPT Image 2 或 MiniMax-H3。详见 [Phase 21 最终剧本](31-phase21-real-three-minute-episode.md)。
- 2026-08-25：Phase 21 将 Critic 与编排控制解耦，并加入 Source Contract、Story Repair Planner、Critic JSON repair 和 Revision Verifier。真实修订证明 Critic 正确发现了“多名被骗买家”被 Repair Planner/Writer 为降低制作规模而删掉的来源漂移；第 7 版恢复该承诺，Writer 首稿经 Verifier 拒绝并定向修复 1 次后通过，最终 Critic 为 `0 HIGH / 2 MEDIUM / 2 LOW`，问题数从上一版 5 降到 4。同期实现 PlanningRun 的直接改稿、Agent 定向改稿、基于最新审阅再修订，以及只读 `agent-tools/plan-change` 语义影响计划；用户后续编辑会成为优先于冲突旧承诺的 authoritative override。定向测试 49 项、后端全量回归 744 项、Pyright 0 error、TypeScript 检查通过；详见 [Skill 审阅、来源诊断与语义修改控制面](32-phase21-skill-review-and-change-control.md)。
- 2026-08-25：Phase 22 将不同时长剧本收敛为同一套分层 Tools。Story Architect 只产生全局 Blueprint，Scene Writer 逐场生成并持久化 Patch；剧本版本投影为 Story Bible + Scene 文档并通过 SQLite FTS5 在 run/revision 内检索。新增剧本检索和局部改稿 Agent Tool API，用户指定场次时只替换该场并由代码重建台词索引；Review Repair 也优先沿 `affected_scene_sequences` 局部修订。2026-08-26 取消了 15 秒到 30 分钟的产品档位边界；在该阶段，不少于 4 秒的输入默认全部进入同一 PlanningRun，后由 Phase 29 的 AgentRun 默认入口替代。详见 [Phase 22 实施记录](33-phase22-hierarchical-script-tools.md)。
- 2026-08-26：Phase 23 将原 `ProductionProfilePolicy` 收敛为显式 `ProductionSkill`，加入静态 Catalog、领域角色策略与 `user_overridable` 建议式 playbook。Planner、分层 Scene Writer、Reference Planner 和语义修改等创作节点统一读取所选 Skill；当时 PlanningRun/AutonomousRun 只负责默认推进、恢复和 Provider 对账，不把 playbook 解释为硬 workflow，也未新增动态 Registry 或 Tool allowlist。旧 AutonomousRun 控制面后续已由 A018/A020 取代并删除；详见 [Phase 23 实施记录](34-phase23-explicit-production-skills.md)。
- 2026-08-27：Phase 24–25 完成第一轮 H3 生成前质量研究、官方模式 Prompt IR、顺序候选和 `CompareTakesTool`；Phase 26 进一步核实纳逗 Pro、可灵、LTX Studio、Google Flow、Runway、Adobe 和 Dreamina 的公开产品机制，静态审查 MiniMax-H3、LTX Desktop、Toonflow、ComfyUI、ViMax、AESR、MSVBench 与 MultiShotMaster 固定源码，并区分论文报告、源码事实和当前 H3 可迁移边界。当时提出的低重试方案包括最小充分路线、生成前视觉比较、首候选自适应停止、带时间证据的类型化修复和局部重算；三视图、机位预演、reference video 与候选数保持 Agent 建议。其中“首候选自适应停止”已被 A029/M005/A037 的全部合法候选暴露、显式 Agent 批次选择与新 Route Plan 探索取代。本轮未调用 H3、GPT Image 2 或其他付费媒体 Provider。详见 [Phase 26 系统调研](37-phase26-platform-paper-open-source-low-retry-research.md)。
- 2026-08-27：Phase 27 将参考视觉重排、关键帧比较和输入兼容性检查实现为可独立调用的 Agent Tools，并以 `visual_input_reports` 持久化建议。三个 Tool 均先使用项目内真实媒体，要求模型先观察再比较，支持冲突/冗余和 `NONE_ACCEPTABLE`，不自动修改任何资产或路线；未知项目媒体会在调用边界拒绝。定向测试、Pyright、TypeScript 和 OpenAPI 生成均通过，未调用 H3 或 GPT Image。详见 [Phase 27 实施记录](38-phase27-visual-input-quality-tools.md)。
- 2026-08-31：Phase 28 将 Production Skill Prompt 拆为稳定 constitution、角色 rubric 和上下文经验，移除核心 Prompt 中的录音/文件/签名/融资/付款等单剧本规则；经验仅在 Profile、角色和当前上下文匹配时追加，宽泛辅助词不能单独触发。Scene Director、Director Advice、资产、H3 Route、GPT→H3 Prompt Compiler 与 Scene/Episode Reviewer 已按职责统一读取 Skill，视频抽帧与比较 Tools 保持中立。后端全量 774 项、Pyright、TypeScript 与生产构建通过，未调用 H3 或 GPT Image。详见 [Phase 28 实施记录](39-phase28-production-skill-layering-and-injection.md)。
- 2026-09-01：Phase 29 将默认新建 GUIDED/AUTO_DRAFT 与已有项目的新自动制作迁移到 AgentRun。Production Manager 动态维护 PlanGraph 与 `goal_complete`，四类中文 Markdown Production Skills 共享 Typed Tools；RunMemory、ContextPack、WorkOrder/AgentResult、项目范围校验、重启恢复和语义变更 plan/apply 已接通。桌面 AUTO_DRAFT 仅追踪本次启动的 Run，在真实 H3 等待期间按退避自动续跑。独立 PlanningRun 与 AutonomousRun 的接口、前端、模型、服务分支和脚本已删除；当时的长篇入口改为由所属 AgentRun 通过 `planning.advance` 推进内部 PlanningRun，当前 A016/A028 已进一步统一为所有配置语言 Provider 的 Agent-owned 原创/扩写都使用该 Content Planning Job，Provider 对账由领域任务状态机负责。未调用付费 Provider。详见 [Phase 29 最终架构与迁移记录](40-agent-skill-tool-runtime-migration.md)。
- 2026-09-03：Agent-native 剧本链路已加入 Story Orientation、`StorySourceContract`/`StoryIntent`/`EndingContract` 持久化、中文共享角色、文件化 Narrative Skill Catalog 与 Agent 语义选择、Critic Issue Ledger、定向逐场修订和增强 Verifier；增强 Verifier 同时承担 Issue 修复验收与脱离修复说明的全稿自然冷读，并直接接受或继续聚焦修订。较早实现中的独立 `FINAL_COLD_READ` 已被取代，只保留旧运行和持久化兼容；专业 WorkOrder 也改为 `specialist_turns` 单步持久化及中断恢复。工程回归能确认状态、Prompt 组合与恢复边界；不同题材的 Narrative Skill 选择质量、Critic 是否减少过度挑错、局部修订是否保护优点，以及真实资产/H3 连贯性仍属真实模型与完整播放实验，不因文档或单元测试通过就记为质量结论。

## 对话顺序

从 `P001` 开始，严格按依赖推进。可以并行确认的相邻小项仍逐项记录；必须实测的事项先锁定实验和判定规则，运行实验后再把状态改为 `LOCKED`。
