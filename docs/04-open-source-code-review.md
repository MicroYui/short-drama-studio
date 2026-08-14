# 开源源码审查与复用建议

本章结论来自浅克隆后的固定 commit 静态审查。没有安装全部依赖，也没有调用真实付费 Provider。README 的功能宣传只有在源码路径能对应时才记为 `SOURCE-CONFIRMED`。

## 1. 快照与总评

| 项目 | commit | License | 最强项 | 主要缺口 | 建议角色 |
| --- | --- | --- | --- | --- | --- |
| Toonflow | `bc61ec7` | Apache-2.0 | 短剧 Skill、审阅闸门、分镜连续性提示 | 无可审计的声音/字幕/最终合成；Provider 任务较粗 | 复用 Skill 与领域模型 |
| ComfyUI | `7fe8a613` | GPL-3.0 | 图执行、节点生态、局部修复与本地模型 | 不是剧本/项目系统，任务历史非生产级持久化 | 可选媒体处理侧车 |
| ViMax | `05a48943` | MIT | Idea/Script-to-video、机位树、首尾帧拆解 | 横屏硬编码、音频/字幕/口型缺失、拼接简单 | 复用规划算法与类型 |
| Jellyfish | `a9678194` | Apache-2.0 | 项目/资产/准备度/任务架构清楚 | 时间线是占位壳，无音频与成片链路，媒体契约难表达多模态角色 | 长期控制面候选 |
| Wind Comic | `c83e1cf5` | MIT | 一句话、9:16、FFmpeg、字幕、音频、QA、预算模块丰富 | 系统面很宽；当前候选模型适配器过时或不完整 | 拆用成片/QA 模块，不宜直接全盘继承 |
| LumenX | `f2a02e23` | MIT | Pipeline-first 产品、模型目录、候选工作台、TTS/合成 | Provider 契约偏现有模型；导出选项不完整 | 快速改造候选，不因已有某个模型适配器加分 |
| LTX Desktop | `24a8440d`（`v1.1.0`） | Apache-2.0（应用；权重另授权） | Electron 桌面壳、专业时间线、Take/Retake/Extend、补空隙、真实 FFmpeg 导出 | 缺少短剧语义模型；项目元数据与任务恢复较弱；Provider 强绑定 LTX；预览与导出能力不完全一致 | 桌面编辑壳与底座 Spike 强候选 |

完整仓库链接见 [来源清单](sources.md)。

## 2. Toonflow

### 源码确认的优点

- Script Agent 以“故事骨架 → 改编策略 → 剧本”三阶段生成，并在关键阶段请求确认；
- Production Agent 分为决策、执行和监督角色，主流程覆盖导演方案、派生资产、分镜表、分镜面板和图像；
- 分镜 Skill 针对竖屏短剧限制单段长度，对长对白拆镜，要求保留原台词；
- 分镜面板会携带上一镜人物位置、朝向和动作终态；
- 模型路由区分多参考、首尾帧和不同视频模型；
- Electron/Express/TypeScript + SQLite，后端与 Skill 开源，Apache-2.0 便于参考与改造。

### 缺口

- MiniMax 适配器是旧 Hailuo 路线，不是 H3 `/v2/video_generation`；
- TTS 是空实现，源码未找到 STT、SRT、口型、FFmpeg 或确定性最终合成；
- Provider 任务 ID、请求指纹、价格快照和结算状态没有形成完整持久化；
- 视频成功主要以 Provider 返回和文件保存判断，没有成片级 QA；
- 前端以编译产物为主，深度修改 UI 的成本和透明度不如完整源码项目。

### 复用结论

直接吸收 `data/skills` 中的短剧节拍、分镜表、连续性和模型提示规则，并改写为规划模型的结构化输出；当前可先用 GPT-5.6-sol，但不把 Skill 与具体模型绑定。不建议把 Toonflow 整仓作为最终底座。

## 3. ComfyUI

### 源码确认的优点

