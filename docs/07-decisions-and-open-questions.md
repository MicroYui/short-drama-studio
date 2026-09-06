# 当前结论与待讨论决策

## 1. 本轮已经成熟的结论

### D1. 质量优先，也不以“最少模型”为目标

GPT Image 2、MiniMax-H3 和 Azure Speech 不是为了凑技术栈而强制使用，但也不需要等现有路线失败才加入。某项能力只要使质量更好，或质量不劣且一致性、可控性、稳定性更强，就可以成为默认链路。成本记录不主导这一判断。

### D2. 闭源平台只学习，不使用

即梦、可灵、LTX Studio、纳逗 Pro 不进入运行时，也不作为未来依赖。只学习它们已经验证过的产品机制：项目级资产、自动与可锁定分镜并存、镜头 Retake、音画协同时间线、连续性资料和专业工种交付物。

### D3. 首版固定为单用户、受信环境

不做注册、登录、账号、用户/组织、RBAC、多租户、团队成员和分享权限，也不给项目与素材增加用户归属字段。内容的批准状态只控制生产流程。Jellyfish 后端本身未发现账号权限耦合；LTX Desktop 也没有产品用户系统，只有模型下载和 Provider 凭证。若采用任一底座，只清理无关表层入口；Provider API 凭证继续作为外部调用配置保留。

### D4. 一条共享工作流，两种输入契约

一句话走 `IDEA_EXPANSION`，已有剧本走 `SCRIPT_LOCKED`。二者在剧本批准后共享 ProjectBible、ShotPlan、ExecutionPlan、素材、任务、候选、QA 和 TimelineManifest。

### D5. 规划模型负责结构化制片，不直接承担媒体生成

当前可使用 GPT-5.6-sol 做扩写、诊断、项目圣经、分镜、连续性、路线建议、Provider prompt 编译和结果复盘；关键中间工件保存为稳定 schema 和版本，不与模型名绑死。

### D6. 视觉资产来源中立，GPT Image 2 按效果参与

角色、服装、场景、道具和动作终态不能只靠跨任务的自由文本猜测，但资产不要求全部由 GPT 生成。它可以来自免费或商业素材库、用户上传、GPT Image 2 新生成，或由 GPT Image 2 对上传/导入素材做修整和派生。GPT Image 2 从首轮与 Direct 路线比较；若下游视频更好，或动态质量不劣但跨镜一致性和构图控制更稳，就用于适合镜头。只有出现风格转换损失、表演受限或动态质量下降时才按镜头跳过。

### D7. 音频路线由成片表现决定

支持原生音频的视频模型应测试原生能力，但它不因步骤更少而自动胜出。录音、Azure/其他 TTS、reference audio、STT QA 与后期口型可直接参与质量比较；最终以台词、声线、表演和口型的综合结果决定。

### D8. 通用领域模型与 Provider 约束分层

通用层保存 `ExecutionPlan` 与 `ReferencePlan`；H3 的 frame/reference 互斥等规则只存在于对应 Adapter 和 capability snapshot。更换视频引擎不应重写剧本、分镜和时间线模型。

### D9. 模型内多镜与项目时间线同时存在

模型内多镜可用于局部连续表演；项目 `TimelineManifest` 负责 30–90 秒整片顺序、字幕、音量、转场和输出。模型不支持多镜时，架构仍然成立。

### D10. 不整仓照搬

LTX Desktop 已确定为唯一底座 Spike 和正式工程历史来源，保留桌面编辑、媒体、Take 与时间线能力，并由自建 SQLite/API 与持久化 Worker 接管项目和任务状态。Jellyfish 只参考领域与任务语义并作为结构性失败回退；Toonflow、ViMax 和 Wind Comic 的成熟规则或模块按需移植到统一 schema。ComfyUI 仅在具体局部修复需求被实验证实后接入。LTX 模型权重不进入产品，LTX 不作为本地 Provider，视频生成使用 H3。

### D11. Mock 用于工程正确性，真实样本用于稳定结论

先用 Mock 排除状态机、时间线和恢复错误，再运行真实质量基准。真实实验不做逐笔审批或逐阶段额度释放，样本量由方差和结论稳定性决定；仍避免无法归因的盲目抽卡和重复提交。

### D12. 首个质量标杆采用真人都市短剧

首个固定样片用真人都市题材验证质量上限，重点暴露人脸、肢体表演、服装、空间、声线、口型和跨镜连续性问题。该决定只锁定首个标杆，不自动决定首版是否同时交付漫剧。

### D13. 历史首版验收曾限定真人标杆（已由 D47 覆盖）

早期首版验收曾只用真人都市短剧验证质量上限，但从未允许把“真人”硬编码进通用领域模型。当前产品决定以 D47 为准：AI 原生短剧不预设真人或动画，具体视觉、运动和声音选择由 Agent 写入项目 Bible Style，不同形态仍共用剧本、资产、分镜、生成、Take 和时间线主线。

### D14. 首版验收单集完整闭环

首版以一集真人都市短剧从输入到最终成片的完整闭环为通过条件，不同时要求多集连续性。角色、场景、服装、道具和可选声线仍从第一版起作为版本化、可复用资产保存，避免后续扩展跨集时重构数据模型。

### D15. 首个验收片为 45–60 秒、8–10 镜

首个真人都市短剧成片目标 45–60 秒、8–10 个最终剪辑镜头；固定实验样片先按约 50 秒、8 镜执行。模型内部是否一次生成多个镜头另行实验，不改变最终剪辑镜头规模。

### D16. 固定测试剧本采用《别相信我》

首个基准采用林夏、周衡在雨夜便利店围绕红色录音笔对峙的真人都市悬疑短剧《别相信我》。五句台词和八镜结构构成锁定基准；后续若修改剧情或台词，保存为新的 `ScriptVersion`，不覆盖原始实验输入。

### D17. 首版正式支持普通话中文

首版内容生成、声音、口型、字幕和 QA 只对普通话中文建立正式质量基准。领域模型继续保存 `language`、`locale`、`original_text`、`spoken_text` 和 `subtitle_text`，但方言及其他语言在完成各自固定输入验证前不作质量承诺。

### D18. 默认自动到底，人工确认是可选停靠

默认 `AUTO_DRAFT` 从无输入、若干图片、一句话或剧本自动生成完整可观看草稿，系统自动完成创作补全、选择 Take、QA、导演修复、定向重试和时间线组装，只展示一个或少量完整最终版。`GUIDED` 模式才在剧本、角色/场景定妆、分镜和镜头候选处暂停，且修复建议可忽略。只有把草稿标记为正式通过需要人工动作，草稿生成与导出不被阻塞。

### D19. 首版提供短剧生产时间线，不做完整专业 NLE

