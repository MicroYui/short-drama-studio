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
| P002 | `LOCKED` | 首版只交付并优化真人都市短剧；真人与漫剧共用制片主线，未来通过风格资产、提示编译、执行路线与 QA Profile 扩展漫剧，不建立第二套工作流 | P001 |
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
| A012 | `LOCKED` | 真人 `AUTO_DRAFT` 在同一 SQLite/API 核心上采用有限推进和自动轮询，复用现有资产/H3 任务、Take、QA 和 TimelineManifest；仅配置缺失、提交未知或重试耗尽进入 `NEEDS_ATTENTION`，应用重启后可继续 | A005、A008、P007 |
| A013 | `LOCKED` | 视觉资产以 Agent 管理、Tool 执行：结构化分镜需求驱动目录搜索、覆盖检查、同源派生、语义审查、登记和逐镜 ReferencePlan；场景先建 Master Wide，必要时再派生局部视图，三视图不是固定前置。这些是可忽略的 Agent 选择，不是用户硬审批门 | A006、A008、P007、P010 |

## C. 图像、视频、音频与后期路线

| ID | 状态 | 待确认事项 | 依赖 |
| --- | --- | --- | --- |
| M001 | `LOCKED` | 当前视频生成模型只有 MiniMax-H3；本阶段不做视频模型候选、选型或横向比较，所有视频路线实验都在 H3 内进行 | P001 |
| M002 | `EXPERIMENT_REQUIRED` | GPT Image 2 是全量视觉资产层还是仅覆盖高风险镜头 | P001、E002 |
| M003 | `EXPERIMENT_REQUIRED` | H3 Direct、Frame route、Reference route 的适用镜头规则 | M001、E003 |
| M004 | `EXPERIMENT_REQUIRED` | 是否使用 H3 Context-IR，以及它相对自编译提示是否有增益 | M001、E003 |
| M005 | `LOCKED` | PoC 的所有 H3 视频请求固定使用 768P，不做 1080P/2K 生成或分辨率对照；时长遵守 4–15 秒镜头计划，候选数使用 E006 的普通 3、高风险 5 及定向补充规则 | M001、E003、E006 |
| M006 | `EXPERIMENT_REQUIRED` | 原生人声、reference audio、真人录音和外部 TTS 的优先级 | M001、E004 |
| M007 | `LOCKED` | STT 默认用于原生/录制音频的台词 QA 与字幕时间候选，具体 Provider 可替换；最终以锁定台词、人工听审和画面共同判定 | E004 |
| M008 | `EXPERIMENT_REQUIRED` | 不预接外部口型模型；只有 H3 原生、声线参考及必要的外部主音频路线仍无法通过正脸/侧脸/多人对白时，才启动候选工具实验 | M006、E004 |
| M009 | `LOCKED` | 字幕计时优先级：可靠 Provider 对齐数据；外部 TTS 使用 word-boundary；其余原生/录制音频用 STT 对齐；不准时允许人工微调并回写 TimelineManifest | E004、M007 |
| M010 | `EXPERIMENT_REQUIRED` | 独立镜头与模型内多镜的分组规则 | M001、E005 |
| M011 | `LOCKED` | H3 源视频保持 768P；交付预设为 1080×1920 9:16 H.264 High/YUV420P/BT.709/CRF18 MP4、AAC-LC 48kHz 192kbps，默认 30fps CFR但避免损伤性改帧；输出带/无字幕 MP4、SRT、ASS、TimelineManifest，安全区上/左8%右16%下20%，约 -14 LUFS/-1 dBTP，并做 FFmpeg/ffprobe 技术 QA | P008、M005 |
| M012 | `LATER` | 局部图像修复、补帧、超分、降噪等后处理是否值得加入 | E003–E005 |
| M013 | `LATER` | LTX-2.x 或其他视频模型候选不进入当前范围；只有未来明确重新开放模型扩展时才讨论 | M001 |

## D. 固定实验与质量判定

