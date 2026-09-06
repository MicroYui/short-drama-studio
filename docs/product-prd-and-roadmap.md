# AI Agent 视频制片桌面产品 PRD 与里程碑

> 状态：当前产品定义
> 更新日期：2026-09-05
> 适用范围：单用户、本地桌面软件

本文是当前产品形态、复用边界和实施顺序的权威说明。历史调研和阶段实验仍保留，但与本文冲突时，以本文为准。

## 1. 一句话产品定义

这是一个运行在用户电脑上的 AI Agent 视频制片工作台：用户可以只给一句话，也可以提供剧本、图片、视频或声音；Agent 负责完成剧本、分镜、核心资产、视频与音频生成和初步剪辑，用户随后可在语义节点画布和专业时间线上继续查看、拖动、修改、局部重做并导出成片。

产品最终组合三类成熟能力：

- 学习 MiniMax Design 的 Agent 制片方法、Workflow、语义节点画布和 ComfyUI 执行体验；
- 使用当前自建 Agent Harness 补齐 MiniMax Design 未开源的 Agent Team、Tools、资产编排和工件交接；
- 复用 LTX Desktop 已有且不依赖 LTX 模型的桌面应用、媒体管理、时间线、多轨剪辑、Take 切换与 FFmpeg 导出。

## 2. 目标用户和核心价值

首版面向愿意在本机完成 AI 视频创作的个人用户，重点是短剧、漫剧、广告、MV 和其他多镜头视频。用户不需要先理解模型、工作流或节点连接方式。

核心价值是：

1. 从自然语言目标到可播放成片形成同一个项目，而不是散落的提示词和媒体文件；
2. 角色、场景、道具、声音、分镜和镜头结果都是可复用、可替换的结构化工件；
3. Agent 可以自主完成全流程，也可以只在用户指定的节点停下确认；
4. 用户可以在生成后继续像普通剪辑软件一样修改，而不必重新开始整条流程；
5. 修改剧本、资产、镜头或时间线时，只重新处理实际受影响的部分。

## 3. 最终用户体验

### 3.1 三种自然入口

- **一句话开始**：Agent 自主确定内容方案并逐步形成完整项目；
- **带素材开始**：用户上传人物、场景、商品、音乐、视频或参考资料，Agent 识别其用途并继续制片；
- **从现有项目继续**：用户打开已有剧本、分镜、素材或时间线，要求局部修改、补镜头或重新剪辑。

三种入口进入相同的 ProjectDocument 和 AgentRun，不建立彼此割裂的产品模式。

### 3.2 从创意到成片

```text
用户目标 / 已有素材
  → 项目意图、时长和视听方向
  → 剧本
  → 分镜与镜头计划
  → 角色 / 场景 / 道具 / 声音等核心资产
  → 图片、视频和音频生成
  → Take 选择与镜头拼接
  → 字幕、对白、音乐、音效等多轨时间线
  → LTX 编辑器继续拖动、裁切、替换或请求 H3 局部重做
  → 导出完整视频
```

这是 Agent 根据项目状态动态组织的制片过程，不是要求所有项目机械走完的固定节点图。

### 3.3 用户参与原则

1. 用户明确提供的内容和选择直接采用，不重复确认；
2. 用户明确要求不交互，或明确交由 Agent 决定时，Agent 自主完成并在 Trace 中记录关键选择；
3. 用户明确要求确认剧本、分镜、资产或其他节点时，只在对应语义节点停下；
4. 其余情况由 Agent 判断。只有缺失信息会实质改变作品或执行路线，而且无法从现有工件推断时才询问用户；
5. 不使用正则、关键词路由、固定确认次数、创意重试次数或审批门实现上述逻辑。

### 3.4 时长与内容密度

目标时长不是分镜阶段再把现有文本机械压进固定秒数，而是剧本创作前就进入版本化 `DurationContract`。Agent 结合用户是否明确给出时长、是否要求保留既有剧本、是否存在 Master Audio，以及当前 Workflow 的叙事方法，判断本项目由目标时长、来源文本还是主音频主导，并说明可接受区间、来源保真程度、失配时的处理方式和相对节奏形状。