首版支持 Take 切换、重生成、Retake/Extend/Gap Fill、镜头拆分裁切排序、视频/音频/字幕轨、音量、基础转场、字幕调整和 9:16 确定性导出。高级调色、蒙版、复杂特效、关键帧曲线和多机位不进入首版，后续按实际使用再研究。

### D20. 首版止于导出，不接抖音发布与运营

首版输出 9:16 MP4、SRT/ASS、TimelineManifest、项目素材和 QA 报告，不接抖音账号、自动发布、账号运营或数据管理。发布能力后续可作为独立边界评估，不污染生成、编辑和质量闭环。

### D21. 首版本地桌面部署优先

首版以单用户本地桌面应用运行，项目数据库与媒体保存在本机；GPT Image、H3 等 Provider 仍可通过云端 API 调用，不要求模型本地部署。后端保留清晰 API/Worker 边界供未来服务器迁移，但首版不同时兼容或维护服务器/浏览器运行形态。

### D22. 只做 LTX Desktop 单候选底座 Spike

不并行实现 LTX Desktop 与 Jellyfish 两个原型。先在 LTX Desktop 中用供应商中立 Mock 验证 SQLite/API 项目域、可重启任务、Take/局部返工和 9:16 时间线导出；通过后直接收敛，只有发现无法合理隔离或替换的结构性阻碍时才回退 Jellyfish。

### D23. LTX Desktop 是主底座，Jellyfish 与 LumenX 降为参考

LTX Desktop 作为首选代码底座与未来 fork 来源。Jellyfish 只贡献项目域、准备度与持久化任务语义，并在 LTX 出现结构性失败时作为回退；LumenX 不进入底座实现，仅参考模型目录和候选工作台。其他开源项目继续作为规则、算法或媒体模块来源。

### D24. 保留 LTX 编辑壳，重构短剧与任务核心

保留并扩展 Electron/FastAPI 壳、Gen Space、多 Take、Retake/Extend/Gap Fill 的用户意图、Source/Program Monitor、时间线、媒体导入、SRT/FCP、FFmpeg 框架、OpenAPI 类型桥与性能测试。重构或复用整合项目持久化、可恢复任务、供应商中立 Adapter、短剧领域/QA/TimelineManifest 和预览导出一致性；LTX 模型 Adapter 保持禁用，需要新媒体的局部重做由 H3 路线执行。

### D25. 领域模型与模块复用优先

实现时可以直接参考或移植其他已审查项目的成熟设计与代码，不要求所有领域模型自己手搓。优先来源包括 Jellyfish 的项目/准备度/任务语义、Toonflow 的剧本与分镜规则、ViMax 的机位/首尾帧结构、Wind Comic 的成片/QA，以及后续证明确实合适的实现；复用结果必须进入统一 schema 和单一项目真相源，不保留第二套并行运行时。

### D26. 首版采用轻量本地数据与 Worker 组合

SQLite WAL 保存项目、版本、任务、Take、QA 和时间线元数据，本地项目目录保存素材、生成结果、缓存、渲染与导出；Electron 管理独立本地 Python Worker，从 SQLite 任务表领取工作。本地 GPU 默认单任务，云端并发按 Provider capability 配置；首版不引入 Redis、Celery、MySQL、S3 或 Kubernetes。

### D27. PoC 优先默认制片工作台，高级执行图后置

PoC 先延续并扩展 LTX Desktop 的语义制片工作台：项目、剧本/ProjectBible、角色/场景/道具资产、分镜/连续性、Gen Space/Take/QA、短剧生产时间线和 Provider 设置。自动流程以语义阶段、工件、进度和可操作问题呈现，不要求普通用户手工连完节点。PoC 稳定后可增加从 `PlanGraph/WorkOrder/ArtifactRef` 派生的高级 Agent 生产图，用于查看、暂停、局部重做或重新规划；它是现有状态的投影，不是第二个执行引擎。

### D28. 状态分离、局部失效、旧版本可复现

产物版本使用 `DRAFT/APPROVED/SUPERSEDED`，依赖新鲜度使用 `CURRENT/STALE`；生成任务使用 `QUEUED→SUBMITTING→ACKNOWLEDGED→RUNNING→SUCCEEDED/FAILED/CANCELLED`，并显式处理 `SUBMIT_UNKNOWN` 与 `OUTPUT_MISSING`。Take 的媒体可用性、`PENDING/PASSED/REJECTED` QA 和是否入选彼此独立，Provider 成功不等于 QA 通过或时间线采用。TimelineManifest 精确引用剧本、资产、音频与 Take 版本；上游修改只令受影响分支失效，旧产物不被静默覆盖且旧成片保持可复现。重启恢复依靠持久化任务记录和对账，不盲目重复提交；首版不引入事件溯源或密码学证据机制。

### D29. 当前视频生成只接 H3，不做模型竞选

MiniMax-H3 是视频生成主链模型，而不是等待与其他模型竞选的候选。不接 LTX-2.x，也不设计 H3 与 LTX 的横向模型对比；所有画面、声音、参考方式和多镜实验都在 H3 的能力边界内进行。LTX Desktop 仅提供 Apache 应用代码、编辑器和媒体壳，不启用、不集成或分发其模型权重。核心 Adapter 边界仍可保持供应商中立以避免模型细节污染领域工件，但不建设 LTX Provider。

### D30. 资产工作台统一管理上传、素材库与 AI 派生

资产包表示一组已批准、可版本化的角色、服装、场景、道具和参考媒体，不表示其来源。首版资产工作台支持本地上传、外部素材库文件/链接导入、GPT 新生成，以及把已有资产交给 GPT 做润色、补全视角、换装或风格统一；每个派生版本保留父版本和普通来源信息。固定样片只需锁定最终输入版本，不把内置素材库检索作为实验前置；多素材库搜索与下载连接器可在核心闭环后按实际需求接入。

### D31. H3 路线按镜头实测，不寻找全局唯一模式

H3 视觉实验只提交合法输入组合：Direct、首帧、首尾帧和 Reference；Frame 与 Reference 的互斥由产品在提交前校验。PoC 的 H3 请求全部固定为 768P，不测试 1080P/2K。先用镜头 1、2、5、8 覆盖空间、正脸、手部道具和动作接续，再扩展完整八镜。Context-IR 只在代表样本上与 GPT-5.6-sol 自编译提示比较，避免把提示和输入模式同时混成一个变量。结果按镜头类型写入 `RouteEvidence`，不要求整片统一使用一种 H3 模式。

### D32. H3 原生声音先行，外部声音与口型按问题升级

