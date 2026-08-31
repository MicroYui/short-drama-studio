# Phase 24：视频生成前质量优化调研

> P0 已在 [Phase 25：H3 Prompt IR、顺序候选与相对选片](36-phase25-h3-prompt-and-adaptive-takes.md) 中实现；本文保留实施前的研究判断和证据边界。

## 结论

本轮没有调用 MiniMax-H3、GPT Image 2 或其他付费生成能力。调研结论不是继续盲目扩大样片量，也不是为单个镜头增加专用字段，而是先补齐五项通用能力：

1. **优先采用 MiniMax 官方 H3 Prompt Writing Skill 的 Prompt IR。** 当前系统提交的是一段自由格式中文提示词，而官方 H3 资料要求按生成模式使用固定结构、稳定说话人 ID、精确切镜时间、原语言对白和显式参考素材关系。这是当前最直接、证据最强的质量改进。
2. **从固定 Best-of-2 改成自适应顺序生成。** 首个 Take 通过时立即停止；只有首个 Take 失败、证据不足或镜头本身高风险时，才继续生成下一个候选。它同时提高质量与资源利用率，不需要降低质量目标。
3. **增加候选 Take 的相对比较。** 当前每个 Take 被独立打分，最后按单项分数取最大值。应让观察 Tool 同时看到候选、关键资产和镜头意图，比较身份、空间、动作、声音语义和成片质感；如果都不可用，则返回“无可选 Take”，交给 Director Agent 修复。
4. **把参考素材选择升级为“两段检索”。** 先用项目隔离、元数据、角色/机位/状态和文本检索缩小候选，再让多模态模型只重排小候选集。参考图不是越多越好，每个输入都应声明它负责人物、空间、道具、声音还是运镜。
5. **运镜建模作为可选 Motion Guide Tool，而非所有镜头的必经步骤。** 成熟产品与论文都支持显式运镜控制的价值，但 H3 的公开合同只能接收参考视频，不能直接接收 6DoF 相机轨迹、深度图或姿态图。因此当前最合理的顺序是：真实运动参考 / 已通过 Take / 当前首帧低保真预演；只有对照证明有净收益后，再研究 2.5D 或 3D blockout。

这些能力继续属于 `Agent Harness + Production Skill + Tools`：Skill 提供模型和内容品类的建议策略，Agent 根据镜头状态调用，用户可以覆盖；除 H3 请求合法性、媒体存在性和时间线可执行性外，不增加创意门禁。

## 范围与证据等级

本轮研究范围包括：

- MiniMax-H3 官方 API、官方 Prompt Skill 与 AceData 传输合同；
- 即梦、可灵、LTX Studio、Google Flow、Runway、Adobe Firefly 的公开产品机制；
- MiniMax-H3、LTX Desktop、ViMax 等官方或开源源码；
- 多场景规划、运镜控制、长视频连续性、Reward/QA 与 test-time scaling 论文；
- 当前项目已有 Prompt、资产检索、路线规划、候选生成与视频检查代码。

标签沿用项目定义：`DOC-CONFIRMED`、`SOURCE-CONFIRMED`、`PRODUCT-CLAIM`、`INFERRED`、`UNKNOWN`。论文作者报告的结果在本文标为 `PAPER-REPORTED`，不自动等同于 H3 上的实际收益。

## 当前系统盘点：已有基础与真实缺口

### 已有且应保留

当前系统已经具备成熟平台常见的多数中间工件：

- Project Bible、角色/场景/道具母版与同源派生资产；
- 按镜头的 ReferencePlan、首帧/尾帧、动作节拍和连续性状态；
- Direct、Frame、First+Last、Reference 与可选视频参考路线；
- 前镜完整 Take、尾窗和当前首帧低保真二维机位预演；
- 自适应抽帧、镜内 contact sheet、相邻镜头边界检查和语义观察；
- Director Agent 的重生成、换资产、拆镜或换覆盖建议；
- Take、Timeline、局部返工和最终导出。

因此，本轮不重新设计一套 storyboard workflow，也不把三视图、深度图或 3D 场景设成全局前置条件。