- 原创或可改写内容以目标时长控制故事规模、场次数量、事件密度和对白量；Writer 在写作中估算自然表演时间，Verifier 从头冷读成稿并给出结构化时长适配结论；
- 用户锁定的完整剧本以来源为主，分镜忠实计算自然时长，不为命中目标而删台词、并行动作或平均压缩；
- 音乐视频等由 Master Audio 主导的项目以音频时间轴为准；ProductionSpec 精确绑定当前 MasterAudio version，目标长度等于时间原点加所选音频窗口，重绑后旧分镜和生成计划失效；
- 可接受区间由 Agent 针对当前作品语义提出并可被用户覆盖，不使用全局固定百分比或代码阈值；
- 分镜的 timing ledger 记录实际对白、动作、停顿和反应，语速是 Agent 对当前台词表演的语义判断，不在共享 schema 设置单一上限。若目标主导项目的结论为 `OUTSIDE_ACCEPTED_RANGE` 或 `UNRESOLVED`，Agent 交回 Leader：可回到现有剧本局部/整体修订链路，也可依据完整证据明确接受取舍；修订时携带精确 Script、ProductionSpec 和 StoryboardDraft 版本证据。来源或音频主导不被偷偷压缩。

MiniMax-H3 的 4–15 秒只约束单次 `ClipGroup / GenerationUnit`，不等于场次、镜头或整集的创作时长。一个场次可以包含多个连续 ClipGroup，一个 ClipGroup 也可以包含多个编辑 Shot。

## 4. 最终产品界面

最终工作区可以采用 MiniMax Design 类似的组织方式：

- 左侧：项目、素材库、Skills、工作流和可复用核心资产；
- 中间：剧本、分镜、角色、场景、图片、视频、音频和成片组成的语义节点画布；
- 右侧：Main Agent 对话、当前任务、Trace 和需要用户决定的内容；
- 下方或独立工作区：LTX 多轨时间线、预览、裁切、拆分、拖动、换 Take、字幕、音频和导出；
- 高级入口：从某个媒体节点展开对应的 ComfyUI 图，编辑模型、参数和局部处理链。

语义画布负责表达“这部作品由哪些工件组成以及它们如何派生”；ComfyUI 负责“某个媒体任务如何执行”；LTX 时间线负责“最终视频如何剪辑和交付”。三者不是同一张图。

## 5. 产品架构

```text
MiniMax Design 式 Agent 对话与语义画布
  ↕
ProjectDocument + versioned Artifacts（唯一内容真相）
  ↕
AgentRun / Production Manager / PlanningRun / WorkOrder / Tools
  ↕
图片、H3、音频与可选 ComfyUI WorkflowArtifact
  ↕
Take / TimelineManifest / EditorialTimeline
  ↕
LTX 时间线、多轨编辑、定向修改与 FFmpeg 导出
```

### 5.1 Agent 制片层

负责理解用户目标、选择 Workflow/Skill、形成剧本和分镜、决定核心资产的重量、拆解任务、调用 Tools、组织媒体生成、持续更新项目并响应自然语言修改。

当前承载该层的核心是：

- `AgentRun + ProductionManager`：整个用户目标的主调度；
- `PlanningRun`：由 AgentRun 拥有的顺序内容规划游标；
- `PlanRevision + WorkOrder + AgentResult`：任务分解、委派和工件交接；
- `ContextManifest + ArtifactRef`：为 Worker 提供最小且精确的上下文；
- `ProductionSpec + Storyboard + AssetPlan + GenerationPlan`：使用版本化 ProductionDocument 承载制作决策、完整分镜、核心资产计划和 Provider 无关的生成单元；
- `DurationContract + DurationFitAssessment`：在故事、剧本审阅、制作规格和分镜账本之间传递同一时长权威与自然表演判断；
- `ProjectOutputSlot`：用稳定逻辑名称交接当前产物，换绑时保留历史 ArtifactRef；
- `ProductionBoardView`：从权威状态派生的用户可见进度和工件视图；
- Typed Tool Registry：资产、图片、视频、音频、时间线和修改能力。

### 5.2 媒体执行层

Provider 和 ComfyUI 都是 Agent 可调用的执行器，不拥有项目总状态。