先验证 H3 原生音频能否满足五句锁定台词、数字/否定词、跨镜声线、正确说话人、静默角色闭嘴及正脸/侧脸口型。若只有声线漂移，再测 `reference_audio` 并区分声线迁移与原信号复用；若台词或声音仍不合格，再比较真人录音和外部 TTS，Azure 只是可替换实现。STT 默认承担台词筛查和原生/录制音频的字幕时间候选，不代替锁定文本与人工听审。外部口型模型不预先集成，只有上述路线仍无法过线时才评估。

### D33. 多镜分组只在连续性收益覆盖返工代价时采用

固定八镜比较四组：全部独立；相邻两镜 `1+2 / 3+4 / 5+6 / 7+8`；表演段落 `1 / 2+3 / 4+5 / 6+7 / 8`；全部独立但用上一镜尾帧或连续性关键帧约束下一镜。所有 H3 模型内多镜任务不超过 15 秒，并显式给出切镜时间。比较身份、服装、空间、道具、动作、声线、切镜准确度、单镜质量、后期自由度和单点失败的返工范围；只有连续性或表演明显改善且严重错误与返工范围没有显著扩大时，才把分组规则写入对应镜头类型。

### D34. 以证据充分性决定样本与候选，不把计数变成运行门禁

早期固定“5/10/20 个样本、3/5 个 Take、至少 2 个硬通过候选”的方案已经废止。实验时先声明要比较的真实问题和可观察证据，再根据结果分歧、波动与决策风险决定是否继续取样；无法形成稳定结论时保留 `UNKNOWN`。生产运行中，Route Agent 根据当前镜头、已有候选和失败证据决定继续生成、改变输入、调整镜头或接受当前结果，不由候选数、失败次数、分数阈值或 QA 布尔值自动停止或放行。Provider 请求格式、媒体可读性和时长范围等技术合同仍在提交边界校验，但不承担审美判断。

### D35. 独立运行与盲评是实验方法，不是产品完成条件

需要比较路线或版本时，可在同模型、同输入和相同可控条件下做独立运行、隐藏条件的成对/盲评及具体失败分析；样本量由当前不确定性和实际决策决定，不预设为产品运行次数。自动闭环验收关注规范项目状态能否独立到达目标工件，质量验收关注真实可播放内容及目标观众体验；两者均保存 trace 和证据，但不以固定运行次数、评分中位数、通过比例或“问题清零”构成运行时门禁。人工仍可换 Take、局部返工或调整时间线，并应区分这是正常创作选择还是为掩盖结构性故障而重建大部分作品。

### D36. H3 PoC 固定 768P，交付规格与生成规格分离

PoC 的所有 H3 视频请求固定使用 768P，不测试 1080P 或 2K，也不把分辨率作为实验变量。原始 768P Take 始终保留；最终交付仍使用已确认的 1080×1920 竖屏预设，一次性从原始 Take 合成/缩放，不能把封装尺寸描述成 H3 原生细节。交付使用 H.264 High、YUV420P、BT.709、CRF 18、AAC-LC 48kHz/192kbps；默认 30fps CFR，但若 H3 原生 24/25fps 转换会产生明显重复帧，则锁定项目到原生帧率且不默认插帧。输出带字幕与无字幕 MP4、SRT、ASS 和 TimelineManifest；安全区上/左 8%、右 16%、下 20%，音频约 -14 LUFS、True Peak 不高于 -1 dBTP，并执行 FFmpeg/ffprobe 技术检查。同一 TimelineManifest 的“确定性”指剪辑与音画结果一致，不要求文件逐字节一致。

### D37. Mock 垂直切片先证明 LTX Desktop 壳可被统一项目核心接管

Mock 切片固定基于已审查的 LTX Desktop 快照，不调用 H3、GPT Image、TTS/STT 或素材库。SQLite WAL 与 Product API 取代 `localStorage` 项目真相源，最小领域覆盖 Project、Script、Asset、ShotPlan、GenerationTask、Take、QA 和 TimelineManifest；可注入失败的 Mock Provider 为《别相信我》八镜生成确定性占位视频、音频、字幕和多个 Take。必须验证排队、取消、失败、重试、进程重启、`SUBMIT_UNKNOWN` 对账、换 Take、局部生成、Retake/Extend/Gap Fill、依赖失效、时间线和带/无字幕导出，并证明预览/导出一致。登录权限、最终 UI 打磨和真实模型不进入本切片。只有统一 SQLite/API 状态无法合理接管现有壳，或 TimelineManifest 无法驱动现有时间线与导出，才视为结构性失败并回退 Jellyfish。

### D38. 正式工程直接继承 LTX Desktop 历史，不包一层第二仓库

正式工程以固定 LTX Desktop commit 的 Git 历史为根，保留 `frontend/`、`electron/`、`backend/` 骨架及桌面生命周期、Preload、媒体导入、Gen Space、Take/Retake/Extend/Gap Fill、Source/Program Monitor、时间线、字幕、SRT/FCP、FFmpeg 和现有测试构建。新增后端 `domain/application/infrastructure/db/adapters/workers`、前端剧本/资产/分镜/连续性/QA features，以及《别相信我》fixture。`localStorage`、内存单任务槽、LTX 专用请求和非 TimelineManifest 导出被替换；LTX/fal/Gemini、模型下载、LoRA 与无关模型入口禁用。当前 `docs/` 随工程保留，不建立嵌套 `vendor` 仓库、第二套旧项目格式或第二运行时。

### D39. 先证明统一 Mock 闭环，再按质量依赖顺序接入真实能力

实际实施已从 LTX Desktop 上游基线、SQLite/API 领域核心、持久化 Worker、Mock 切片、MiniMax-H3 768P、资产工作台、GPT Image、GPT-5.6-sol、真实 `AUTO_DRAFT`、音频检查和三次成片验收推进到 Phase 11 的可选参考音频输入。TTS 或外部口型仍按后续失败证据再决定；ComfyUI 后处理、内置素材库等增强也继续由真实质量收益驱动。

### D40. 自动正确性、媒体合规和人工质量分层验收

测试固定分为六层：单元测试覆盖状态机、依赖失效、版本选择、时间码、字幕、Prompt 编译、路线决策与 TimelineManifest；使用临时 SQLite 和真实 Worker 的集成测试覆盖成功、取消、失败重试、运行中重启、`SUBMIT_UNKNOWN` 对账、`OUTPUT_MISSING`、换 Take 和局部失效，并验证不重复提交且旧产物保留；桌面 E2E 使用《别相信我》fixture 和 Mock Provider 走完整八镜闭环并中途强制重启；媒体层用 ffprobe/FFmpeg 检查交付规格和预览、Manifest、导出一致性，黑帧、冻帧与异常静音检测仅作诊断提示；Provider 日常合约测试使用离线响应 fixture，每种实际启用模式另做最小真实冒烟，非法能力组合必须在提交前阻止；最终表演、镜头、连续性和观看意愿严格执行 E007 人工验收。每次提交运行快速测试，阶段出口运行完整 Mock E2E 和媒体检查，真实 Provider 实验及盲评独立运行；上游已有问题与新增回归分开记录。