- JSON 图提交、节点校验、队列、缓存、局部执行和 WebSocket 进度成熟；
- Custom Node 可以封装本地模型或媒体处理；
- Workflow JSON 可以记录节点版本，适合可重放的图像修复、超分、插帧和局部重绘；
- 官方蓝图包含首尾帧视频、视频拼接、抽帧、插帧和音频相关示例。

### 缺口

- 它是执行图引擎，不是剧本、角色、分镜、候选、审批和时间线的产品域；
- 核心队列/历史主要是内存结构，不能直接替代持久化生产任务系统；
- 当前候选媒体资源以 API 调用为主，把整个主工作流搬进节点图会造成双重状态；
- 当前检查的官方蓝图没有提供可直接采用的中文对白、字幕和真人口型全链路；
- GPL-3.0 对分发方式有影响，需要按实际使用方式审查，不在本调研中下法律结论。

### 复用结论

首版不依赖 ComfyUI。后续只有在需要本地局部修复、超分、插帧、抠像、色彩或开源口型模型时，作为无项目状态的处理侧车接入。

## 4. ViMax

### 源码确认的优点

- `Idea2VideoPipeline` 可从创意扩写故事并拆成多个场景；
- `Script2VideoPipeline` 依次生成角色、多视图、Storyboard、首/尾帧描述、机位树、关键帧和视频；
- Shot schema 将 first frame、last frame、motion 和 audio description 分开；
- Camera tree 用较宽的父机位生成新角度，再把新角度传播到后续镜头；
- 支持先生成文字工件并审阅，再启动昂贵媒体生成；
- MIT，Python 代码清晰，容易抽取算法。

### 缺口

- Provider 协议只有通用 prompt + reference paths，无法表达 H3 的媒体 role、互斥模式、音频 reference、计费和异步任务；
- 音频只是写进视频 prompt，数据模型中的说话人/口型字段仍被注释；
- 没有 TTS/STT、字幕、强制对齐和口型 QA；
- 关键帧默认 `1600x900`，整体偏横屏；
- MoviePy 只做简单串联，缺少统一帧率、音量、字幕、安全区和完整时间线；
- 断点续跑按“文件是否存在”判断，没有输入版本 hash，容易误用过期工件；
- API 网络异常时可能重新提交而没有先持久化 task ID。

### 复用结论

抽取 typed schema、首尾帧拆解、Camera tree、ReferenceSelector 和审阅后渲染的思想；不直接运行其媒体后端。

## 5. Jellyfish

### 源码确认的优点

- 明确区分“分镜信息确认状态”“运行时任务状态”和“视频生成准备度”；
- 项目、章节、镜头、角色、演员、场景、道具、服装、文件和候选关系为持久化实体；
- FastAPI + SQLAlchemy + MySQL/Redis/Celery，异步任务有进度、取消、结果和业务关联；
- Provider 与任务 contract 分层，前端通过 OpenAPI generated client 同步类型；
- 分镜编辑页负责准备，生成工作室负责执行，产品职责清楚；
- 图片/视频生成前有 context、derive、submission 三段式构建，适合插入 ReferencePlan。

### 与单用户边界的适配

- 在本轮固定 commit 的后端业务模型与路由检索中，未发现产品用户 JWT、登录会话、组织/租户、成员关系、RBAC 或按 `owner_id` 过滤项目的耦合；
- 后端出现的 `Authorization` 主要用于调用外部模型 Provider，必须保留，但它不是 Jellyfish 产品用户登录；
- 前端本地 store 中的用户名/“系统管理员”角色、设置页角色选择和退出菜单属于表层 UI，没有对应的后端权限体系；首版可直接隐藏或删除；
- 因此不需要为采用 Jellyfish 而继承一套账号系统，也无需给 Project、Asset、Shot、Task 等实体补用户归属字段。

### 缺口

- `timeline.py` 仍明确写着“占位壳”；
- 视频输入 contract 只有 prompt 和首/尾/关键帧，无法表达 H3 reference image/video/audio；
- Provider task ID 存在于结果对象，但底层轮询对象以内存字段为主，尚未形成 H3 提交未知后的完整恢复/对账模型；
- 没有可用的 TTS、STT、口型、字幕和最终 FFmpeg 成片路径；
- 当前 Provider 主要是 OpenAI video 与火山，不包括 H3；
- README 中的一键导出能力超前于当前后端实现。

