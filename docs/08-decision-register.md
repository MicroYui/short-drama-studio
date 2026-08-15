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
| D005 | `LOCKED` | 同时支持一句话扩写和已有剧本输入；已有剧本保留原文与执行文本，不静默改写。 |
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
| P007 | `LOCKED` | 默认 `AUTO_DRAFT` 从输入自动生成完整可观看成片并自动选 Take、QA、定向重试；剧本、定妆、分镜和候选均为可选停靠点；只有标记正式通过需要人工确认，草稿生成与导出不受阻 | P002、P003 |
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

## 对话顺序

从 `P001` 开始，严格按依赖推进。可以并行确认的相邻小项仍逐项记录；必须实测的事项先锁定实验和判定规则，运行实验后再把状态改为 `LOCKED`。