- 当前图片能力可生成或编辑角色、场景、道具、关键帧及标准参考板；
- MiniMax-H3 负责当前真实视频生成路线；
- 音频能力负责对白、音乐、音效、分析和时间对齐；
- ComfyUI 后续作为版本化 `WorkflowArtifact` 接入，用于本地模型、图像/视频工作流、超分、插帧、修帧、抠像和用户自定义节点图；
- 每次执行结果都登记为 ProjectDocument 可解析的资产版本或 Take。

Storyboard 中的 Shot 是叙事和编辑边界，ClipGroup / GenerationUnit 是 Provider 执行边界。Agent 可以把连续动作的多个 Shot 组合成一次 H3 生成，但 Task、Take 和时间线仍保留每个 Shot 在生成媒体中的 source window，以支持后续裁切和局部修改。

### 5.3 LTX 编辑交付层

LTX Desktop 是已经存在的桌面与编辑底座，最终负责：

- 视频、图片和音频素材预览；
- 多轨时间线、拖动、裁切、拆分、复制、删除、层级和音量；
- Take 切换、捕获帧和局部编辑；需要新媒体时向 Agent 发出 H3 镜头重生成请求；
- 字幕、画面和多源音频混合；
- FFmpeg 导出。

短剧 Agent 的 `TimelineManifest` 与 LTX 编辑器项目模型需要建立双向适配：Agent 可创建初始时间线，用户编辑后也要把有效修改同步回项目工件，后续 Agent 才能在最新版本上继续工作。

## 6. 唯一真相与三个视图

`ProjectDocument + versioned Artifacts` 始终是内容真相。未来的 `EditorialTimeline` 也是其中一个版本化项目工件，而不是独立数据库。其他界面只保存各自必要的表现信息：

| 视图 | 表达内容 | 不负责的内容 |
| --- | --- | --- |
| 语义节点画布 | 工件、派生关系、状态、空间布局 | 不充当 Agent 或 Provider 状态机 |
| ComfyUI 图 | 一个媒体工作流的节点、参数、连接和执行结果 | 不管理完整剧本、项目和最终时间线 |
| LTX 时间线 | 最终剪辑、多轨和交付状态 | 不替代剧本、资产和 AgentRun |

这样可以同时学习 MiniMax Design 的节点体验和 LTX 的专业剪辑体验，又不会产生三套互相冲突的项目状态。

## 7. 复用、迁移与自研边界

截至 2026-09-04，公开源码能够确认的边界如下。