### D41. 文档按普通工程方式维护，不建立额外冻结机制

产品、架构、技术设计、实验方案和实施计划随项目正常更新，由 Git 自然保留历史。不设置 v0.1/v1.0 冻结层级、文档 SHA 锁定、额外审批门或证据链；只需清楚区分已经确认的决定和仍需实验的结论。实验达到约定判定条件后，直接更新相关文档、决策状态和实现。

### D42. ComfyUI 在 PoC 后作为可选媒体执行侧车

剧本、资产和 H3 主闭环 PoC 之前不实现 ComfyUI 集成。PoC 后，ComfyUI 可作为版本化的本地媒体工作流侧车，承载 H3 前后处理、定制图像、超分/插帧/修帧/抠像或用户明确要求的本地模型图。`WorkflowArtifact` 保存图、版本、输入/输出合同和 Agent Hint，结果登记回 ProjectDocument；ComfyUI 不接管剧本、项目、AgentRun 或时间线的总状态。哪些具体 workflow 成为默认能力，仍由真实样片中的明确收益决定。

### D43. 核心结构按闭环需要实现并正常迁移

Mock 阶段直接实现当前完整闭环所需的 schema、模块边界、API 和任务状态机，不等待全部真实模型实验结束。后续实验需要新增或调整字段时，使用普通 SQLite migration 和接口类型变更演进；Provider 特有参数保留在对应 Adapter 的请求/响应记录中，不污染通用领域模型。首版不为未知需求设计万能元数据系统，也不设置结构冻结流程。

### D44. 长镜头和镜间连续性检查作为 Agent 工具

视频探测、适应性抽帧、镜内时间序列和相邻镜头边界检查作为可复用工具供观察器、候选裁判和 Director Agent 调用。长镜头随时长增加采样密度，但稀疏帧不能证明未采到的动作不存在；工具只返回可见证据和通用 QA 分类。Agent 据此推荐重生成、换关键帧、拆镜或换覆盖机位，用户可以覆盖或忽略，不把建议变成硬阻断，也不为单个门、把手或进出动作扩张领域模型。

### D45. 真人 AUTO_DRAFT 采用同一核心上的可恢复有限推进

真实自动草稿不建立第二运行时，也不以一个长 HTTP 请求等待整集完成。它复用现有资产任务、H3 任务、Take、RouteEvidence、视频观察和 TimelineManifest，每次推进一个可观察动作并持久化；桌面在 Provider 等待时自动轮询，进程重启后继续。配置缺失、`SUBMIT_UNKNOWN` 或确有无法自主解决的外部状态可进入 `NEEDS_ATTENTION`；失败次数本身不触发停止，普通导演建议也不自动暂停流程。

### D46. 照片生成和参考包是 Asset Agent Tools

Planner 先声明镜头实际需要的主体、角色、角度、景别和状态，Asset Agent 再搜索当前/跨项目目录、检查覆盖、复用已批准资产或调用 GPT Image 从当前母版派生缺失视图，然后语义审查、登记系谱并为每镜选择最小参考集。不同剧本默认项目隔离：目录搜索默认只返回当前项目，自动母版复用只能从 `linked_asset_project_ids` 显式关联的系列/续集项目取用；全局跨项目搜索及导入仍是 Agent 或用户明确调用的可选 Tool，导入后须在目标项目重新审查。同一项目内也按镜头实际入镜范围选择资产：显式场景需求作为锚点，连续镜头继承最近的当前地点，不把对白里提到但未出场的人物、下一场地点或明确留在画外的道具一起塞进生成输入。场景默认先建单张 Master Wide；反打、局部、交互区和角色三视图只在分镜需要时生成。Agent 推荐可被用户忽略，不形成硬阻断或样片专用规则。

### D47. 通用视频与 AI 短剧共用运行时，Format 只做本地 schema/profile 适配

项目创建时可选择通用视频、AI 短剧、数字人或动漫 `ProductionProfile`；它们共享项目、剧本、资产、H3、QA 和时间线实现。Format 只保留本地 schema/profile 映射；`AI_SHORT_DRAMA` 不再自建竖屏节奏、爽点、喜剧或审校方法。活动 Production Method Catalog 分为 3 个 MiniMax Design 项目 Workflow adapter 与 8 个 MiniMax-H3 任务 Method adapter。`drama-series` 已提供通用短剧/漫剧主骨架，因此不再把通用真人短剧、动态漫或爽剧整体声明为能力缺口；具体节奏与爽点由 `StoryIntent`、`EndingContract`、项目 Style 和 Agent 选中方法共同决定。原有七个本地综合 Method 与三个自创 Narrative Skill 退出活动 Catalog。

四视图/轻量群像选择、场景俯视/全景/功能区 coverage、时间线分镜中的镜头/动作/台词/情绪拆分，以及 H3 的真实 `@` 媒体绑定，分别属于资产 Agent、导演/分镜 Agent 和 Provider Prompt Compiler。共享 Story Architect 只负责故事承诺、人物选择、因果推进和结尾意图，不读取这些下游 Provider/资产规则。

Design 的 Canvas/Hub/Stage Plan 只映射到现有 ProjectDocument/ProductionDocument、AgentRun/PlanRevision/WorkOrder 和派生 ProductionBoardView，不替换本地 Harness。`clip_group` 的目标数据语义是用 ProductionDocument 记录“多个 ShotPlan 合成一个生成片段”，不改写 ShotPlan 或 `timeline_beats`；当前尚未实现对应编译 Tool 和 H3 运行时消费者。`EndingContract` 始终优先于 `drama-series` 的连载默认；用户要求单篇完结时不强制留钩。

### D48. H3 视频参考是 Route Agent 工具，不是连续性门控

前镜完整 Take、前镜尾部运动窗口和低保真机位预演均作为可选 `H3VideoGuide` 工件进入 Route Agent。真实样本表明，H3 可能把原始前镜视频中的动作当作待继续内容，即使 Prompt 要求不要重复；因此完整 Take/尾窗主要用于确实延续同一动作与相近覆盖的镜头。反打或换机位时优先比较 Frame 路线，若使用机位预演则优先从当前镜头已批准起始关键帧生成。预演可帮助保持构图和粗运镜，但不能证明人体走位或终态动作必然完成。所有路线建议都可忽略；缺少 guide、Agent 不可用或用户坚持原路线都不阻断生成，也不为该能力增加场景专用字段、hash 冻结或额外审批门。

### D49. 多分钟 AUTO_DRAFT 已迁移到 AgentRun，不保留固定返工额度

