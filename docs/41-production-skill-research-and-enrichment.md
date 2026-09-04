# 制片 Skill 研究与增补边界

> 复核日期：2026-09-03。本文把 MiniMax H3、Dreamina/Seedance、LTX Studio/LTX-2、Toonflow、ViMax 和 ComfyUI 的公开资料映射到本项目当前的 Agent / Skill / Tool 架构。它不是新的阶段 workflow，也不把闭源产品宣传写成已实现能力。

## 结论

在本轮核对的公开实现与产品资料中，没有一个系统同时完整提供“项目级业务真相、动态 Agent 规划、可组合 Tool、格式 Skill、多候选版本、局部修复、专业时间线与可恢复外部任务”。本项目应组合吸收不同系统的长处：

- 从 Dreamina 与 LTX Studio 学习项目、资产、分镜、候选和时间线保持连接的产品对象；
- 从 Toonflow 学习决策、执行和审校职责分开，但不复制固定六阶段和强制确认；
- 从 ViMax 学习规划工件与渲染分离、部分工件可恢复和依赖关系显式化；
- 从 ComfyUI 学习只执行目标输出依赖的失效分支，但不把短剧业务改造成节点图；
- 从 MiniMax H3 与 Seedance 学习 Provider 的多模态输入、原生音频和模型级多镜头如何表达，但所有易变参数仍由 Provider capability、Tool Schema 和 Prompt Compiler 管理。

最终分层保持不变：

```text
用户目标 + ProjectDocument
  → Production Manager 动态计划
  → StorySourceContract + StoryIntent + EndingContract
  → 共享中文角色 + 宽粒度 Format Skill + Agent 选中的 Narrative Skills
  → Provider Skill 翻译模型语法
  → Tool 执行类型化能力
  → AssetVersion / Shot / Take / Timeline 等项目工件
  → 检查结果驱动局部返工或重新规划
```

其中 AgentRun 是一次制片目标的总调度，`ProjectDocument` 及其版本化领域工件是唯一项目内容真相。长文本首次规划是 AgentRun 拥有的 **Content Planning Job**；当前运行时代码与持久化合同仍使用兼容名称 `PlanningRun`，它只提供大纲、逐场写作、审校和导演规划的持久化游标与恢复点，不形成第二套项目内容或端到端编排。场景总数与跨场修订范围不再使用 40/12 这类低常数创作上限，长任务以场次和修订单元持续保存进度。当前不抽象通用 `ChildJob`，待多个同级领域作业出现可验证的共同生命周期后再提取最小合同。

`ProductionProfile` 只区分 AI 短剧、通用视频、数字人、动漫这类宽粒度媒介/生产方法。Story Orientation Agent 先从完整用户输入形成 `StorySourceContract` 和 `StoryIntent`，再按项目语义选择零个或多个 Narrative Skills；Loader 不做关键词/正则路由、题材枚举或必选数量。`SCRIPT_LOCKED` 普通文本中的锁定台词也由 Story Orientation Agent 语义结构化到 `StorySourceContract.locked_dialogue`，或由调用方直接提交结构化条目；代码只做逐字与顺序保护，不猜 speaker/旁白。`EndingContract` 保护本集兑现与续集策略，`ProjectBible.style` 保存视觉/摄影/声音/材质表达，不代替叙事意图。

专业委派同样保持语义选择边界：`assigned_agent` 只是接收者身份，Production Manager 基于完整目标显式给出 `production_skill_role`，运行时只按这个字段加载中文职责 Prompt，不从 Agent 名称或用户文本关键词推断角色。

专业 Agent 需要 Tool 时使用 `WorkOrder.specialist_turns` 单步持久化。一次外部执行只完成模型决策或一个已记录的 Tool 调用；请求和结果分步保存，下一次再让 Agent 基于历史继续。重启时未落盘结果的 `IN_PROGRESS` turn 变为 `INTERRUPTED`，由 Agent 检查规范状态后决定对账或重试，不自动重放。这是恢复边界，不是固定调用次数。

Critic 的可迁移经验不是“多审几轮就更好”，而是以目标观众视角阅读完整文本，用稳定 issue ID 追踪同一语义问题的证据、修复和已接受取舍。Production Manager 执行 Critic/Verifier 的语义结论，只在存在需要脱离大纲元数据重新通读的具体疑点时选择最终冷读；它不是必经门禁。系统不叠加严重度、问题数、关键词、固定轮数或总超时门槛。需要修订时以受影响场次为单位持久化，不用一次整稿重写覆盖进度。