| ID | 状态 | 待确认事项 | 依赖 |
| --- | --- | --- | --- |
| E001 | `LOCKED` | 固定使用《别相信我》的角色、雨夜便利店、红色录音笔、五句锁定台词和八镜表作为首轮实验输入 | P001–P005 |
| E002 | `LOCKED` | 资产来源不限定；GPT Image 实验比较独立生成、角色参考、角色+场景+上一镜状态、连续编辑四种方式，每条件至少 5 次并盲评；用镜头 2/3/5/8 验证下游 H3，只有动态质量不降且身份、空间、道具、构图或稳定率至少一项提高才通过 | E001 |
| E003 | `LOCKED` | 固定 768P 比较 H3 Direct、首帧、首尾帧和 Reference 合法路线；Frame/Reference 互斥并在提交前校验。先用镜头 1/2/5/8，再扩展八镜；对代表样本比较 GPT-5.6-sol 自编译提示与 Context-IR，最终按镜头类型记录路线；PoC 不做更高分辨率实验 | E001、M001 |
| E004 | `LOCKED` | 先验证 H3 原生音频的五句逐字正确、数字/否定词、跨镜声线、说话人和正/侧脸口型；声线漂移再测 reference audio，仍不合格再比较真人录音/外部 TTS。STT 只做 QA/时间候选，字幕按 Provider→TTS boundary→STT→人工微调取时；外部口型最后才评估 | E001、M001 |
| E005 | `LOCKED` | 比较 8 镜独立、相邻两镜 `1+2/3+4/5+6/7+8`、表演段落 `1/2+3/4+5/6+7/8`、独立镜头+连续性帧四组；多镜不超过 H3 15 秒，只有连续性/表演增益且不显著扩大严重错误与返工范围时才按组采用 | E001、M001 |
| E006 | `LOCKED` | 每条件先 5 个样本，可能入选者至少扩至 10；接近/波动时每次加 5、最多 20，仍不稳定记 `UNKNOWN`。普通镜头首轮 3 Take、高风险 5 Take，至少取得 2 个硬通过候选；同方案 10 次仍不稳则改路线。默认路线须严重失败≤20%、硬通过≥70%、严格对白正确≥80%；最终 Take 100% 过硬门槛 | E002–E005 |
| E007 | `LOCKED` | 1 人检查硬门槛、至少 3 人匿名盲评；同一锁定输入独立运行 3 次 AUTO_DRAFT，全部到可播放时间线并导出，自动 QA/定向重试后至少 2/3 无严重失败。最终片 45–60 秒、全部硬门槛通过、核心项中位数≥4/5、至少 2/3 愿意继续看；允许换 Take/局部返工，不允许靠人工重建大部分影片 | E006 |
| E008 | `EXPERIMENT_REQUIRED` | 各镜头类型的默认 RouteEvidence | E002–E007 |
| E009 | `LATER` | 视频模型横向比较不进入当前范围；未来开放 M013 时再设计 | M013 |

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
- 2026-08-17：Phase 5 已完成四种输入归一化、`AutonomousRun`、适应性抽帧、镜内/镜间接触表、语义观察和观察结果到 Director Agent 的通用连接。观察结论是可忽略建议，不把单一样片的门、方向或动作词固化成底层领域字段；详见 [Phase 5 实施记录](15-phase5-autonomous-runner-and-video-observation.md)。
- 2026-08-17：Phase 6 已完成证据优先的 H3 制片路线。系统在不新增 H3 请求的情况下自动复用 9 条当前版本、非合同冒烟且人工通过的真实 Take，运行逐镜和镜间观察，重建 57.13 秒全 H3 `TimelineManifest`，并完成 1080×1920 带/无字幕技术导出。该结果证明复用型真实执行闭环，不代表 E007 已完成；仍需从零项目的三次独立 `AUTO_DRAFT`、整集人工观看和匿名盲评。详见 [Phase 6 实施记录](16-phase6-real-route-planner.md)。
- 2026-08-17：Phase 7 已完成可恢复的真人 `AUTO_DRAFT` 第一闭环。离线 Fake Provider 从空输入完成核心资产生成与语义检查、镜头关键帧、H3 异步候选、进程重建恢复、镜内/镜间观察、自动候选裁判、Director 有界修复、Take 选择、TimelineManifest 和导出；通用构图失败会使旧 Take 失效、自动修改镜头意图并重新规划，缺少配置时则持久化为 `NEEDS_ATTENTION`，不会回退 Mock。后端全量 641 项测试、Pyright、TypeScript 和 Vite 生产构建通过。该结果是工程闭环证据，不是新项目真实画面、对白或声线质量证据；详见 [Phase 7 实施记录](17-phase7-real-autonomous-draft.md)。
- 2026-08-18：Phase 8 完成第一次真实从零真人 `AUTO_DRAFT`。锁定剧本在真实 GPT Image 2、GPT-5.6-sol 和 MiniMax-H3 768P 下自动得到 9/9 已选 Take、35 份视频观察、54.31 秒 `manifest-v3` 成片和全部通过的技术导出。真实运行修复了关键帧主参考选择、批准资产身份对照、生成/QA 参考分离、画外状态提示投影和画外证据判定等通用缺口；最终 S04 没有身份漂移或道具误入，仅保留中等级景别建议。用户已按本轮整体可用标准将成片暂时通过，从锁定剧本到最终人工验收的 Phase 8 流程因此闭环。E007 仍待逐字声音 QA、另外两次独立运行和三人盲评；详见 [Phase 8 实施记录](18-phase8-real-autonomous-validation.md)。
- 2026-08-18：Phase 9 已把音轨提取、信号诊断、字幕时间、锁定对白转写和跨镜声线比较实现为 Provider 中立的 `AudioInspection`，并接入 `AUTO_DRAFT`、SQLite、Product API、QA 工作台和 Director 建议。使用现有 Phase 8 `manifest-v3` 复检而未新增 H3 请求，得到 9/9 音轨技术通过、5/5 繁简体与标点归一化后逐字一致、林夏和周衡声线检查均通过，问题/告警为 0。该结果完成本片 E004 音频证据，但不把单次结果外推为跨剧本稳定率；E007 仍待另外两次独立运行和三人盲评。详见 [Phase 9 实施记录](19-phase9-audio-dialogue-qa.md)。
- 2026-08-20：Phase 10 新增固定前期制片包的独立重复入口，并以真实 GPT Image 2、GPT-5.6-sol 和 MiniMax-H3 768P 再执行 Run 2 / Run 3。两次都自动完成关键帧、H3 候选、长镜头/镜间抽帧、Director 有界修复、9/9 选 Take、`TimelineManifest`、音频 QA 和 54.31 秒 1080×1920/30fps 导出；五句锁定对白和林夏/周衡跨镜声线均通过自动检查。加上 Phase 8 Run 1，三版已生成匿名盲评包；自动结果中至少 2/3 没有高严重度入选问题。E007 尚未最终通过，剩余至少三人匿名盲评与最终片选择。详见 [Phase 10 实施记录](20-phase10-independent-repeats-and-blind-review.md)。
- 2026-08-20：用户选择不填写数值 CSV，直接提交三版完整观看后的汇总定性结论：A 的首镜有竖向接缝造成局部空间不连续；B 的录音笔播放被错误表现为女主本人说话，属于声音来源语义失败；C 未发现明确严重问题，并选为当前最终版。本轮产品验收据此完成，至少 2/3 未被判为整片严重失败；没有收集三名独立评审、维度分数或继续观看意愿，因此不宣称完成三人评分 benchmark。B 的问题保留为通用已知缺口，不增加录音笔专用字段。详见 [Phase 10 实施记录](20-phase10-independent-repeats-and-blind-review.md)。
- 2026-08-20：Phase 11 将可上传或麦克风录制的 2–15 秒角色人声规范化为 H3 可用 WAV，并以项目级可选绑定接入资产工作台、独立重复和 Route Planner。实际有该角色台词的镜头可自动选择带 `reference_audio` 的 Reference 候选；未绑定时继续使用 H3 原生声音，Frame 修复路线也可因画面质量不采用声线输入。声线版本会使旧路线与旧任务产物失效。用户明确本阶段只支持输入，不新增声音 QA；详见 [Phase 11 实施记录](21-phase11-optional-character-voice-input.md)。
- 2026-08-20：Phase 12 已实现跨项目资产目录、分镜结构化 `reference_needs`、场景 Master Wide、同源 GPT Image 派生、母版系谱、资产覆盖检查和逐镜 `ReferencePlan`，并将搜索、导入和覆盖检查暴露为 Product API。真实《明天别来了》运行生成 4 个资产包、18 个登记视图和 36 个 H3 768P Take，Director 自动拆分高风险交接镜头，最终形成 8 个入选 Take、6 条锁定字幕和 58.00 秒成片。详见 [Phase 12 实施记录](22-phase12-cross-script-asset-agent.md)。

## 对话顺序

从 `P001` 开始，严格按依赖推进。可以并行确认的相邻小项仍逐项记录；必须实测的事项先锁定实验和判定规则，运行实验后再把状态改为 `LOCKED`。