这条决定记录 Phase 19 当时的实现：超过 90 秒的 `PlanningRun` 在 FINALIZE 后建立 Project 与 `AutonomousRun`，并曾以每镜计数限制返工。Phase 29 已将默认新建和新自动制作迁移到 AgentRun，由 Production Manager 复用同一 Asset、H3、QA、Director、Take、TimelineManifest 与导出 Tools；旧 AutonomousRun 控制面已经删除，独立 PlanningRun 也不再对外开放。当系统负责原创或扩写且配置了语言 Provider 时，无论时长都由 AgentRun 拥有 Content Planning Job，作为 `planning.advance` 背后的可恢复内容规划子流程；当前代码和持久化兼容名仍为 `PlanningRun`。ProjectDocument 是唯一项目内容真相，Provider 对账由 GenerationTask/H3 任务状态机负责。Scene Director 仍读取整集资产契约、当前场、当前对白与 compact handoff，但是否返工、返工哪一镜、继续同一路线或改变输入，都由 Agent 根据当前证据决定，不保留每镜额度、失败次数或其他计数式停止规则。

### D50. 整集 QA 归入 Verifier 责任，报告不成为门控

时间线建立后，Verifier 按场读取入选 Take、锁定剧本和现有 VideoInspection，再汇总跨场与整集问题。报告必须关联证据、场次和镜头；局部修复建议由 Leader 退回 Director Worker。Verifier/Provider 暂时失败时保留当前报告与错误证据，由 Agent 决定稍后重试、换路线或带明确未完成项继续，不用固定失败次数代替判断。

### D51. 产品采用 Agent + Skills + Tools，workflow 只作执行底座

用户面对的是能理解自然语言目标并自主完成制片的 Agent，不是节点编排器。活动 Skills 分为 3 个 MiniMax Design 项目 Workflow 和 8 个 MiniMax-H3 任务 Method；本地 Format 只做 schema/profile 映射。Multi-Agent 委派、单场/单镜规划、资产、H3、视频观察和时间线更新作为可组合 Tools/Harness 能力；AgentRun、PlanRevision、WorkOrder 和 Provider 任务状态机负责推进、恢复与对账。Design Canvas/Hub 语义映射到现有工件与派生视图，不覆盖这套 Harness。`assigned_agent` 仅标识委派给谁，Production Manager 必须基于完整目标显式填写 `production_skill_role`，运行时只按该字段装载职责与上游精确 section，禁止按关键词路由。

PoC 不建设远程市场式的动态 Skill Registry；当前最小 Skill 基础是 3 个 MiniMax Design 项目 Workflow 与 8 个 MiniMax-H3 任务 Method。上游源文中的 Hub/Canvas、确认步骤、固定时长和重试建议只作溯源内容保留，不进入 Harness 执行契约。用户明确值直接采用；明确无交互/交由 Agent 决定则继续并记录；明确要求确认才停靠；其余只就实质信息缺口询问。自然语言局部修改仍由 Agent 解析实体、属性和适用范围，再利用 `ProjectIndex` 与版本依赖生成 `ImpactPlan`，只重算实际受影响的分支。

### D52. 语义修改优先设计为按需索引和影响计划

语义修改是对现有项目对象的理解和局部重算，不是单独的创作 Agent 服务。最小合同为 `ChangeIntent`、从唯一 ProjectDocument 按需编译的只读 `ProjectIndex`、经真实 ID/依赖校验的 `ImpactPlan`，以及复用现有 Tools 和任务状态机的 Change Executor。首版不增加图数据库、向量库、第二份项目状态或换装等专用流程。LLM 负责理解自然语言和提出范围，确定性查询负责确认实际依赖；只有歧义会造成明显不同重算分支时才询问用户。

`POST /projects/{project_id}/agent-tools/plan-change` 已实现前三项合同。用户既可直接给自然语言，也可显式指定镜头/资产 ID；返回计划只描述根目标、实际依赖、会失效的 Take 和建议调用的已有 Tools，不修改项目。这个只读边界使 Agent 可以无人值守继续，也使桌面 UI、用户直接编辑和 Agent 修改共享同一依赖解释，不需要强制审批节点。

### D53. Prompt 与制片策略采用持续改进，不要求每次完整实验

制片过程中若发现 Prompt、资产选择、镜头拆法、检查顺序或 Provider 恢复方式明显改善结果，可记录为带证据的 ExperienceMemory 或更新普通工程实现。它不因此成为 MiniMax 官方 Skill，也不自动进入活动创作 Catalog。新的通用创作方法只来自可核验上游 Skill 或用户显式提供。

### D54. Story Orientation 先定义项目体验，再按任务装载成熟方法

当系统负责原创或扩写时，Story Orientation Agent 先从用户整段输入中分离 `StorySourceContract` 与 `StoryIntent`，保护其标志性反差和目标观众体验。通用真人短剧、连续剧和动态漫的叙事骨架由 MiniMax Design `drama-series` Workflow 提供，具体视频任务再按需要装载八个 MiniMax-H3 Task Method；只有当前两层方法确实都没有覆盖的能力才记录为缺口。选择由 Agent 基于完整语义判断，不使用关键词、正则、题材配额或类型门禁路由。

### D55. 确定性校验不限制正常创作内容

Schema 和校验器只维护引用完整性、时间范围、Provider 合约、可恢复执行和数据规模等工程边界，不对角色年龄、性别、关系、题材、结局或正常叙事元素设置创作门禁。已移除 `PlannedCharacter.age >= 18 && <= 80`，儿童、老人及题材需要的非人角色均可进入规划。规模上限、文本长度和 H3 单段 4–15 秒等仍属于上下文/技术约束；若未来某 Provider 对特定内容存在真实限制，由对应 Adapter 明确报告，不反向污染通用故事模型。

### D56. 分镜时长与 H3 单次生成时长分离

`ShotPlan` 表示叙事和剪辑意义上的镜头，时长由内容、表演和运镜决定，可以短于 4 秒，也可以超过 15 秒。MiniMax-H3 的限制只作用于一次 Provider 请求：每个请求必须是 4–15 秒的整数。执行规划应把长镜头编译为多个连续的 H3 生成段，把不足 4 秒的剪辑镜头生成带安全余量的最短合法素材后在时间线裁切；不能把 H3 的窗口反向写成分镜规则，也不能直接提交超过 15 秒或非整数时长。

长镜头生成段之间需要继承前段终态、可选尾窗或关键帧，并由镜内/边界观察检查人物、空间、动作、声音和运镜连续性。Director Agent 仍可根据质量风险建议把它改成多个剪辑镜头、换覆盖机位或保留长镜头并技术分段，用户可以忽略。当前 Planner 已移除 4–15 秒分镜门限；在重新进入真实视频生成前，还需把“一镜多生成段、逐段 Take、段间 QA 和 Timeline 拼接”接入 H3 Route Plan，现阶段不会用不完整实现触发付费生成。