## 证据口径

| 标签 | 本文中的含义 |
| --- | --- |
| `OFFICIAL-DOC` | 厂商官方产品、模型或 API 文档明确说明；可确认公开接口或产品行为，不能据此推断闭源内部实现 |
| `SOURCE-CONFIRMED` | 在固定 commit 的公开源码中确认 |
| `PAPER-REPORTED` | 论文作者报告的方法或结果，未在本项目复现 |
| `USER-PROVIDED-EXAMPLE` | 用户提供的 Prompt、样片或工作稿；可用于归纳方法，不等于厂商官方合同或通用最佳实践 |
| `CURRENT` | 当前仓库已经存在对应领域模型、Tool 或运行时路径 |
| `UNKNOWN` | 公开资料不足，或仍需在本项目 Provider 与样片上验证 |

README 中的愿景、厂商展示样片和营销质量词不能单独作为生产事实。闭源产品只能用于确认外部可见工作方式；开源结论尽量落到固定源码文件。

## 总体研究矩阵

| 系统 | 可确认的核心组织方式 | 本项目采用 | 本项目不采用 |
| --- | --- | --- | --- |
| MiniMax H3 | Context-IR、768P Base、2K Regenerate；按文本、首帧、首尾帧、尾帧和多模态参考区分 Prompt；异步任务提交与查询 | Provider-neutral Shot/ReferencePlan 与 H3 Prompt Compiler 分层；保存最终 Prompt、媒体职责和任务状态 | 把时长、文件数、分辨率或模式组合写进格式 Skill；把旧 Hailuo 接口自动等同 H3 |
| Dreamina / Seedance | 同一项目连接对话、素材、脚本、分镜、逐场景生成、局部调整和时间线；Seedance 提供模型级多镜头与音画能力 | 项目级工件链、分镜控制层、连续性锚点、按镜头风险选择输入、局部返工 | 把闭源产品的质量承诺当源码事实；把 native multi-shot 当成完整整集工作流 |
| LTX Studio / LTX-2 | Project 连接 Gen Space、Storyboard、Timeline；源生成与时间线引用分离；Retake 可只替换时间区间或单一模态 | AssetVersion/Take 与时间线引用分离；保留 alternate Take；局部 Retake/Extend/Gap Fill | 从产品页面猜测 Studio 内部路由；复制 LTX 模型专属 temporal mask 到 H3 |
| Toonflow | 决策 Agent 调度执行/监制 Agent；脚本、资产、分镜与视频候选分表保存 | 有边界的 WorkOrder、专业角色委派、候选与选择分离 | 固定六阶段、每阶段强制停顿、XML 作为业务状态、Skill 作为 Tool allowlist |
| ViMax | 文本规划工件与渲染分开；角色、分镜、shot description、camera tree、关键帧和 clip 分层；保留部分产物 | 先计划后渲染、分层工件、失败保留部分结果、显式下游影响 | 文件存在即视为有效、原位覆盖版本、所有角色固定三视图、简单拼接替代时间线 |
| ComfyUI | Tool-like 节点组成 DAG；按输入签名缓存；只运行目标输出依赖且已失效的分支 | 语义影响预览、只失效受影响工件、复用未改变的资产和 Take | 把节点图作为用户业务模型、Agent 策略或项目真相；为迁移原则新增 hash 审批层 |

## MiniMax H3：Provider Skill 与项目 Skill 必须分层

### 可验证结论

MiniMax 官方把 H3 描述为可理解文本、图像、视频和音频上下文并生成原生立体声音视频的多模态系统。官方仓库将其拆成 H3-Context-IR、H3-Base 和 H3-Regenerate-2K，并公开 FL2VA 与 Ref2VA 基础模型；Context-IR 与 2K regeneration 仍通过托管 API 提供。`OFFICIAL-DOC`

官方 Prompt Skill 明确区分 T2VA、I2VA、FL2VA、L2VA 与 Ref2VA，并分别约束时间线描述、首尾状态连接、引用标签、对白、画外音、声音环境和参考媒体职责。V2 API 是异步任务接口，具体支持的时长、分辨率、文件数量与输入组合可能随版本变化。`OFFICIAL-DOC`