| 来源 | 当前事实 | 采用方式 |
| --- | --- | --- |
| [LTX Desktop](https://github.com/Lightricks/LTX-Desktop) | 完整桌面应用、生成与编辑代码公开，Apache-2.0；当前仓库仍保留这些代码，但产品入口只挂载 ShortDramaStudio | 仅复用应用壳、媒体、编辑交互与导出；不启用 LTX 模型 Provider，重点衔接 ProjectDocument、Agent 产物与编辑器状态 |
| [MiniMax Design ComfyUI Plugin](https://github.com/MiniMax-AI/minimax-desgin-plugin) | 公开 `frontend/` 与 `backend/`，基于 ComfyUI，GPL-3.0 | PoC 后按独立本地组件移植，尽量保持上游目录与适配层分离 |
| MiniMax Design 外层语义画布 | 未发现官方公开源码 | 学习交互和数据组织，在现有 ProductionBoard/Artifacts 上实现等价产品，不复制安装包私有前端代码 |
| MiniMax Design Main Agent / Team Engine / Hub Gateway | 未公开 | 由当前 AgentRun、ProductionManager、WorkOrder、TeamExecutor 和 Tool Registry 实现 |
| MiniMax Design 3.0.11 Workflow Markdown | 本机包中可读取；80 个 active workflow 文件已按版本复制到仓库 | 作为成熟制片方法直接装载并做薄适配；正式对外分发前确认其再分发条款 |
| [MiniMax-H3](https://github.com/MiniMax-AI/MiniMax-H3) Skills 与模型代码 | 官方仓库公开 | 已接入 Prompt Skill 和 8 个任务 Method；继续保留上游原文，适配本地工件和 Tools |
| [Mini-Agent](https://github.com/MiniMax-AI/Mini-Agent) | MIT 开源的通用单 Agent 示例，不是 Design Agent Team | 可选择性吸收 Tool loop、上下文摘要和会话记录方法；不替换当前更完整的 AgentRun |
| MiniMax 核心资产 Tools | Workflow 描述了方法，但未公开 Design Tool 实现 | 复用当前结构化资产与图片 Tools，根据 Workflow 补齐缺失合同 |

因此，“直接抄 MiniMax Design”应准确拆成：

1. 开源 ComfyUI 插件可以直接迁移；
2. 已获得的 Workflow Markdown 可以直接作为 Agent 方法使用；
3. 外层语义画布只能学习产品行为后实现，因为目前没有找到对应开源源码；
4. Agent Team、核心资产编排和 Hub Tools 必须由本项目实现，但应尽量照着成熟 Workflow 的工件与调用语义做，不重新发明另一套制片方法；
5. LTX 中不依赖其模型的编辑能力直接复用，本项目主要编写数据适配、Agent 编辑入口和修改后的反向同步。

“直接复用”不等于当前已经连通。LTX 编辑器目前仍以自己的前端 Project 模型和 `localStorage` 保存项目；Short Drama 使用 SQLite、ProjectDocument 和较简单的顺序 TimelineManifest。两者之间尚无真实转换和回写层。

### 7.1 上游代码的接入方式

- 成熟开源代码优先以独立上游目录或组件保留，记录版本和 License；
- 对上游的修改尽量放在桥接层，不在复制进来后立即大规模重写；
- MiniMax Workflow 原文和本地角色适配器分开，便于以后更新上游内容；
- 不从 MiniMax Design 安装包复制没有公开授权的闭源 JavaScript 来代替产品开发；
- 普通工程测试用于确认接口、保存、恢复、播放和导出正常，不建设额外审批、对抗或盲审体系。

### 7.2 当前复制量与剩余范围

| 内容 | 当前仓库 | 说明 |
| --- | --- | --- |
| MiniMax Design active Workflows | 80 个文件，676,646 bytes | 与本机 3.0.11 包一致；包含 3 个活动项目 Workflow 与 `_shared`，未复制 65 个 `_disabled` 文件 |
| MiniMax-H3 任务 Skills | 8 个 Skill、31 个文件，360,229 bytes | 已映射为 8 个任务 Method |
| 当前活动 Method adapter | 11 个 | 3 个 Design Project Workflow + 8 个 H3 Task Method |
| MiniMax Design ComfyUI Plugin | 尚未复制 | 官方仓库约 4,440 个 tracked files，包含前端、Python 后端、蓝图和 Hub 嵌入桥；安排在几分钟成片 PoC 后 |
| Mini-Agent | 尚未复制 | 只在发现当前 Runtime 的具体缺口时选择性吸收，不整套替换 |
| Design 外层画布、Team Engine、Hub Gateway 和通用 Tools | 无可复制公开源码 | 只能根据公开行为、Workflow 与当前可观察合同自行实现 |

PoC 后可直接引入的 ComfyUI 部分包括图编辑器、ComfyUI Python 后端、公开蓝图、Workflow Draft、可执行快照、运行登记和结果回写结构。其中指向 Design 私有网关的 H3 API 节点不能原样工作，需要改为当前 H3 Provider、ArtifactRef 和 ProjectDocument 桥。

本机 Design 编译包可以观察到大量 Tool 合同和 Clip Studio/3D Director 方法，但没有对应公开源代码和开放许可；它们只作为 Tool 语义参考，不作为可直接复制的实现。H3 的 `Context-IR` 同样未包含在 H3 开源发布中，因此多阶段理解和资产编排仍由本项目 Agent 层承担。

## 8. 当前已经迁移和实现的内容

### 8.1 已直接吸收的 MiniMax 内容

- MiniMax Design 3.0.11 的 80 个 active Workflow 文件，包括 `drama-series`、`ad-tvc`、`mv` 与 `_shared`；
- 三个 Project Workflow adapter，允许 Agent 按项目语义装载对应原文章节；
- MiniMax-H3 Prompt 方法及 8 个任务型 Skills；其中任务 Skills 已保存 31 个上游文件并映射为 8 个 Method；
- 角色卡、场景卡、镜头表、分镜、`clip_group`、Master Audio 和后期交接等工件方法；
- 用户明确确认才停靠、否则可由 Agent 自主推进的交互语义。

### 8.2 已由本项目实现的闭源替代能力

- AgentRun、动态 PlanRevision 和 PlanningRun 内容规划游标；
- Leader/Worker/Verifier 责任以及单机 WorkOrder 执行；
- Tool Catalog、按需 Tool Schema、ContextManifest 和 Artifact handoff；
- 结构化角色、场景、道具资产及多视图参考板；
- 图片、视频、音频、H3 路由、Take、时间线和导出 Tools；
- ProductionBoardView、可恢复任务和自然语言局部修改影响计划；
- ProjectDocument 和版本化 Artifacts 作为统一项目状态。

### 8.3 尚未完成

- 在新的 Agent Runtime 上从一句话独立完成剧本、分镜、核心资产、真实视频、音频和几分钟完整成片的一次连续 PoC；
- MiniMax Design ComfyUI 开源前后端的实际移植与 WorkflowArtifact 桥接；
- MiniMax Design 式语义节点画布；
- 恢复 LTX Home、GenSpace 和 VideoEditor 的产品入口；当前 `frontend/App.tsx` 只显示 ShortDramaStudio；
- 在 ProjectDocument 内建立版本化 `EditorialTimeline`，覆盖真实多轨、trim、speed、transition、text、opacity、color 和 Take 选择；
- `ProjectDocument / EditorialTimeline` 与 LTX 编辑器项目模型的完整双向同步，并将持久化真相从前端 `localStorage` 收回后端；
- 将 LTX 编辑器的 trim、split、move、Take 切换和捕获帧等非模型操作包装成 Agent 可调用的编辑 Tools；需要新媒体的 Retake/Extend 意图由 H3 路线重新生成；
- 让最终导出消费 EditorialTimeline，并逐步补齐编辑器预览与 FFmpeg 导出在转场、多层画面、透明度、颜色和文本上的差异；
- 用户安装、模型配置和一体化桌面交付体验。

## 9. Agent 能力范围

Agent 应能够自主选择完成整个项目或只完成其中一部分：

- 从 Brief 形成故事意图、剧本和分镜；
- 判断哪些人物、场景和道具需要重型核心资产，哪些群像或一次性元素可以轻量处理；
- 生成或接收用户上传的资产，制作多视图、场景总览、局部视图和参考板；
- 为镜头选择 Direct、首帧、尾帧、首尾帧或 Reference 等生成路线；
- 生成视频、对白、音乐、音效和字幕并形成多轨时间线；
- 根据用户自然语言修改剧本、资产、某个镜头、声音或剪辑；
- 将修改定位到最小受影响分支，再调用相应 Tool；
- 将项目送入 LTX 编辑器，让用户手动继续；
- 在用户要求时展开某个 ComfyUI 工作流或高级参数。

Skill 描述成熟制作方法，Tool 提供可执行能力，Agent 根据当前项目决定何时组合二者。Skill 不等于 Tool 白名单，也不等于固定状态机。

## 10. 产品里程碑

这些里程碑描述产品逐步形成的可见结果，不是审批门，也不要求额外盲审或对抗评测。

| 里程碑 | 当前状态 | 主要结果 |
| --- | --- | --- |
| 产品形态与复用边界 | 本轮完成 | PRD、AGENTS 北极星、MiniMax/LTX/自研边界统一 |
| Agent 内容制片 | 版本化后端合同已补齐，待 Agent 完整 PoC | 一句话或已有素材形成完整剧本、ProductionSpec、Storyboard/ClipGroup、GenerationPlan 和可修改工件 |
| 分镜到生成单元 | 本轮后端主链已实现 | ClipGroup 编译为 GenerationUnit，一个 H3 task/take 可覆盖多个 Shot，并按 source window 进入时间线 |
| 核心资产与媒体准备 | 版本化 AssetPlan 与媒体合同已补齐，待 Agent 完整 PoC | Agent 自主决定 anchor / text-only / reuse，生成或接收角色、场景、道具参考，并精确绑定到镜头 |
| 几分钟完整视频 PoC | 下一项主工作 | Agent 独立完成剧本、分镜、资产、视频、音频、时间线和可播放导出 |
| ComfyUI 本地执行层 | PoC 后 | 移植开源插件，Agent 可选择、绑定、编辑和运行 WorkflowArtifact，结果回写项目 |
| 语义节点画布 | ComfyUI 桥接后 | 用户查看剧本、资产、镜头、任务和派生关系，并可从任一节点继续对话或局部重做 |
| LTX 编辑器完整衔接 | 编辑与生成代码保留，但当前未挂载 | 建立 EditorialTimeline 和后端持久化桥；Agent 产物进入时间线，用户拖动、裁切、多轨编辑和换 Take，媒体重做请求回到 H3 Agent 路线，修改同步回项目 |
| 一体化本地桌面产品 | 最终目标 | Agent、语义画布、ComfyUI 和 LTX 编辑体验统一，可安装并完成完整视频项目 |

## 11. 下一步实施顺序

1. 用 Fake Provider 和本地媒体回归验证 ProductionSpec → Storyboard → AssetPlan → GenerationPlan → H3 Route/Task/Take → Timeline 的后端闭环；
2. 让 Agent 在不人工代调 Tool 的情况下独立产生和修改核心资产计划与媒体，并观察真实 Trace；
3. 用户明确授权后，运行一次几分钟完整视频 PoC；遇到代码 Bug 就修复，效果问题记录到对应 Workflow、Artifact 或 Provider 输入层；
4. PoC 跑通后，引入 MiniMax Design ComfyUI Plugin 开源代码，先完成独立进程、WorkflowArtifact、输入绑定、运行和结果回写；
5. 再实现语义节点画布，直接消费现有 ProjectDocument、ProductionBoardView 和 Artifact 派生关系；
6. 建立版本化 EditorialTimeline 与 LTX 编辑器状态的双向桥接，恢复完整剪辑体验，并让需要重生成的局部修改返回 H3 Agent 路线，最终导出逐步与编辑预览一致；
7. 最后处理安装、模型管理、项目迁移和整体界面一致性。

## 12. 当前不做

- 多用户、多租户、团队权限、账号和协作发布；
- 预算上限或基于预算的强制路线；
- 固定关键词路由、正则分发或固定次数工作流；
- 新的质量门、审批门、对抗评测、盲审系统或为了流程完整而设置的 Critic 循环；
- 在几分钟成片 PoC 前重写大规模前端；
- 让语义画布、ComfyUI 或 LTX 时间线成为第二份项目真相。

## 13. 已确认与仍待处理的产品决定

### 13.1 已确认：不启用 LTX 本地模型 Provider

视频生成主链使用 H3，不集成、不分发、不评估 LTX 模型权重。LTX Desktop 只作为应用和编辑代码底座；其模型驱动的 generation、Retake、Extend 和 IC-LoRA 后端保持禁用。

编辑器仍可保留“局部重做”这一用户意图，但它应交给 Agent 编译为 H3 镜头重生成，或者只执行裁切、替换、切换 Take 等不需要模型的操作。

### 13.2 MiniMax Design ComfyUI Plugin 的发布方式

该开源插件采用 GPL-3.0，而 LTX Desktop 是 Apache-2.0。研发阶段可以直接验证和集成，但正式分发前需要决定组合方式并履行相应的源码、License 和 Notices 要求。

建议：把它作为独立本地进程和独立前端组件接入，通过本地 HTTP/WebSocket 与主应用通信，尽量保持上游代码、修改和 License 边界清楚。

## 14. 相关详细资料

- [MiniMax Design、Agent Team 与 H3 Skills 蓝图](43-minimax-design-agent-team-product-blueprint.md)
- [Agent / Skill / Tool 运行时迁移](40-agent-skill-tool-runtime-migration.md)
- [Agent Tool / Artifact 就绪度核对](44-agent-tool-artifact-readiness-audit.md)
- [从完整分镜到 H3 生成单元的后端闭环](45-storyboard-generation-unit-backend-closure.md)
- [当前项目总览](project-overview.md)
- [MiniMax Design Plugin for ComfyUI](https://github.com/MiniMax-AI/minimax-desgin-plugin)
- [MiniMax-H3](https://github.com/MiniMax-AI/MiniMax-H3)
- [LTX Desktop](https://github.com/Lightricks/LTX-Desktop)