### D57. Verifier 以目标观众验收并维护 Issue Ledger

Verifier 的判断依据是 `StoryIntent`、`StorySourceContract` 和人真正能读到的完整剧本。它为语义上相同的问题保留稳定 `issue_id`，在 Issue Ledger 中累积状态、文本/修复证据、演变说明、已接受取舍和 handoff。未通过时，Leader 把具体问题退回原 Writer Worker；严重度、问题数和轮次只是证据，不触发代码门槛。`SCRIPT_CRITIC`、`STORY_REPAIR`、`REVIEW_ROUTER` 和 `FINAL_COLD_READ` 只作历史持久化阶段名，不再对应独立 Agent/Skill。

### D58. 修订先对照来源承诺与创作意图，再做局部改稿

`StorySourceContract` 保存用户显式承诺、明确禁区、开放选择和后续权威覆盖；`StoryIntent` 保存目标观众体验、戏剧引擎、推进策略、调性/可信度、受保护元素与偏移风险。它们随大纲、剧本版本和 PlanningRun 恢复，不只存在一次 Prompt 中。`SCRIPT_LOCKED` 普通文本由 Story Orientation Agent 依据完整语义结构化为 `StorySourceContract.locked_dialogue`；API 调用方也可以直接提交结构化锁定台词。确定性代码只保护逐字文本、来源顺序与调用方给定结构，不用正则、冒号模板或关键词猜测 speaker、旁白和内心音。Leader 依据用户约束、当前工件与 Verifier 证据界定修订目标；用户承诺不能为降低制作复杂度而静默删除。

Writer Worker 只处理 Leader 指向的 issue ID、场次和全局元数据，每完成一场就保存进展；即使需要全剧调整，也通过逐场 Patch 而不是一次性覆盖整稿。Verifier 先对照 Issue Ledger、受保护承诺和修订证据验收原问题与新回归，再脱离修订说明，对用户简报、`StoryIntent`、`EndingContract` 和当前可读全稿做自然通读，并将结论写回 Issue Ledger。它直接接受当前稿，或带具体证据退回 Leader，再由 Leader 交还原 Writer Worker 聚焦修订。收敛不设固定审校/修订轮数、问题数、严重度或时间阈值。`SCRIPT_CRITIC`、`STORY_REPAIR`、`REVIEW_ROUTER` 和 `FINAL_COLD_READ` 只是历史持久化阶段名，不再构成主动链路。用户直接改稿或明确要求 Agent 改稿时，本次指令作为 authoritative override，后续不能擅自改回。

### D59. 约 4 秒到数十分钟共用分层剧本 Tools

不按短片、3 分钟或 30 分钟切换 workflow。约 4 秒的单节点内容到数十分钟剧本都共用 `Leader 定义内容目标 → Story Architect/Writer Worker 逐场产出 → Verifier 验收 → Leader 按证据续行或局部返工`的责任拓扑，时长只影响场次节点数、单场时长和需要取回的文档上下文。场次数与跨场修订范围由 Agent 根据内容和问题判断；原先 40 场、跨场 12 场这类低常数创作上限已移除，不建立固定时长档、节点配额、总超时或审校轮次上限。长任务以蓝图、逐场写作和逐场修订单元持久化推进。需要分镜时，Leader 再交给 Director Worker 并由对应 Verifier 验收。

Blueprint、单场写作、版本化剧本检索、修改范围建议、Scene Patch、Patch 合并和局部复审都必须是可独立调用的 Agent Tools。只要系统负责原创或扩写且配置了语言 Provider，所属 AgentRun 就使用 Content Planning Job；该作业当前以 `PlanningRun` 实现，只提供顺序内容规划游标、持久化和恢复。AgentRun 决定何时推进、哪些工件需要局部修改以及何时完成，而不是按时长选择是否使用该作业。用户指定某场、某角色或某段台词时，Agent 先检索当前 run/revision 的 Story Bible 和 Scene 文档，建议最小影响范围；用户明确给出场次时直接覆盖该建议。未选中场次不重新生成，台词索引由确定性代码重建。

Verifier 判断需要修订时，Leader 优先把 issue ID 和 `affected_scene_sequences` 交回原 Writer Worker 执行局部 Patch；只有标题、中心设定、全局角色/地点契约、总时长或大部分因果主干真正需要改变时，才更新 Blueprint 元数据，随后仍逐场持久化修订。这是 Agent 建议而非门禁，用户始终可覆盖。详见 [Phase 22](33-phase22-hierarchical-script-tools.md)。

当前工程已实现 Story Orientation、合同/意图持久化、Issue Ledger、局部修订游标，以及三个 MiniMax Design 项目 Workflow 与八个 MiniMax-H3 Task Method 的分层装载。`drama-series` 已提供通用短剧/连续剧的创作主干，`AI_SHORT_DRAMA` 继续负责把项目意图适配为本地结构化合同，而不是承担一套平行叙事方法。只有上游方法和现有本地能力都未覆盖的具体需求才记录为能力缺口。真实剧本与成片质量仍需真实模型样本验证，不因 Prompt 与结构化测试通过就预告质量。

### D60. 专业 WorkOrder 以单步 turn 持久化，不在一次请求内隐藏循环

专业 Agent 请求 Tool 时，第一次外部 `execute` 只让模型决定下一步，并把 Tool、参数和理由写入 `WorkOrder.specialist_turns`；下一次只执行该已持久化 Tool 并写回结果；再下一次才让 Agent 阅读历史、继续请求其他 Tool 或返回 AgentResult。这里没有固定 Tool 次数上限，终止由专业 Agent 和 Production Manager 根据目标与证据判断。

若进程在 Tool 执行中重启，恢复逻辑将对应 `IN_PROGRESS` turn 标为 `INTERRUPTED`、写入结果未知的中断证据，并把 WorkOrder 置为 `NEEDS_ATTENTION`。Agent 应先检查规范项目状态，再决定对账、重试或换路线；Harness 不自动重放可能已有外部副作用的调用。该合同服务于进度可见、恢复和正确性，不是新的审批门。

### D61. 剧本时长以 Agent 的自然表演证据判断

蓝图中的场次秒数和总和只是规划分配，不证明实际成稿能在目标时长内自然表演。Writer Worker 对当前输出的对白、动作、停顿、反应与观众读清信息所需时间给出 `performance_timing_assessment`；Verifier 再对完整文本做同样的语义复核。只有成稿明确写成同时发生、蒙太奇或可连续掠过时才按并行处理，不替剧本假设尚未写出的快切。该证据帮助 Agent 取舍、合并、修订或请求上游重规划，不转化为字数、语速、场次数或时长偏差的代码阈值。