截至复核日期，官方发布页、仓库和 API 文档足以确认公开能力与 Prompt 合同，但没有发现可引用的完整 H3 Technical Report PDF 或 arXiv 版本；不能把发布博客补写成论文级实现细节。`UNKNOWN`

直接来源：

- [MiniMax H3 发布说明](https://www.minimax.io/blog/minimax-h3)
- [MiniMax H3 官方仓库](https://github.com/MiniMax-AI/MiniMax-H3)
- [固定快照中的 H3 Prompt Skill](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/SKILL.md)
- [H3 模式与 Skill 目录](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/README.md)
- [V2 视频生成任务](https://platform.minimax.io/docs/api-reference/video-generation-v2-create)
- [H3 Context-IR 任务](https://platform.minimax.io/docs/api-reference/video-generation-v2-h3-context-ir)
- [任务查询](https://platform.minimax.io/docs/api-reference/video-generation-v2-query)
- [2K regeneration](https://platform.minimax.io/docs/api-reference/video-generation-v2-regeneration)

### 采用与不采用

采用的是“格式无关镜头事实 → H3 专属语法翻译”的边界：剧本、人物、动作顺序、首尾状态、对白和媒体职责先存在于项目工件，H3 Prompt Compiler 只做最后一层模型表达。任务提交、轮询、对账和下载仍属于 Tool/Provider 生命周期。

不把官方示例中的 choice card、固定重试次数或某一版本参数复制到 Production Skill。官方页面之间若出现模式枚举或组合说明不一致，以当前 Tool Schema、Capability Profile 和真实 Provider 预检为准，并把未确认项标为 `UNKNOWN`。

### 用户提供的 15 秒角色预告 Prompt：取舍记录

用户提供的 `MiniMax H3.pdf` 是一份“15 秒、精确 13 切、AAA 动作游戏角色亮相片”的具体 Prompt，不是 H3 官方文档。它对当前项目的价值是展示了如何把一个明确的成片概念写成参数、主体约束、时间分段、剪辑和声音设计。`USER-PROVIDED-EXAMPLE`

| 样例中的做法 | 本项目的取舍 |
| --- | --- |
| 先要求设计好角色，并让参考图决定身份、比例、服装、材质和色彩 | 采用“先建立结构化核心资产、再生成运动镜头”；但以当前 ReferencePlan 中多份已批准 AssetVersion 的明确职责为准，不固定只能有一张“唯一权威图” |
| 把可替换文字和成片指令分开 | 采用结构化参数、锁定事实与创作内容分层；但不要求所有项目拥有该样例的英文字卡参数 |
| 按 `0.00–1.10s`、`1.10–2.20s` 等时间段写每个镜头/节拍 | 采用“完整剧本 → 时间线分段 → 逐段定镜”，每段聚焦镜头、动作、台词/声音、情绪/表演和媒体绑定 |
| 行动、字卡、英雄时刻与终幕身份卡交替 | 采用“每段有可观察作用、画面/文字/声音可交替主导”；但字卡和身份卡只在作品风格需要时使用 |
| 硬切、图形匹配、甩镜、定格、前景擦过和速度渐变与音乐击打点同步 | 采用转场、动作和声音对齐可见变化；拒绝把这组特效当作通用必填清单 |
| 固定 15 秒、13 切、AAA 游戏预告语气、黑白/冰蓝/霓虹黄/暖金配色，并禁止慢溶解与长连续动作 | 全部保留为该角色预告片的风格选择，不沉淀为 AI 视频、H3 或 AI 短剧的硬规则 |

因此，该样例带来的通用范式是“核心资产锁定 + 时间轴结构 + 每段单一可读作用 + 真实媒体绑定 + 声画共同组织节奏”，而不是其中的任意固定数字或美术语汇。

这些范式的落点必须在故事之后分层：资产 Agent 决定核心角色是用四视图还是群像轻量图，并决定场景是否需要俯视布局、全景 master 与功能区 coverage；导演/分镜 Agent 把完整剧本按时间线拆成镜头、动作、台词/声音与情绪/表演；Provider 层再将语义 `@` alias 解析为请求真实携带的媒体。共享 Story Architect 只负责故事承诺、人物选择、因果推进与结尾意图，不注入 PDF/H3 的下游表达规则。

当前 `ShotPlan.timeline_beats` 可选地保存一个连续 editorial shot 内部的时间阶段；非空时必须从 0 连续覆盖整镜时长，并按原顺序恰好分配该镜头对白。每个 beat 可结构化保存画面、景别、摄影机、动作、表演、情绪、声音与 `ShotReferenceCue`；简单镜头和旧文档可留空并沿用镜头级概要。Beats 不是多镜容器，任何显式切镜、独立生成或独立剪辑边界仍映射为相邻 ShotPlan。`CURRENT`

`ShotReferenceCue` 用 `subject_asset_id`、不带 `@` 的 alias、`required_tags` 和 purpose 表达某个时间段的语义引用；H3 Route 将解析后的语义写入 `H3MediaInput.reference_alias` / `binding_purpose`。alias 只服务于 `@资产名` 这类人和 Agent 可读映射，不是文件路径、AssetVersion 或上传编号。真实 H3 绑定仍由选定的 AssetVersion/`source`、媒体输入顺序和顺序生成的 `<Picture N>`、`<Video N>`、`<Audio N>` Provider label 共同确定；Prompt 只能引用请求实际携带的媒体。旧输入可不提供 alias，由解析器生成稳定后备名称。`CURRENT`

### 当前落点

- H3 Provider Skill：[skills/providers/minimax_h3/SKILL.md](../backend/short_drama/skills/providers/minimax_h3/SKILL.md)
- H3 Prompt 翻译：[providers/h3_prompt_skill.py](../backend/short_drama/providers/h3_prompt_skill.py)
- GPT 辅助编译入口：[providers/h3_language_prompt.py](../backend/short_drama/providers/h3_language_prompt.py)
- 路线与能力证据：[h3_route_planner.py](../backend/short_drama/h3_route_planner.py)
- Agent 可组合入口：[tools/route](../backend/short_drama/tools/route)
- 通用镜头与引用事实：[models.py](../backend/short_drama/models.py)

## Dreamina 与 Seedance：连接工件，不绑定闭源平台

### 可验证结论

Dreamina 官方页面展示了一个项目内的连续创作面：对话 Agent 和画布读取上传素材，生成或管理角色、环境与风格参考，把脚本拆成分镜和逐场景视频，再送入时间线调整与导出。Script-to-storyboard 支持逐帧审阅与局部修改；Multi-shot 页面把 shot list、continuity anchor、camera、transition、duration、ratio、identity 和 lighting 作为需要共同检查的镜头变量。上述内容只能确认产品公开行为，不能证明人物一致性算法或国内即梦版本具有完全相同实现。`OFFICIAL-DOC`

Seedance 1.0 官方页面确认文本/图像生成和模型级原生多镜头；Seedance 1.5 Pro 发布说明确认联合音画生成，同时也承认复杂运动、多角色对白和歌唱等场景仍有限制。模型能在一次请求中生成多镜头，不等于它管理了项目剧本、资产版本、候选 Take、局部修改和最终时间线。`OFFICIAL-DOC`

直接来源：

- [Dreamina AI Creative Workspace](https://dreamina.capcut.com/ai-video/ai-creative-workspace)
- [Dreamina AI-native Creative System](https://dreamina.capcut.com/ai-video/ai-native-creative-system)
- [Dreamina Script to Storyboard](https://dreamina.capcut.com/resource/script-to-storyboard)
- [Dreamina Multi-shot Video](https://dreamina.capcut.com/ai-video/multi-shot-ai-video-generator)
- [Seedance 1.0 官方页面](https://seed.bytedance.com/en/seedance)
- [Seedance 1.0 技术报告](https://seed.bytedance.com/en/public_papers/seedance-1-0-exploring-the-boundaries-of-video-generation-models)
- [Seedance 1.5 Pro 官方发布](https://seed.bytedance.com/en/blog/sound-and-vision-all-in-one-take-the-official-release-of-seedance-1-5-pro)

### 采用与不采用

采用项目上下文、资产引用、分镜控制层和逐镜审阅的对象关系；迭代时保留核心 brief，并尽量一次改变最可能相关的控制变量。把原生音频看成一条可检查的生成路线，而不是默认跳过字幕、声音来源和时间线复核。

不依赖 Dreamina/即梦账号、项目文件或 Seedance 运行时，不把页面中的批量帧数、参考数量和质量宣传写成稳定 Skill，也不把多镜头模型请求当成整集生产主控。

### 当前落点

- 项目、资产、镜头、候选与时间线真相：[models.py](../backend/short_drama/models.py)
- 核心资产的结构化 brief 和轻量/角色标准视图/场景 coverage 策略：[tools/artifact](../backend/short_drama/tools/artifact)
- 把显式选定的本地 AssetVersion 确定性组成带源格子记录的 DRAFT 参考板：[tools/image](../backend/short_drama/tools/image)
- 参考视觉比较与兼容性检查：[tools/qa](../backend/short_drama/tools/qa)
- 图像资产任务：[tools/image](../backend/short_drama/tools/image)
- 音频与时间线检查：[tools/audio](../backend/short_drama/tools/audio)、[tools/timeline](../backend/short_drama/tools/timeline)

## LTX Studio 与 LTX-2：非破坏项目对象和局部时间修复

### 可验证结论

LTX Studio 完整源码未公开，因此只能从官方产品文档确认外部行为：Project 连接 Gen Space、Storyboard、Timeline 和 Pitch Deck；生成结果按 Session 组织；Storyboard/Timeline 引用源生成并支持重排、替换、时长、转场与音频调整。不能据此声明其内部数据库、模型路由或缓存实现。`OFFICIAL-DOC`

LTX API 的 Retake 文档确认可以替换已有视频的选定时间区间，并分别选择音频、视频或两者。LTX-2 固定源码中的 `retake.py` 进一步确认：源视频/音频先被编码为 latent，`TemporalRegionMask` 只作用于选定区间，视频和音频可独立冻结或重生成。`SOURCE-CONFIRMED`

直接来源：

- [LTX Studio Projects](https://ltx.io/blog/introducing-projects)
- [LTX Studio Tutorial](https://ltx.io/blog/ltx-studio-tutorial)
- [LTX Retake API](https://docs.ltx.io/api-documentation/api-reference/video-generation/retake)
- [LTX-2 retake.py 固定快照](https://github.com/Lightricks/LTX-2/blob/a95ab856bf29407b6b066ede0abe1846050db56c/packages/ltx-pipelines/src/ltx_pipelines/retake.py#L52-L329)

### 采用与不采用

采用 Project 内资产、生成候选和时间线引用分离；新 Take 不覆盖旧 Take；局部缺陷优先 retake、extend、gap fill 或时间线替换。相邻镜头的结束/开始状态共同参与补缝。

不把 LTX 的 temporal mask、模型 latent 或 keyframe conditioning 当成 H3 已有能力，也不从 Studio 产品说明推断其未公开 Agent 架构。

### 当前落点

- Take 选择、比较与局部返工：[tools/video](../backend/short_drama/tools/video)
- TimelineManifest 与导出：[tools/timeline](../backend/short_drama/tools/timeline)
- 镜头边界与关键帧说明：[tools/shot](../backend/short_drama/tools/shot)

## Toonflow：保留角色分工，删除固定编排

### 可验证结论

固定快照中的 Production Agent 由一个决策层调度导演规划、派生资产、分镜表、分镜面板、分镜图和监制等专业 Agent；各执行 Agent 加载独立 Markdown Skill。数据库把脚本、资产、图像、分镜、Agent 工作数据、视频和视频轨道分开保存，工作台可以为同一轨道产生多个视频并选择其中一个。`SOURCE-CONFIRMED`

同时，`production_agent_decision.md` 明确要求六阶段按顺序执行，部分阶段强制用户确认，视频生成与合成被排除在 Agent 之外。这是可读的实现事实，但不符合本项目“动态计划 + 可组合 Tool + 无人值守可恢复 + 用户随时局部修改”的目标。`SOURCE-CONFIRMED`

关键源码：

- [Script Agent](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/agents/scriptAgent/index.ts#L41-L233)
- [Production Agent](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/agents/productionAgent/index.ts#L43-L374)
- [固定六阶段决策 Skill](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/data/skills/production_agent_decision.md#L1-L210)
- [生产数据表](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/lib/initDB.ts#L428-L553)
- [批量视频候选](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/routes/production/workbench/batchGenerateVideo.ts#L58-L136)
- [选择视频候选](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/routes/production/workbench/selectVideo.ts#L8-L20)

### 采用与不采用

采用决策、专业执行、审校之间的责任边界，以及“一个镜头/轨道可以有多个候选，当前选择只是引用”的数据关系。

不采用固定六阶段、静态门禁、XML Prompt 充当项目状态、Skill 预先决定 Tool 顺序，也不把“任务完成”当成“内容质量通过”。

### 当前落点

- 动态计划和目标反馈：[production_manager.py](../backend/short_drama/production_manager.py)
- Agent loop、ContextPack、WorkOrder 和运行记忆：[agent_runtime](../backend/short_drama/agent_runtime)
- 专业委派 Tool：[tools/agent](../backend/short_drama/tools/agent)
- 中文格式角色 Prompt：[skills/formats](../backend/short_drama/skills/formats)

## ViMax：规划与渲染分开，但版本和失效必须更严格

### 可验证结论

ViMax 的 `plan_text_artifacts()` 只生成角色、storyboard、shot description 和 camera tree，在画像、关键帧、视频和最终拼接之前停下；渲染阶段再并发生成帧和镜头视频，最后使用 MoviePy 顺序拼接。Agent adapter 也把 narrative planning、novel planning 和 render 暴露为不同 Tool。规划失败会保留已产生的部分工件。`SOURCE-CONFIRMED`

它的局部修订会重写一个结构化文件并标记下游 stale，但渲染主路径主要依赖文件存在性复用输出；没有完整的候选版本图，最终视频也主要是 clip concat。因此“标记 stale”并不自动等于“所有渲染路径都会拒绝旧结果”。`SOURCE-CONFIRMED`

关键源码与论文：

- [ViMax 论文](https://arxiv.org/abs/2606.07649)
- [Script-to-video pipeline](https://github.com/HKUDS/ViMax/blob/05a48943878312d88fe5a016c12a9654940ecc43/pipelines/script2video_pipeline.py#L108-L520)
- [Agent loop](https://github.com/HKUDS/ViMax/blob/05a48943878312d88fe5a016c12a9654940ecc43/agent_runtime/loop.py#L43-L187)
- [ViMax Agent adapters](https://github.com/HKUDS/ViMax/blob/05a48943878312d88fe5a016c12a9654940ecc43/agent_runtime/vimax_adapters.py#L43-L95)
- [局部修订与 stale 标记](https://github.com/HKUDS/ViMax/blob/05a48943878312d88fe5a016c12a9654940ecc43/agent_runtime/vimax_adapters.py#L231-L276)

### 采用与不采用

采用规划与渲染分离、显式中间工件、依赖关系和失败时保留部分结果。Camera tree 或三视图只在镜头风险需要时作为可选资产或预演。

不采用文件存在即有效、结构化文件原位覆盖、所有人物固定三视图、总是选择相对最好候选，以及把顺序拼接当成完整音画时间线。

### 当前落点

- 分层长剧本规划：[hierarchical_script.py](../backend/short_drama/hierarchical_script.py)
- 可恢复内部规划入口：[tools/planning](../backend/short_drama/tools/planning)
- 版本化领域工件：[models.py](../backend/short_drama/models.py)
- 外部任务状态机：[tools/task](../backend/short_drama/tools/task)

## ComfyUI：借鉴增量执行，不复制节点产品

### 可验证结论

ComfyUI 的 workflow JSON 是节点和连接组成的 DAG。执行器只把请求的 output node 放入执行列表，并按依赖调度必要节点；缓存签名包含节点类型、`IS_CHANGED`/fingerprint、字面输入和有序祖先签名。官方 Partial Execution 文档也明确只运行所选输出分支。`SOURCE-CONFIRMED`

这些机制解决的是媒体计算图的增量执行，不提供短剧项目所需的剧本版本、人物/场景资产语义、Shot 目的、Take 选择、时间线真相或 Agent 决策策略。队列/history 是执行记录，不是业务候选版本。`SOURCE-CONFIRMED`

关键源码与文档：

- [Workflow JSON 规范](https://docs.comfy.org/specs/workflow_json)
- [Partial Execution](https://docs.comfy.org/interface/features/partial-execution)
- [执行器固定快照](https://github.com/Comfy-Org/ComfyUI/blob/3216c62e9962c3babd28a4dfea6e5aef50b8fe16/execution.py#L730-L824)
- [输入签名缓存固定快照](https://github.com/Comfy-Org/ComfyUI/blob/3216c62e9962c3babd28a4dfea6e5aef50b8fe16/comfy_execution/caching.py#L82-L127)
- [变更输入只重跑下游的测试](https://github.com/Comfy-Org/ComfyUI/blob/3216c62e9962c3babd28a4dfea6e5aef50b8fe16/tests/execution/test_execution.py#L273-L290)
- [只执行目标输出分支的测试](https://github.com/Comfy-Org/ComfyUI/blob/3216c62e9962c3babd28a4dfea6e5aef50b8fe16/tests/execution/test_execution.py#L674-L805)

### 采用与不采用

采用“输入或依赖没有变化就复用，变化时只执行目标输出所需分支”的原则，并用短剧领域对象表达影响范围。

不把 ComfyUI 图变成前台制片流程、Agent 策略或项目数据库，不为了借鉴缓存而新增文档 hash、冻结或审批机制。若未来接入 ComfyUI，它只适合作为抠像、修帧、超分、插帧等媒体处理侧车。

### 当前落点

- 语义影响预览与应用：[change_planner.py](../backend/short_drama/change_planner.py)、[change_executor.py](../backend/short_drama/change_executor.py)
- Agent 可调用变更能力：[tools/change](../backend/short_drama/tools/change)
- 未受影响 Take 与资产复用：[models.py](../backend/short_drama/models.py)

## 研究结论应该写到哪一层

| 结论类型 | 应落位置 | 示例 |
| --- | --- | --- |
| 所有视频制片长期成立的方法 | 共享 workflow | 先读取当前项目真相；检查后局部返工；时间线验收后再导出 |
| 所有剧本角色共享的职责 | `skills/shared/roles` | Story Architect 保护故事承诺与因果，Writer 交付可读场景，Critic 以目标观众冷读 |
| 某一视频格式长期稳定的媒介/生产差异 | `formats/<format>/SKILL.md`、类型补充 playbook | AI 短剧的冷启动可读性、竖屏与生成式制作；数字人口播的说话人、字幕和声画可信度 |
| 可在多种格式中组合的叙事方法 | `skills/narrative/<skill>` | 概念放大、递进兑现、荒诞喜剧；由 Agent 按项目语义选择，不做关键词路由 |
| 本项目的故事承诺、叙事意图、结尾与视听风格 | ProjectDocument 领域工件 | `StorySourceContract`、`StoryIntent`、`EndingContract`、`ProjectBible.style` 分工，不相互代替 |
| 某个 Provider 的 Prompt 语法 | Provider Skill / Prompt Compiler | H3 的 T2VA、FL2VA、Ref2VA 表达和引用标签 |
| 会随 API 变化的能力与限制 | Tool Schema / capability / Provider adapter | 时长、分辨率、文件数量、可组合输入、任务状态 |
| 项目中产生的事实和版本 | ProjectDocument 领域工件 | ScriptVersion、AssetVersion、ReferencePlan、Take、TimelineManifest |
| 一次运行中验证过的经验 | AgentRuntime `ExperienceMemory` | 某类镜头在当前项目上哪种输入更稳、某种失败应优先改哪个控制变量 |
| 尚未验证的外部主张 | 研究文档，标记 `UNKNOWN` 或 `OFFICIAL-DOC` | 厂商声称的一致性、模型榜单或闭源缓存策略 |

格式 Skill 不应保存某个样片的合同、融资、录音笔、办公室等剧情经验，也不应积累某次 Provider 失败的静态案例。稳定原则可以进入 Skill；具体运行经验由 `ExperienceMemory` 按项目、角色、Skill 和证据召回。

## 本项目的完整落地映射

| 制片关注点 | 当前实现位置 | 当前判断 |
| --- | --- | --- |
| 完整端到端生产方法 | [skills/workflows/end_to_end.md](../backend/short_drama/skills/workflows/end_to_end.md) | `CURRENT`：供 Planner 读取，可跳过、合并、重排和局部进入，不是固定节点图 |
| 视频类型差异 | [skills/formats](../backend/short_drama/skills/formats) | `CURRENT`：只保留宽粒度格式/生产差异，不枚举题材或预设具体 Style |
| 通用角色与叙事组合 | [skills/shared](../backend/short_drama/skills/shared)、[skills/narrative](../backend/short_drama/skills/narrative)、[production_profiles.py](../backend/short_drama/production_profiles.py) | `CURRENT`：中文共享职责；Agent 显式选择 Narrative Skills，Loader 只验证 ID 和角色支持 |
| 故事定向与审校记忆 | [models.py](../backend/short_drama/models.py)、[production_planner.py](../backend/short_drama/production_planner.py) | `CURRENT`：`StorySourceContract`/`StoryIntent`/`EndingContract`、`SCRIPT_LOCKED` 语义结构化与逐字/顺序保护、Narrative Skill 选择、Critic Issue Ledger、局部 Repair/Verifier 和按需最终冷读已有持久化合同 |
| 动态计划与专业委派 | [production_manager.py](../backend/short_drama/production_manager.py)、[agent_runtime](../backend/short_drama/agent_runtime) | `CURRENT`：PlanRevision、ContextPack、显式 `production_skill_role` 的 WorkOrder/AgentResult、`specialist_turns` 单步持久化及中断恢复、目标反馈和经验记忆；`assigned_agent` 不用于职责推断 |
| 可恢复内容规划 | [production_manager.py](../backend/short_drama/production_manager.py)、[models.py](../backend/short_drama/models.py) | `CURRENT`：AgentRun 总调度；Content Planning Job（兼容实现名 `PlanningRun`）只保存内容规划游标和恢复所需中间工件，最终内容回写唯一 ProjectDocument；场景及跨场修订无 40/12 低常数创作上限；当前不抽象通用 ChildJob |
| 可组合能力目录 | [tools/README.md](../backend/short_drama/tools/README.md)、[tools/defaults.py](../backend/short_drama/tools/defaults.py) | `CURRENT`：全格式共享 Tool Catalog，由 Agent 按当前缺口选择 |
| 项目工件与版本 | [models.py](../backend/short_drama/models.py) | `CURRENT`：项目、剧本、资产版本、镜头、任务、Take、时间线和导出记录是业务真相 |
| H3 路线与 Prompt | [H3 Provider Skill](../backend/short_drama/skills/providers/minimax_h3/SKILL.md)、[h3_route_planner.py](../backend/short_drama/h3_route_planner.py)、[providers/h3_prompt_skill.py](../backend/short_drama/providers/h3_prompt_skill.py)、[tools/route](../backend/short_drama/tools/route) | `CURRENT`：格式事实与 Provider 语法分层；能力变化留在 route/provider 层 |
| 参考与关键帧质量 | [tools/qa](../backend/short_drama/tools/qa)、[tools/shot](../backend/short_drama/tools/shot) | `CURRENT`：视觉重排、关键帧比较、输入兼容性和镜头级导演建议 |
| 候选、选择与局部修复 | [tools/video](../backend/short_drama/tools/video) | `CURRENT`：检查、比较、选择、Retake、Extend、Gap Fill；旧 Take 不被覆盖 |
| 语义修改与局部重算 | [tools/change](../backend/short_drama/tools/change) | `CURRENT`：先预览影响，再只失效相关下游；继续使用领域对象，不引入节点图 |
| 外部任务恢复 | [tools/task](../backend/short_drama/tools/task)、[tools/image](../backend/short_drama/tools/image) | `CURRENT`：推进、取消、重试、对账和输出恢复保持为真实任务状态机 |
| 时间线、音频和成片 | [tools/audio](../backend/short_drama/tools/audio)、[tools/timeline](../backend/short_drama/tools/timeline) | `CURRENT`：从选定 Take 重建 TimelineManifest，检查音频并记录导出 |

上表的 `CURRENT` 只表示当前代码已有对应工件、编译或 Tool 路径。Narrative Skill 是否选对、`StoryIntent` 是否真正改善不同观众体验、Critic 是否减少过度挑错、局部修订是否保护原稿优点，以及四视图/场景 coverage/真实 H3 绑定是否提高完整成片连贯性，仍属于需用真实语言模型、图像、H3 和完整播放验证的产品结论。

## 后续增补原则

1. 新研究先回答它改变的是创作判断、Provider 表达、领域工件还是执行恢复，再选择落点；不要默认再加一个 workflow。
2. 只有跨项目、跨题材稳定成立的原则才进入 Production Skill。具体样片经验进入 `ExperienceMemory`。
3. 新 Tool 应是一项边界清楚、可组合、可观察的能力；不要把“剧本到成片”封装成一个不可检查的大 Tool。
4. Provider 新能力先进入 capability 与预检，再由 Agent 按镜头风险选择；不把演示页中的最大值变成所有项目的固定目标。
5. 版本候选和局部修复继续围绕项目工件实现：新结果新增版本，显式选择当前版本，未受影响分支继续复用。
6. 对闭源产品，只吸收外部可见对象关系和交互方法；对开源项目，优先引用固定源码位置；真实质量收益仍以本项目样片和完整播放验收为准。