### 复用结论

如果优先长期可维护性，Jellyfish 是最好的“控制面”候选。它现有后端已接近本项目需要的单用户形态，只需清理前端账号外观；主要工作仍是新建供应商中立的多模态 contract、媒体时间线和成片模块，工作量较大但边界清楚。

## 6. Wind Comic

### 源码确认的优点

- 默认支持一句话输入与 9:16 短剧模板；
- 存在导演、编剧、角色 DNA、Style Bible、分镜、候选、节奏和连续性模块；
- 有真实的 FFmpeg composer、抽尾帧、字幕/ASS、BGM、ducking、转码、音轨检查和成片检查；
- TTS、口型 Provider、原生音频分流和本地 Mock Provider 都有具体代码；
- 包含成本归因、预算闸门、Provider 健康、候选重生、质量门禁和大量单测文件；
- 原生音频代码明确承认“原生语音不保证逐字台词或固定角色声线”，判断较务实；
- MIT，所有产品层代码可读。

### 缺口

- 代码表面超过两千文件，功能和版本层叠明显，改动影响面较大；
- 当前 OpenAI 图像插件默认 `gpt-image-1`，只做 generation，明确不支持参考图编辑；
- 当前 MiniMax 实现仍走 `/v1/video_generation`、Hailuo 2.3/S2V-01，不是 H3 Context-IR 与 `/v2/video_generation`；
- VideoProvider contract 没有通用 reference audio/video role，H3 会迫使它扩展；
- Provider 提交、轮询和持久化散落在不同 service，尚未证明所有付费调用都能在进程重启后对账；
- README 中的成千上万测试与效果宣称本轮没有完整运行验证；
- 内置“2D 口型”只能作为风格化兜底，不能等同于真人视频口型。

### 复用结论

不建议直接以整个仓库为首版底座。优先抽取或重写：

- `video-composer` 与字幕/音频工具；
- `TimelineManifest` 可借鉴的 xfade/时长计算；
- native audio 分流思想；
- 连续性表、角色 DNA、Style Bible；
- QA、Mock Provider、预算记录和局部重生。

## 7. LumenX

### 源码确认的优点

- Studio 是明确的 pipeline-first 产品：剧本 → 分镜 → 资产 → 视频 → 合成 → 导出；
- FastAPI + Next.js，MIT，代码规模比 Wind Comic 更可控；
- 已有角色/场景/道具资产、角色多视图、候选视频、选片、任务状态、尾帧提取和合成；
- Model Catalog 把 Provider、模式和参数集中管理；
- `mulerouter.py` 已包含 GPT Image 2 generation/edit 和尺寸规范化；
- VideoTask 持久化 `provider_task_id` 与 `provider_request_id`；
- 支持 TTS 声线、对白音频 stale 检查、配音视频、BGM 与 FFmpeg 合并；
- 本地优先，适合快速启动 UI Spike。

### 缺口

- GPT Image 2 不是 OpenAI 直连，而是 MuleRouter/MuleRun；若最终选择官方 API，需要重写 adapter；若不需要图像生成，则不应为此改造；
- 没有 MiniMax-H3；现有视频模式和字段混合了多个厂商特有参数；
- `ExportManager` 仍写 dummy 文件，API 也注明 resolution/format/subtitles 参数尚未应用；主 `merge_videos` 虽是真实 FFmpeg，但只是重编码串联与可选 BGM，并不是完整 TimelineManifest 渲染；
- 部分 Provider task ID 只在特定路径中途持久化，MuleRouter 本地轮询仍在一个同步调用内；
- 声音功能以外部 TTS 为核心，不能自然表达“根据所选视频路线决定是否需要外部音频”；
- 当前仓库对 9:16 的端到端覆盖和字幕安全区仍需运行验证。

### 复用结论

如果目标是最快做出可操作的工作台，LumenX 曾是强候选。改造重点是把 Provider contract 和 TimelineManifest 做成能力驱动；当前视频侧只接 H3，未使用或占位的旧 Provider 出口应清理。