### D62. 剧本逐场继承状态，EndingContract 保护最终状态而非仪式清单

Writer Worker 和 Verifier 都把前文已建立的人物关系、群体态度、资源与权限当作后续场的入场事实。换地点、换见证者、换道具，或把口头认可改成文件、挂牌，不会自动把同一次震惊、让位、授予或交接变成新升级；后续场需要产生新的选择、代价或后果。`EndingContract` 描述本篇必须到达的最终状态、最后画面/台词意图和续集策略，不要求 Writer Worker 把每个 closure 分别演成认可、授予、核验或交接仪式。多个 closure 若只证明同一次状态变化，应由最有力的决定性动作统一兑现。这是通用角色的叙事判断，不是固定节拍或题材模板。

### D63. H3 全部合法候选由 Route Agent 显式选择

Route Tool 把当前镜头技术上合法的全部 Direct、Frame、Reference、Video Guide 和可复用 Take 候选，连同类型化 QA、观察与缺失输入证据暴露给 Route Agent；代码不用 top-k、候选序号、分数、QA 状态或失败类别预先截断创作选择。Route Agent 显式返回候选 ID、需要的参考媒体 ID 和本批生成意图，可以选择一个或多个候选，不设固定数量。同一 Route Plan 重复执行只对账且保持幂等；Agent 根据新证据决定继续探索时，建立新 Route Plan 与显式候选批次。没有语言 Route Agent，且调用方也没有显式决定时，计划保持 `NEEDS_AGENT_DECISION/PARTIAL`，技术基线不自动提交或计费。

### D64. 创作领域是工作区软偏好，不是运行时路由

MiniMax Design 3.0.11 的“短剧/漫剧、影视/混剪、带货/电商、广告/营销、MV/音乐视频、动画/二次元、知识/科普、其他”在当前客户端中只确认了持久化与分析上报，未发现 Agent Profile、Router 或 Skill Loader 读取。本项目若在 PoC 后引入同类入口，使用 `CreatorInterests/SkillCategory` 作为首页示例、Skill 发现和初始建议的弱先验。当前请求、项目工件和用户显式选择始终优先；偏好不限制 Tool/Skill，不改写 ProductionProfile，不做关键词路由。

### D65. 本地 Agent Team 以 Production Manager 为 Leader，按交付边界建立 Worker/Verifier

`AgentRun + ProductionManager` 继续持有用户总目标和 PlanGraph；Agent-owned `PlanningRun` 仍是顺序、可恢复的 Content Planning Job，不与 AgentRun 合并。剧本批准后的资产、镜头、声音、调研和审查等可独立产出且可独立验收的工作，才拆为带依赖的 WorkOrders；不固定启动 copy/image/video/audio 四个 Agent。每份进入权威项目状态的交付物都必须能追溯到 `Leader → Worker → Verifier` 责任拓扑；这三者是责任而非强制的三个进程或三份 WorkOrder。当独立通读、视觉观察、来源核对或跨工件验收能增加价值时，Verifier 可以是独立 WorkOrder；其余情况也不得省略验收责任和结论。

本地 `TeamExecutor` 已实现锁内认领当前 PlanRevision 的就绪 WorkOrder、锁外并行执行无共享写入的工作、锁内提交结果。ProjectDocument 的 `WRITE/EXTERNAL` Tool 统一经过项目级串行写通道，`READ` Tool 可并行。WorkOrder 以 `next_owner` 明确下一责任方；统一 `advance` 每次只推进一个 Worker batch 或一个 Leader 单元，同一 Worker 所有权内的模型决策和 Tool 回合可在 batch 内自动续跑且每轮落盘；Worker 返回不会在同次调用里隐式触发 Leader 收尾，批次失败可见并停止。新 PlanRevision 必须显式声明继续携带的未完成 WorkOrder；未携带任务转为 `CANCELLED`，迟到结果保留为 `stale_handoff` 历史证据但不再合并 Action、Decision、Experience 或权威工件。首版不引入 Redis、Celery、租约、分布式协调或 Agent 微服务集群。

### D66. Shared Board、上下文隔离和画布都是权威工件的投影

`ProductionBoardView` 从 ProjectDocument、AgentRun、PlanRevision、WorkOrder、DecisionMemory、AgentResult 和 Issue Ledger 派生，展示目标、已确认决定、任务状态、工件变化、阻塞和下一步，不成为第二份可写项目真相。Leader 为 WorkOrder 语义选择 `ContextManifest`：当前目标、精确工件版本、邻接边界、必须继承的决定、相关经验与预期输出；Loader 只解析指针与校验 ID，不做内容路由。默认制片工作台、高级 Agent 生产图和 ComfyUI 媒体图分属三个视图/执行层，都不取代 ProjectDocument。详见 [MiniMax Design、Agent Team 与 H3 Skills 蓝图](43-minimax-design-agent-team-product-blueprint.md)。

### D67. Production Method 的结构化工件统一进入版本化 ProductionDocument

品牌事实表、信息架构、精确文案、音乐结构、歌词时间、教学结构、参考拆解记录等跨角色制片工件，不为每种 Method 增加一套顶层表或自由文本白板。它们统一使用 ProjectDocument 内的版本化 `ProductionDocument`：稳定 document ID、不可变 version snapshot、父版本、结构化 payload、来源 ArtifactRef 与 USER/AGENT/IMPORT 来源。来源引用必须属于当前项目、在需要时带精确版本且可解析。通用 Artifact Tool/API 提供创建、修订、读取和列表；修订必须基于当前版本，旧版本完整保留。带专属不变量的 `audio.master`、`audio.structure-map`、`audio.timed-text-lock` 只能由对应音频操作写入，避免通用入口绕过媒体、时间窗和版本校验。某类工件成熟到需要独立高频查询或确定性运算时再升格专用领域模型，不预先造万能 schema。

### D68. 音乐主轴先固定 Master Audio 与精确文字，再让镜头围绕它编排

Agent 可把当前已批准的本地音频 AssetVersion 及其选定窗口绑定为版本化 Master Audio。AudioStructureMap 只保存本机实际测量到的基础媒体信息、BPM、beat、onset、energy 或 key moment；没有兼容分析库或无法测量的维度明确留空，不由模型伪造节拍。歌词、标题、口播或其他必须逐字保留的内容使用绑定到当前 Master Audio version 的 `TimedTextLock`。`time_origin + duration` 是音乐主轴项目的最终时间线长度；画面不足时保留最后一帧，画面过长时裁切。TimelineManifest 只投影这一条主音轨并替换逐 Take 原生音轨，TimedText 直接形成精确字幕，导出统一规范声画长度；重绑音频保留历史文档，但清除不再对应的当前音频结构/文字指针并失效当前时间线。镜头是否逐拍切换仍由 Agent 判断，不增加节拍门控。