### 缺口 1：当前 H3 Prompt 没有采用官方 Prompt IR

`backend/short_drama/providers/minimax_h3_compiler.py` 当前生成一段自由格式中文文本；`h3_language_prompt.py` 让 GPT-5.6-sol 在同一自由格式上增强时序和动作。它们保住了项目事实，但没有遵循 MiniMax 官方 H3 Prompt Skill 的字段顺序和表达合同。

MiniMax 官方仓库在 commit [`d21241f0a4b3acbb34c97dae47fa417b7065e438`](https://github.com/MiniMax-AI/MiniMax-H3/commit/d21241f0a4b3acbb34c97dae47fa417b7065e438) 中提供了可移植的 [`h3-prompt-writing` Skill](https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing)：

- T2VA / I2VA / FL2VA / L2VA 使用 `integrated_multimodal_description`、`overall_soundscape`、`non_diegetic_music`；
- Ref2VA 使用 `subject_definitions`、`summary`、`retention_analysis`、`detailed_description`、`overall_soundscape`、`non_diegetic_music`；
- 说明部分使用英文，对白、歌词和画面内文字保留原语言；
- 说话人使用稳定 `(S1)`、`(S2)`，对白用 `<d>[Chinese] ...</d>`；
- 画外音明确要求 on-screen 角色嘴唇保持闭合；
- 切镜使用精确时间，如 `[Shot 2] At 00:03.500`；
- FL2VA 明确描述从首帧到尾帧的可观察动作路径，而不是重复两张静态图；
- Reference 模式为每个 `<Picture N>`、`<Video N>`、`<Audio N>` 声明用途和保留关系。

官方仓库还把 H3-Context-IR 描述为 H3 完整系统中的关键语义处理层，并提供独立的 [Context-IR API](https://platform.minimax.io/docs/api-reference/video-generation-v2-h3-context-ir)。该接口只把多模态输入转成增强后的结构化 prompt，不生成视频。`DOC-CONFIRMED` / `SOURCE-CONFIRMED`

**判断：** 先在客户端实现官方 Skill 格式，比继续在现有自由格式后追加更多规则更合理。现有 ShotPlan、锁定对白、资产绑定和连续性信息仍是输入；变化只发生在 H3 专属 Prompt Compiler Skill。

### 缺口 2：自动运行固定为每镜两个候选

当前 `service.py` 的真实 AUTO_DRAFT 路线以 `candidate_count=2` 编译，并在执行时一次性排队全部候选。它会在第一个 Take 已经足够好时仍支付第二个 Take，也无法根据第一次 QA 结果选择不同修复路线。

Video-T1 把视频生成质量改进表述为 verifier 引导的 test-time search，VideoReward 则用多维人类偏好训练相对评价器。我们无法在黑盒 H3 内部执行 Tree-of-Frames 或 noisy-latent reward guidance，但可以迁移其外层原则：**逐个生成、及时评价、只在需要时增加计算量。** [Video-T1](https://arxiv.org/abs/2503.18942) [VideoReward](https://arxiv.org/abs/2501.13918) `PAPER-REPORTED` / `INFERRED`

建议默认策略：

- 首个候选生成并完成技术检查、语义观察和镜间边界观察；
- 若通过且置信度足够，立即停止；
- 若失败但同路线仍有随机性修复可能，再生成一个同路线候选；
- 若失败指向关键帧、参考冲突、空间或动作规划，则不浪费同 prompt 重抽，先让 Director Agent 选择换关键帧、换路线、拆镜或换覆盖；
- 用户可以指定固定候选数或忽略建议；系统只执行用户预算上限和 H3 合法边界。

### 缺口 3：候选选择不是 pairwise comparison

当前 `_select_best_current_takes` 依据 `qa_status`、独立 `qa_report.score` 和候选序号排序。视频观察器会检查单个 Take 及其与前镜的边界，但没有同时比较同一镜头的多个候选。

独立绝对分数容易出现两个问题：

- 不同调用的评分尺度轻微漂移，1 号的 4.2 与 2 号的 4.0 未必构成可靠偏好；
- 一个候选可能动作更准，另一个人物更稳，单分数隐藏了真正的取舍。

建议新增 `CompareTakesTool`：输入镜头意图、批准资产、候选的 contact sheet/短采样和上一镜边界，输出各维比较、推荐 Take 或 `NONE_ACCEPTABLE`。比较维度保持通用：身份与服装、场景/道具连续性、动作与朝向、对白/声音来源语义、构图与视觉质感。它只给 Agent 建议，不直接成为不可覆盖的 gate。

### 缺口 4：参考检索只有规则与文本，没有视觉重排

当前 `asset_catalog.py` 以项目范围、类型、角色、tag、名称和 token overlap 排序；`reference_planner.py` 再按 role、angle、framing、tag、同母版 lineage 和 priority 选择。这个结构正确、便宜、可解释，但尚未验证图像内容是否真的匹配目标机位、构图和当前状态。

ViMax commit [`05a48943878312d88fe5a016c12a9654940ecc43`](https://github.com/HKUDS/ViMax/commit/05a48943878312d88fe5a016c12a9654940ecc43) 中的 `ReferenceImageSelector` 在候选较多时先做文本筛选，再让多模态模型重排图像；`BestImageSelector` 同时查看参考图和生成候选，按人物、空间、描述符合度和边框等可观察条件选择。`SOURCE-CONFIRMED`

可迁移方式：

```text
项目隔离 + 结构化过滤 + 文本/BM25 候选召回
  → 只加载小候选集图像
  → VLM 按目标镜头、人物视角、空间布局、状态与冗余度重排
  → H3 Prompt IR 明确每张素材的职责
```

这不是用 VLM 代替资产数据库；数据库和元数据负责召回与隔离，VLM 只承担小范围视觉判别。

## 成熟产品公开机制：共同点是什么

闭源产品只能用于学习公开方法，以下不代表其内部实现已知。

| 产品 | 公开机制 | 可迁移原则 |
| --- | --- | --- |
| [即梦](https://jimeng.jianying.com/) | 文/图生视频、首尾帧、运镜控制、智能画布和多层编辑 | 把关键帧、运镜和局部返工作为独立创作工件，不把一次生成当最终成片 |
| [可灵 VIDEO 3.0](https://app.klingai.com/cn/quickstart/klingai-video-3-model-user-guide) | 自动/自定义 Multi-Shot、Element 角色/物体/场景绑定、角色声线、多说话人台词归属 | 在生成前显式声明镜头段、元素身份与说话人，不把所有约束塞成无结构自然语言 |
| [LTX Studio](https://website.ltx.studio/) | Elements、Storyboard、Camera、Keyframes、References、Timeline、Retake | 资产、镜头、Take 和局部修改是可复用工件；失败后局部替换而非整集重做 |
| [Google Flow](https://blog.google/innovation-and-ai/products/flow-video-tips/) | Ingredients、Camera Controls、Frames to Video、Scenebuilder | 一致主体与镜头控制分开；已有片段可以继续、扩展和重组 |
| [Runway Gen-4 References](https://help.runwayml.com/hc/en-us/articles/40042718905875-Creating-with-Gen-4-Image-References) | 最多三张活动参考、复杂合成分步迭代、生成结果可继续作为参考 | 使用干净、职责单一的少量参考；复杂画面先得到稳定中间资产再组合 |
| [Adobe Firefly Motion Reference](https://helpx.adobe.com/firefly/web/work-with-audio-and-video/work-with-video/match-camera-motion-to-reference-video.html) | 用短参考视频传递 pan、tilt、zoom 和运动路径，并可与首尾帧组合 | 运镜可以是一等控制媒体，但应在模型合同支持的形式内传递 |

这些产品的共同方向不是“固定做三视图”或“固定做 3D”，而是：**建立可复用元素，按镜头挑选最有信息量的控制输入，生成后支持局部比较和返工。** `PRODUCT-CLAIM` / `INFERRED`

## 开源实现与论文可借鉴部分

### 1. 模型专属 Prompt Enhancer，而非通用润色器

LTX Desktop 最新审查快照 commit [`68cd86c15e5fd25f56229ea63c0dbcb0338f7812`](https://github.com/Lightricks/LTX-Desktop/commit/68cd86c15e5fd25f56229ea63c0dbcb0338f7812) 的 prompt enhancement 会根据目标模型、LoRA/Control 类型和 keyframe 模式选择不同 instruction：

- 多关键帧被视为各时间点的视觉事实；
- 先描述每张图的可见状态，再描述连接动作；
- 不创造两张图都不存在的折中姿态；
- 相同人物是同一人物移动，不得复制；
- Prompt Template 的固定骨架由代码填充，LLM 只补内容槽位。

这说明 Prompt Enhancer 应匹配模型训练/官方 caption 格式。对当前系统而言，最直接的迁移不是复制 LTX prompt，而是把 MiniMax 官方 H3 Prompt Skill 实现成 H3 专属编译器。`SOURCE-CONFIRMED`

### 2. 分层叙事与镜头工件

- [VideoDirectorGPT](https://arxiv.org/abs/2309.15091) 让 LLM 先产生多场景、实体、空间布局和跨场一致性分组，再交给视频生成器；
- [MovieDreamer](https://proceedings.iclr.cc/paper_files/paper/2025/hash/7d4647db960780c8b5a4e7d9e4a58d68-Abstract-Conference.html) 用全局叙事层与扩散渲染层生成长视觉序列；
- [DrawVideo](https://arxiv.org/abs/2605.23508) 把长视频分成多镜，每镜由结构草图、appearance prompt、motion prompt 和派生动作状态关键帧控制。

当前项目已经具有分层剧本、Scene/Shot、资产和关键帧，不需要再引入另一套长视频 workflow。可迁移的是在 Keyframe Tool 中更明确地区分：外观锚点、构图/空间锚点、动作状态和运动描述。`PAPER-REPORTED`

### 3. 运镜建模的真实边界

- [CameraCtrl](https://arxiv.org/abs/2404.02101) 使用相机姿态与轨迹控制 T2V；
- [MotionCtrl](https://arxiv.org/abs/2312.03641) 分开控制相机运动和物体轨迹；
- [CamI2V](https://arxiv.org/abs/2410.15957) 用极线注意力增强 I2V 的几何和相机控制；
- [ReCamMaster](https://github.com/KlingAIResearch/ReCamMaster) 从现有视频和目标相机轨迹重新渲染机位。

这些方法证明显式相机几何有研究价值，但它们依赖模型内部的 pose、trajectory、depth 或 attention 接口。H3 当前公开 API 只提供 text、first/last frame 和 reference image/video/audio；因此不能把论文中的 6DoF 控制强度直接宣称为 H3 能力。`PAPER-REPORTED` / `DOC-CONFIRMED`

当前可实施的 H3 转译层是：

1. 用户上传或素材库中的真实运动参考；
2. 同动作、同覆盖的已通过 Take 或其尾部窗口；
3. 从当前批准首帧生成的低保真 2D/2.5D motion guide；
4. 后续若对照有收益，再增加 3D blockout → reference video。

Motion Guide 是否值得使用由 Agent 按镜头推荐；静态对话、近景反应或已有强首尾帧时可以完全不使用。

### 4. 长镜头与跨片段连续性

[FramePack](https://arxiv.org/abs/2504.12626) 通过固定大小的重要上下文打包和提前建立终点信息减少长序列漂移。其内部方法不能直接装进 H3，但可以支持两个外层原则：保留边界状态，必要时先确定终态。`PAPER-REPORTED` / `INFERRED`

当前系统已经有上一镜边界、first/last frame 和 previous-tail guide。下一步不是增加更多连续性字段，而是让 Route Agent 在以下选项中选择最小充分输入：

- 单首帧：起点重要、终点可自由；
- 首尾帧：终态必须锁定且中间动作可解释；
- reference pack：身份/空间/声音比精确边界更重要；
- reference video：运动路径或同动作接续比静态构图更重要。

### 5. QA 与 test-time compute

[VideoReward](https://arxiv.org/abs/2501.13918)、[VideoScore](https://arxiv.org/abs/2406.15252)、[VBench](https://openaccess.thecvf.com/content/CVPR2024/papers/Huang_VBench_Comprehensive_Benchmark_Suite_for_Video_Generative_Models_CVPR_2024_paper.pdf) 和 [VBench-2.0](https://github.com/Vchitect/VBench/blob/master/VBench-2.0/README.md) 都避免只用一个模糊“好看”分数，分别评价视觉、运动、时间一致性、文本对齐、主体/物理与常识。`PAPER-REPORTED`

本项目不需要复制完整 benchmark；应把现有通用失败分类用于两个用途：

- 单 Take 观察：定位哪里、何时、哪类问题发生；
- 多 Take 比较：在相同镜头意图下判断哪个候选更适合进入时间线。

声音无需新增独立声纹 QA；但声音来源语义仍属于成片内容检查，例如“录音笔播放”不能被误判为画面人物本人说话。

## 图像生成质量：先稳定资产，再生成镜头图

OpenAI 官方 [Image generation guide](https://developers.openai.com/api/docs/guides/image-generation) 区分单次 Images API 与支持多轮编辑的 Responses API；GPT Image 2 支持图像生成和编辑。Runway 与 ViMax 的公开实现也都强调参考图职责、候选比较和迭代中间资产。`DOC-CONFIRMED` / `SOURCE-CONFIRMED`

适合当前真人短剧的图像链路是：

```text
真实授权素材 / 用户上传 / 已批准母版
  → 清理背景、光线和不稳定细节，形成 canonical asset
  → 派生角色视角、场景局部机位、道具状态
  → 按镜头检索少量参考
  → 生成或编辑 shot-specific keyframe
  → VLM 比较身份、空间、动作起点、构图和文本错误
  → 批准版本进入 H3 路线
```

关键选择：

- 角色三视图是**可用资产类型**，不是每个项目必须完成的表单；目标镜头只需要与目标角度最匹配的视图；
- 场景优先建立 master layout、核心出入口/通道和常用 coverage，而不是无限生成局部图；
- 道具在剧情状态变化明显时才派生状态图；
- 改衣服颜色等语义修改优先走图像 edit/局部编辑并由 ImpactPlan 找到受影响镜头，不重新生成整套无关资产；
- 当前 Azure GPT Image 2 URL 是否支持 edits、多图输入和多轮 Responses 仍为 `UNKNOWN`，实现前应做不生成视频的兼容性检查，不能直接假设与 OpenAI 公网 endpoint 完全一致。

## 建议的 AI-native 能力划分

| 能力 | 类型 | 职责 | 是否阻止用户 |
| --- | --- | --- | --- |
| `H3PromptCompilerSkill` | Model Skill | 把 ShotPlan、对白、媒体职责编译成官方 H3 Prompt IR | 否；用户可编辑最终 prompt |
| `ReferenceRetrieverTool` | Tool | 项目隔离、结构化/文本召回和小候选集视觉重排 | 否；用户可手选、上传或不采用 |
| `KeyframeComposerTool` | Tool | 用批准资产生成/编辑镜头首尾帧并保留来源 | 否 |
| `KeyframeCompareTool` | Tool | 对候选图做相对比较，允许 `NONE_ACCEPTABLE` | 否 |
| `MotionGuideTool` | Tool | 从真实参考、Take、首帧预演或未来 blockout 生成 H3 reference video | 否；仅建议式调用 |
| `InspectVideoTool` | 既有 Tool 扩展 | 自适应抽帧、边界检查和可定位语义观察 | 否 |
| `CompareTakesTool` | Tool | 同时比较同镜候选，返回推荐或无可用候选 | 否 |
| `AdaptiveGenerationPolicy` | Skill/Harness 策略 | 首个 Take 后决定停止、再抽或转 Director 修复 | 否；遵循用户候选数/预算设置 |
| `DirectorRepairTool` | 既有 Tool | 比较重抽、换关键帧、换路线、拆镜和换覆盖 | 否 |

这里没有新增固定 workflow。AutonomousRun 只记录 Agent 实际选择的 Tool 调用、Provider task 和恢复进度。

## 实施优先级

### P0：下一次 H3 生成前完成

1. 实现官方格式的 `H3PromptCompilerSkill`，用确定性模板保证字段、顺序、标签和时长，再让 GPT-5.6-sol 填充内容；现有 locked facts 校验继续保留。
2. 为 Ref2VA 明确 `subject_definitions` 与 `retention_analysis`，不再只在 prompt 尾部附加一句“视频连续性参考”。
3. 把 AUTO_DRAFT 的固定 `candidate_count=2` 改为可恢复的顺序候选策略。
4. 增加 `CompareTakesTool` 和 pairwise 结果工件；若都失败，不选“相对最好但不可用”的 Take。
5. 在 H3 提交前继续执行图片/媒体存在性、API 合法组合和关键帧可观察冲突检查；创作风险仍是建议，不是硬门。

P0 全部可以用单元/集成测试与 Fake Provider 完成，不消耗 H3 额度。

### P1：图片制作与首轮真实 H3 时验证

1. 在现有资产 Catalog 上增加小候选集多模态重排；
2. 增加 `KeyframeCompareTool`，把“好看”拆成人物、空间、动作起点、构图和错误文字；
3. Route Agent 根据镜头意图比较 first frame、first+last、reference pack 和 motion guide；
4. 用少量代表性镜头做 A/B：普通对白、多人/道具、明显运镜或动作接续；只验证官方 Prompt IR 与现有 prompt、顺序候选和 pairwise judge 是否真正提高可用率。

### P2：有证据后再做

- 2.5D/3D 场景 blockout 和相机轨迹转 reference video；
- depth/pose/canny 等本地控制链路；
- 本地人物一致性模型或训练型 reward model；
- 更复杂的长镜头分段与 overlap 策略。

这些方法在其他模型上有依据，但对 H3 的净收益目前为 `UNKNOWN`，不应抢占 P0。

## 首轮验证应回答什么

完成 P0 后，真实生成不需要做大而全实验，只需回答四个直接影响成片和成本的问题：

1. 官方 H3 Prompt IR 是否比当前自由格式 prompt 更稳定地执行对白归属、动作顺序和 reference 关系；
2. 首个候选通过后停止，是否减少无意义第二候选且不降低最终可用率；
3. pairwise judge 是否比独立分数更符合人工选片；
4. Motion Guide 在“明确运镜/同动作接续”镜头中是否优于首帧或首尾帧，在普通镜头中是否反而引入干扰。

实验结果直接更新 Skill、Prompt Compiler 和 Harness 策略，不建立冻结层、SHA 锁定或额外审批门。

## 仍为 UNKNOWN

- AceData 的 `/minimax/videos` 是否在服务端自动调用了 MiniMax 官方 H3-Context-IR；其公开 [H3 集成文档](https://platform.acedata.cloud/documents/minimax-videos-integration) 没有说明。客户端仍应生成结构正确的 prompt，因为这是当前唯一可控层。
- 官方 H3 英文结构化字段通过 AceData 网关提交后，相对现有中文自由格式的实际增益。
- Azure GPT Image 2 部署对 edits、多参考图、mask 与 Responses API 的具体兼容范围。
- GPT-5.6-sol 的 pairwise Take 选择与人工选择在跨剧本上的一致率。
- 2D、2.5D、3D motion guide 对不同镜头类型的收益边界。

## 本轮源码快照

| 项目 | Commit | 本轮使用部分 |
| --- | --- | --- |
| [MiniMax-H3](https://github.com/MiniMax-AI/MiniMax-H3) | `d21241f0a4b3acbb34c97dae47fa417b7065e438` | 官方 Prompt Skill、Context-IR 与模式合同 |
| [LTX Desktop](https://github.com/Lightricks/LTX-Desktop) | `68cd86c15e5fd25f56229ea63c0dbcb0338f7812` | 模型专属 prompt enhancement、多关键帧视觉事实 |
| [ViMax](https://github.com/HKUDS/ViMax) | `05a48943878312d88fe5a016c12a9654940ecc43` | 两段参考检索、候选图比较、camera tree/过渡帧思路 |

源码只用于理解可迁移方法；本轮没有安装其模型权重、运行真实 Provider，也没有把整个上游 workflow 复制进当前产品。