## 8. LTX Desktop

本轮固定审查 `v1.1.0` 对应 commit `24a8440d`。它是 Beta，前端仍在重构；本轮没有下载数十 GB 模型或实际运行生成，只做静态源码、CI 和测试结构审查。

### 源码确认的优点

- React/TypeScript Renderer + Electron 主进程 + Python/FastAPI 本地后端，已经形成可安装桌面应用所需的进程生命周期、Preload 类型桥、文件选择、模型管理和 FFmpeg 集成；
- Gen Space 将生成结果作为资产保存，支持多个 Take、当前 Take、重新生成、局部 Retake，以及向前/向后 Extend；
- 编辑器不是简单缩略图列表：已有多轨时间线、Source/Program Monitor、裁切、变速、倒放、音量、字幕轨、SRT 导入/导出，以及 FCP7 XML/FCPXML 互操作；
- “补空隙”会读取上一片段末帧、下一片段首帧和相邻 prompt，建议桥接 prompt，再把生成结果插回精确 gap；这套交互很适合短剧镜头接续；
- 有真实 FFmpeg 成片路径：按时间线展平视频/图片、填黑场、统一帧率、混音、烧录字幕，并支持 H.264、ProRes 与 VP9；
- 支持本地或 API 生成，Windows/Linux CUDA 与 Apple Silicon MPS 都有路径；`v1.1.0` 还加入 LoRA/IC-LoRA 库、模型版本管理、Extend 和 Stage Cache；
- 后端测试数量和跨 Linux/macOS/Windows 的 CI 明显强于一般 Demo，另有冷启动、显存、输出完整性与 soak/leak 性能检查；
- 没有产品用户、组织、RBAC 或租户系统。Hugging Face 登录只服务于受限模型下载，LTX/fal/Gemini Key 只服务于 Provider，符合单用户边界。

### 对短剧平台最有价值的可复用机制

1. **生成空间与编辑时间线并列**：先生成和选 Take，再进入剪辑，不把一次生成直接当成最终镜头；
2. **Gap Fill**：用左右镜上下文生成桥接片段，比只在全局点击“重做”更符合连续性修复；
3. **Retake / Extend**：把局部改表演、续镜头和整体重生成分开；
4. **媒体文件与工程壳**：桌面文件路径、导入、预览、代理式操作和导出基础无需从零搭；
5. **专业交换格式**：SRT 与 FCP XML 让自动生成不是封闭终点，必要时可交给人工精修；
6. **本地模型生命周期**：下载、版本选择、磁盘/显存检查、LoRA 管理和计算后端选择都可借鉴，即使最终不使用 LTX 权重。

### 不能直接继承的核心缺口

- `ProjectV2` 主要只有 bin、媒体 `Asset`、track、clip、subtitle 和 active timeline；没有 `ScriptVersion`、角色/场景/道具、ProjectBible、ShotPlan、ReferencePlan、连续性状态或 QA；
- 项目 JSON 存在浏览器 `localStorage`，媒体文件才复制到项目目录。对长周期短剧工程、版本迁移、依赖失效和可恢复任务而言不够可靠；
- FastAPI 后端按单客户端、单个内存 `AppState` 设计，同时只有一个活动生成槽。前端 recovery marker 只能在后端进程仍活着时重新轮询，不是重启后的持久化恢复；
- 外部 API job ID 在同步轮询调用中使用和记录日志，但没有落到持久化任务表。进程在提交后退出时，无法实现 `SUBMIT_UNKNOWN` 对账；
- 视频请求直接写死 LTX fast/pro、本地 LTX-2.3、单图片/单音频等字段，无法自然表达 GPT Image 关键帧、H3 多参考 role、frame/reference 互斥和供应商中立的候选任务；
- Gap Fill 的上下文和交互值得复用，但当前视频生成并没有通用的“双边界帧 + 多角色/场景参考”能力；尾帧侧通过反向生成/倒放等技巧处理，不能当作任意模型的连续性保证；
- 导出器是真实可用的基础版，但当前传入参数没有转场、调色、clip opacity、文字叠加和效果；源码还明确把“导出不支持”的 effects 隐藏。Program Monitor 中看到的 dissolve/调色并不等于最终导出会保留；
- 应用快照仍绑定 LTX-2.3，而当前官方 LTX-2 仓库已经推荐 LTX-2.5。桌面壳与模型版本必须解耦，不能因为内置模型方便就锁死媒体路线。