### D69. H3 五类合法输入模式共享精确资产准备与状态版本

Route Agent 可在技术上合法时显式选择 `DIRECT`、`FRAME_FIRST`、`FRAME_LAST`、`FRAME_FIRST_LAST` 或 `REFERENCE`；不因当前没有 preparation 就把尾帧/首尾帧候选藏掉，也不因已有准备工件就自动选择某一路线。首帧、尾帧和首尾帧缺失时形成可恢复的 asset-preparation 交接；READY 输入必须仍是 current-approved AssetVersion。父 Route Plan 对剧本、Bible、声线、所有镜头和 video guide 做完整 freshness 检查，过期计划与过期 preparation 不参与执行。`version` 表示编译计划版本，`state_revision` 表示每次持久化状态变化，Tool/API/ArtifactRef 使用精确 state revision 交接；这些是防止错用旧输入的普通正确性合同，不是创作审批门。

## 2. 已完成确认的五项基础

### Q1. 首个样片美术方向（已确认）

首个固定质量样片已选择真人都市短剧，见 D12。这是历史实验输入，不是当前 AI 原生短剧的默认 Style；当前范围见 D47。

### Q2. 首个版本目标时长

已确认 45–60 秒、8–10 个最终剪辑镜头，固定样片先按约 50 秒、8 镜执行，见 D15。这个长度选择服务于实验可归因性，不是预算限制。

### Q3. 第一组质量路线

同批比较 `direct_audiovisual` 与 `image_guided`：Direct 是对照，GPT Image 2 是增强路线变量。视频生成固定使用 H3，分别比较 H3 Direct、H3 reference 和 GPT Image 关键帧 → H3 frame route，不做其他视频模型横向比较。

### Q4. 底座策略

已确认采用 **LTX Desktop 壳 + 自建短剧/任务核心**，只做一个供应商中立 Mock Spike；替换 `localStorage` 项目真相源、内存任务和 LTX 专用契约。Jellyfish 不并行实现，只在统一状态源或 TimelineManifest 无法合理接管现有壳时作为结构性回退。

### Q5. 首批真实实验样本量

价格只记录。建议每个图像条件至少 5 次，并检查其下游视频；Direct 与 Image-guided 对同一组高风险镜头各生成足够候选，使主要结论不依赖一次抽卡。音频路线同样按稳定率决定样本量，不做逐批预算审批。

## 3. 质量基准之后再决定的事项

### Q6. GPT Image 2 的覆盖范围

首轮就建立角色定妆、场景锚点和代表性首帧，并比较下游动态结果。若质量非劣且一致性更强，再决定是覆盖全部适合镜头，还是只覆盖身份、空间和构图高风险镜头。

### Q7. 字幕时间来源（已确认）

- 原生音频稳定：批准台词 + STT/音频对齐；
- 外部 TTS：优先使用 TTS word-boundary；
- Provider 返回可靠对齐信息：优先使用 Provider 数据；
- 无可靠自动时间：允许人工微调后回写 TimelineManifest。

### Q8. 声线资产是否成为强制项（已确认）

不成为强制项。用户可上传、录制或绑定已有音频，Route Planner 在有该角色台词的镜头中把它作为 `reference_audio` 候选；未提供时继续使用 H3 原生声音，不阻断无人值守流程。当前只支持该输入，不为它新增声音 QA。

### Q9. 是否需要外部口型模型（由实际问题证据决定）

只有所选原生/参考音频路线无法通过正脸、侧脸和多人对白探针时再选。不要提前集成 MuseTalk、LatentSync、Wav2Lip 等后处理。

### Q10. 模型内多镜的分组粒度

由连续性与局部返工实验决定；不因“理论上更连贯”就把模型最大窗口塞满。

## 4. 真实质量实验前的工程验收状态

当前架构与实验协议已经确认。Phase 0–11 已完成从工程基线到无人值守 Mock、真实 H3 从零单集、音视频 QA、独立重复、三版匿名材料和可选角色声线输入。当前工程门状态为：

- LTX Desktop 上游构建、测试和桌面打包基线可复现；
- SQLite/API、持久化 Worker 和固定《别相信我》fixture 完成；
- Mock 垂直切片通过排队、取消、重试、重启、对账、Take、局部返工、时间线和导出测试；
- 预览、TimelineManifest 与最终导出的音画结果一致；
- LTX Desktop 应用代码与未采用的 LTX 模型权重保持清晰分离；
- H3 Adapter 已完成 capability 校验、任务恢复、用量记录和真实冒烟；
- 四种输入归一化、AgentRun 总调度、适应性抽帧、镜内/镜间观察和观察报告到 Director Agent 已完成；旧 `AutonomousRun` 只作为历史阶段记录；
- Route Planner 已能向 Agent 暴露当前版本中技术可用的 H3 Take 与 Direct、Frame、Reference 候选；只有 Agent 或调用方显式选择后才复用或提交。《别相信我》已完成 9 镜、57.13 秒全 H3 时间线和技术导出；
- 没有可复用 Take 的项目已验证图像资产准备、H3 异步任务、原生声音 QA、Director 语义修复、时间线与成片导出的历史闭环；当前运行由 Route/Director Agent 根据证据决定候选、返工与停止，不再使用 Director 有界重试或每镜返工额度。E007 三次独立 `AUTO_DRAFT` 与用户汇总定性验收已完成，C 版成为当前最终版。原三人数字评分数据未收集，因此只关闭本轮产品验收，不将其表述为三人评分 benchmark。

Mock 工程门的实现和验证证据见 [Phase 1：供应商中立 Mock 垂直切片](10-phase1-mock-vertical-slice.md)。

没有合理质量或控制增益预期的资源不需要为了凑栈测试；有正向预期的资源可以主动进入基准。闭源平台不需要账号实测结果。

## 5. 已确认实施顺序

1. 导入并验证 LTX Desktop 上游基线；
2. 建立 SQLite/API 领域核心和固定 fixture；
3. 实现持久化 Worker、Mock Provider、恢复与失效传播；
4. 接通现有工作台、Take、局部返工、时间线和导出，完成 Mock 闭环；
5. 接入唯一视频模型 H3，固定 768P 执行视觉路线和镜头分组实验；
6. 接资产工作台与 GPT Image，验证下游动态增益；
7. 接 GPT-5.6-sol 自动规划和视频观察工具，先完成无人值守 Mock 闭环；
8. 已验证 H3 原生声音，并把真实视频、图像、音频 QA、自动审片、路线选择和局部重试接入三次真实 `AUTO_DRAFT`；
9. 完成成片人工验收和最终片选择；若后续需要可比较的 benchmark，再补三人数字盲评。随后依据实际失败证据决定参考音频、ComfyUI、内置素材库等增强。