### License 边界

应用代码是 Apache-2.0，可以 fork 和改造；模型权重不是 Apache。LTX-2.3 Community License 对特定规模商业实体和直接竞争产品有额外条款。当前明确只复用应用代码、编辑器和媒体层，不集成、分发或评估 LTX 模型权重；未来若重新开放视频模型扩展，再单独确认 License、硬件与质量。

### 复用结论

LTX Desktop 会实质改变原先的底座判断：**如果首版要做单用户桌面应用并包含较深的人工时间线编辑，它比 LumenX 更值得成为 Jellyfish 的同级底座候选，甚至可能是首选。**原因不是内置 LTX 模型，而是已经完成了最难从零补齐的桌面媒体壳、生成/选 Take/剪辑交互和真实导出基础。

但不建议原样采用。更合理的改法是保留 Electron、编辑器、媒体文件、Take/Retake/Gap Fill 与导出框架；用 SQLite/数据库替换 `localStorage` 项目真相源，重建短剧领域模型、持久化任务 Worker、供应商中立 Adapter 和一致的 TimelineManifest 导出。Jellyfish 的项目/准备度/任务语义可被吸收到这套核心里，不必同时运行两套控制面。

## 9. 底座选择的候选方案

### A. LTX Desktop 桌面壳 + 自建短剧核心

- 优点：专业编辑交互、桌面媒体链路、本地模型管理和真实导出起点最强；
- 缺点：需要替换项目持久化、任务运行时和 LTX 专用 Provider contract，并补齐短剧语义资产；
- 适合：P008 选择“完整或较深时间线编辑”，A004 选择本地桌面优先。

### B. Jellyfish 控制面 + 移植必要的 LTX 编辑模块

- 优点：状态和产品边界最干净，适合长期演进；
- 缺点：需要移植或重做桌面编辑器、声音、字幕、时间线和合成，首版媒体工作量最大；
- 适合：A004 选择受信服务器/浏览器优先，且更看重多任务恢复与控制面。

### C. LumenX 快速改造

- 优点：UI、资产、候选、TTS 和合成已有，最快验证工作流；
- 缺点：与 LTX Desktop 相比缺少成熟桌面时间线，与 Jellyfish 相比任务语义较弱；仍需重构能力契约并重做输出主链；
- 适合：前两个 Spike 出现不可接受改造量时保留为备选，不再是默认二选一。

### D. 小型新控制面 + 定向吸收模块

- 优点：围绕供应商中立的制片工件和质量回路建模，没有兼容包袱；
- 缺点：项目/资产/任务/UI 都需重建，容易重复造轮子；
- 适合：现有底座改造成本高，且短名单媒体路线已经比较清楚时再考虑。

## 10. 当前建议

不让底座选择被内置 LTX、GPT Image 或 H3 Adapter 左右。P008 已确认首版需要短剧生产时间线，A004 已确认本地桌面优先，A001 已确认只做 LTX Desktop 单候选 Mock 垂直切片：

1. 把一个项目从 `localStorage` 迁到最小 SQLite/API 持久化域；
2. 接入通用 Mock 图像/视频/音频 Adapter 和可重启任务；
3. 走通 Take → 局部返工 → 短剧时间线 → 9:16 导出，并验证预览/导出一致性。

通过后直接收敛到 LTX Desktop 壳；只有发现无法合理隔离或替换的结构性阻碍时才回退 Jellyfish，不维护两个一次性原型。LumenX 降为备选；Wind Comic、Toonflow 和 ViMax 仍作为模块/规则来源。闭源平台不属于底座或执行器候选。

## 11. 关键源码证据路径

以下链接均固定到本轮审查 commit，方便后续复核；它们比项目 README 更接近本章判断依据。

### Toonflow

- [分镜表技巧](https://github.com/HBAI-Ltd/Toonflow-app/blob/bc61ec7a1b5df31293b286981a5f4ad4635464ee/data/skills/production_skills/storyboard_table_techniques.md)
- [分镜面板生成 Skill](https://github.com/HBAI-Ltd/Toonflow-app/blob/bc61ec7a1b5df31293b286981a5f4ad4635464ee/data/skills/production_execution_storyboard_panel.md)
- [Production Agent](https://github.com/HBAI-Ltd/Toonflow-app/blob/bc61ec7a1b5df31293b286981a5f4ad4635464ee/src/agents/productionAgent/index.ts)
- [视频工作台生成入口](https://github.com/HBAI-Ltd/Toonflow-app/blob/bc61ec7a1b5df31293b286981a5f4ad4635464ee/src/routes/production/workbench/generateVideo.ts)

### ComfyUI

- [图执行器](https://github.com/comfy-org/ComfyUI/blob/7fe8a6138504f90ff7be82f3babf416da32876b1/execution.py)
- [服务端队列与接口](https://github.com/comfy-org/ComfyUI/blob/7fe8a6138504f90ff7be82f3babf416da32876b1/server.py)
- [依赖图实现](https://github.com/comfy-org/ComfyUI/blob/7fe8a6138504f90ff7be82f3babf416da32876b1/comfy_execution/graph.py)

`PromptQueue` 的 queue 与 history 在上述 `execution.py` 中是进程内容器，因此不能直接作为项目级持久化真相源。

### ViMax

- [Script-to-video pipeline](https://github.com/HKUDS/ViMax/blob/05a48943878312d88fe5a016c12a9654940ecc43/pipelines/script2video_pipeline.py)
- [Idea-to-video pipeline](https://github.com/HKUDS/ViMax/blob/05a48943878312d88fe5a016c12a9654940ecc43/pipelines/idea2video_pipeline.py)
- [Camera interface](https://github.com/HKUDS/ViMax/blob/05a48943878312d88fe5a016c12a9654940ecc43/interfaces/camera.py)
- [视频合成工具](https://github.com/HKUDS/ViMax/blob/05a48943878312d88fe5a016c12a9654940ecc43/utils/video.py)

### Jellyfish

- [镜头准备状态](https://github.com/Forget-C/Jellyfish/blob/a9678194ddf2d9be3ccbe78d4287d87d5089e123/backend/app/services/studio/shot_preparation_state.py)
- [视频准备度](https://github.com/Forget-C/Jellyfish/blob/a9678194ddf2d9be3ccbe78d4287d87d5089e123/backend/app/services/studio/shot_video_readiness.py)
- [视频生成 contract](https://github.com/Forget-C/Jellyfish/blob/a9678194ddf2d9be3ccbe78d4287d87d5089e123/backend/app/core/contracts/video_generation.py)
- [任务模型](https://github.com/Forget-C/Jellyfish/blob/a9678194ddf2d9be3ccbe78d4287d87d5089e123/backend/app/models/task.py)
- [时间线占位路由](https://github.com/Forget-C/Jellyfish/blob/a9678194ddf2d9be3ccbe78d4287d87d5089e123/backend/app/api/v1/routes/studio/timeline.py)

### Wind Comic

- [FFmpeg 视频合成](https://github.com/ChrisChen667788/wind-comic/blob/c83e1cf5e9b88fa8ac62bb737c79985a95243b8d/services/video-composer.ts)
- [字幕服务](https://github.com/ChrisChen667788/wind-comic/blob/c83e1cf5e9b88fa8ac62bb737c79985a95243b8d/services/subtitle.service.ts)
- [连续性表](https://github.com/ChrisChen667788/wind-comic/blob/c83e1cf5e9b88fa8ac62bb737c79985a95243b8d/lib/continuity-sheet.ts)
- [Mock Providers](https://github.com/ChrisChen667788/wind-comic/blob/c83e1cf5e9b88fa8ac62bb737c79985a95243b8d/lib/mock-providers.ts)
- [GPT Image adapter](https://github.com/ChrisChen667788/wind-comic/blob/c83e1cf5e9b88fa8ac62bb737c79985a95243b8d/lib/image-providers/openai-gpt-image.ts)
- [MiniMax adapter](https://github.com/ChrisChen667788/wind-comic/blob/c83e1cf5e9b88fa8ac62bb737c79985a95243b8d/services/minimax.service.ts)

### LumenX

- [统一模型目录](https://github.com/alibaba/lumenx/blob/f2a02e23171447c939e7d8e1386b24d17049bbf1/src/utils/model_catalog.py)
- [GPT Image/MuleRouter adapter](https://github.com/alibaba/lumenx/blob/f2a02e23171447c939e7d8e1386b24d17049bbf1/src/models/mulerouter.py)
- [视频任务与 Provider ID 模型](https://github.com/alibaba/lumenx/blob/f2a02e23171447c939e7d8e1386b24d17049bbf1/src/apps/comic_gen/models.py)
- [对白音频 stale 检查](https://github.com/alibaba/lumenx/blob/f2a02e23171447c939e7d8e1386b24d17049bbf1/src/apps/comic_gen/audio.py)
- [主 pipeline 与真实 merge 路径](https://github.com/alibaba/lumenx/blob/f2a02e23171447c939e7d8e1386b24d17049bbf1/src/apps/comic_gen/pipeline.py)
- [仍写 dummy 文件的 ExportManager](https://github.com/alibaba/lumenx/blob/f2a02e23171447c939e7d8e1386b24d17049bbf1/src/apps/comic_gen/export.py)

### LTX Desktop

- [三层架构与运行要求](https://github.com/Lightricks/LTX-Desktop/blob/24a8440d7a424c283d7e6dd534cbf78549785090/README.md)
- [项目、资产、时间线与字幕 schema](https://github.com/Lightricks/LTX-Desktop/blob/24a8440d7a424c283d7e6dd534cbf78549785090/frontend/types/project-model.ts)
- [`localStorage` 项目持久化](https://github.com/Lightricks/LTX-Desktop/blob/24a8440d7a424c283d7e6dd534cbf78549785090/frontend/lib/project-storage.ts)
- [单客户端内存后端架构](https://github.com/Lightricks/LTX-Desktop/blob/24a8440d7a424c283d7e6dd534cbf78549785090/backend/architecture.md)
- [活动生成状态](https://github.com/Lightricks/LTX-Desktop/blob/24a8440d7a424c283d7e6dd534cbf78549785090/backend/state/app_state_types.py)
- [导航期间的生成恢复](https://github.com/Lightricks/LTX-Desktop/blob/24a8440d7a424c283d7e6dd534cbf78549785090/frontend/lib/generation-recovery.ts)
- [LTX 专用视频请求 contract](https://github.com/Lightricks/LTX-Desktop/blob/24a8440d7a424c283d7e6dd534cbf78549785090/backend/api_types.py)
- [时间线 Gap Fill 交互](https://github.com/Lightricks/LTX-Desktop/blob/24a8440d7a424c283d7e6dd534cbf78549785090/frontend/views/editor/VideoEditorTimelineEditingPanel.tsx)
- [相邻镜头 Gap Prompt 建议](https://github.com/Lightricks/LTX-Desktop/blob/24a8440d7a424c283d7e6dd534cbf78549785090/backend/handlers/suggest_gap_prompt_handler.py)
- [FFmpeg 导出入口](https://github.com/Lightricks/LTX-Desktop/blob/24a8440d7a424c283d7e6dd534cbf78549785090/electron/export/export-handler.ts)
- [导出视频滤镜图](https://github.com/Lightricks/LTX-Desktop/blob/24a8440d7a424c283d7e6dd534cbf78549785090/electron/export/video-filter.ts)
- [跨平台 CI](https://github.com/Lightricks/LTX-Desktop/blob/24a8440d7a424c283d7e6dd534cbf78549785090/.github/workflows/ci.yml)
- [应用 License 与模型权重提示](https://github.com/Lightricks/LTX-Desktop/blob/24a8440d7a424c283d7e6dd534cbf78549785090/NOTICES.md)
