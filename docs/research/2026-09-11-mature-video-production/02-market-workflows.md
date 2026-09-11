# 成熟 AI 视频产品的可借用制作方法与交互

调研时间：2026-09-11。范围：Google Flow / Veo、Runway、LTX Studio、Luma、Adobe Firefly。只使用并实际打开官方帮助、API 文档、教程与发布说明；未登录生成、未购买订阅、未消耗媒体额度。

## 判断

最有价值的共同做法，是把**外观、运动、表演、声音、剪辑和局部修改分别控制，再组合成作品**。这些产品已经把许多“再解释一遍、重新抽一次”的工作变成了明确的素材输入和编辑操作。Short Drama Studio 可以借用这些制作机制，继续保持“剧本 / 镜头 / 成片 + 项目资产 + 常驻 Agent”的产品形态。

这轮材料能够确认用户可用的界面、输入和 API 行为，不能证明某家产品整片一次成功率更高，也不能证明其内部使用了多少 Agent、怎样通信、怎样自动审片。没有发现足以公开复用的闭源内部专业系统提示词。本文的专业提示词建议来自官方公开模型指南与制作教程；后面的本地模板是针对本项目重新编写的草案。

“效果好、少返工、快”的机制性机会主要有三处：在便宜的静帧阶段发现外观问题；用已有画面、表演和声音减少视频模型需要猜测的内容；发现问题后修改最小范围。实际收益需要本项目用同一任务测量，不能从演示视频或厂商表述直接推导。

## 五个产品分别值得借什么

| 产品 | 已公开且可操作的机制 | 借用价值 | 不能由此推断的内容 |
| --- | --- | --- | --- |
| Google Flow / Veo | 素材作为 Ingredients 和 `@` 引用；首尾帧；保存视频帧再复用；短片段编辑；场景序列剪辑 | 让已定人物、场景和前镜画面成为下一次生成的实际输入，减少反复描述 | 不知道内部 Agent 架构；首尾帧和引用也不保证完整连续性 |
| Runway | Gen-4.5 图生视频专用提示；References；Act-Two 表演驱动；Aleph 2.0 先编辑静帧再传播到视频 | 最完整地展示了“视觉确定、运动确定、表演确定、局部修正”四种不同操作 | 公开教程仍需要作者判断与剪辑，不是全自动制片成功率证明 |
| LTX Studio | 图片生成后动画；项目 Elements；Storyboard 排列与音频；Retake | 生成资产与叙事序列相互衔接，而非全部放进一个对话窗口 | LTX Studio 云端能力不等同于本项目复用的 LTX Desktop 编辑代码 |
| Luma | 当前 Agents REST API 的异步任务；Ray 3.2 的生成、编辑、扩画幅等独立模式；视频编辑参考帧 | 按修改目的选择能力；把来源视频与引导帧当作强控制输入 | 品牌名含 Agents 不能证明是公开的多 Agent 协作系统 |
| Adobe Firefly | 运镜参考视频；首尾帧；时间线上定位声音并用录音指导声音时机 | 将“慢慢推近”“这里碰一下”等模糊要求转为可复用参考和时序输入 | 文档中的视频编辑器仍标注 beta，不能统称所有功能为已成熟稳定 |

对应依据：[Flow 生成帮助](https://support.google.com/flow/answer/16353334?hl=en)、[Flow 编辑帮助](https://support.google.com/flow/answer/16935718?hl=en)、[Runway Gen-4.5](https://help.runwayml.com/hc/en-us/articles/46974685288467-Creating-with-Gen-4-5)、[LTX 官方教程](https://ltx.io/blog/ltx-studio-tutorial)、[Luma 当前 API](https://docs.agents.lumalabs.ai/)、[Firefly 运镜参考](https://helpx.adobe.com/firefly/web/work-with-audio-and-video/work-with-video/match-camera-motion-to-reference-video.html)。详细证据与限制见下面各项。

## 十二项可落地做法

### 1. 参考资产要能解决当前镜头的问题

**官方机制。** Flow 提醒人物、商品参考尽量使用干净背景，环境或风格图避免无意带入其他主体；多个素材应在视觉上相容。Runway References 推荐光线均匀、表情中性的主体图，可以用草图约束构图、视线和物体位置；复杂元素可以先分别迭代，再把结果当作新参考。[Flow 素材输入](https://support.google.com/flow/answer/16353334?hl=en)、[Runway References](https://help.runwayml.com/hc/en-us/articles/40042718905875-Creating-with-Gen-4-Image-References)

**本地适配建议。** 本项目已有对象、具体媒体和 `@` 绑定，重点应检查绑定质量：当前镜头究竟需要正面身份图、侧面、全身服装、手持道具、空间布局还是风格参考。Worker 只收到本镜需要的图片、用途及简短理由。缺少关键视角时先做一次有目的的参考图派生，避免把全部项目图片塞给视频模型。此处是对既有引用机制的质量改进，不能再建一份资产真相。

**限制。** 清晰参考是有利条件，不是身份一致性保证。Runway 的“最多三个引用”是该功能的限制，不能复制为 H3 或整个项目的资产数量限制。

### 2. 先确定静帧，再付出视频生成时间

**官方机制。** LTX 教程先生成图，再选中图进行动画。Runway Edit Studio 则把视频修改先预览为静帧，调整满意后传播到视频；官方于 2026-05-21 发布 Aleph 2.0 / Edit Studio。[LTX 官方教程](https://ltx.io/blog/ltx-studio-tutorial)、[Runway Edit Studio 发布说明](https://runway.com/news/introducing-aleph-2-and-edit-studio)

**本地适配建议。** 对首次出现的主要角色、商品特写、复杂双人构图，Agent 先得到可用首帧并检查人物数、服装、道具位置、构图，再生成运动。已有合适图片的镜头直接复用。检查通过自动继续，用户明确要求看图时才停下。这样利用静帧提前暴露外观错误，同时保留目标驱动的自主执行。

**限制。** 不能把“每镜必须先生成新图、每张必须人工批准”写成工作流。对纯运动试验、已经存在好视频的修改、某些文本视频能力，这一步会徒增耗时。

### 3. 区分图生视频、文生视频、视频编辑的提示词

**官方机制。** Runway 当前 Gen-4.5 帮助明确区分：图生视频主要描述运动，文生视频同时描述画面与运动。其图生视频指南指出输入图已承载构图、主体、光线和风格，文本负责主体动作、环境变化、镜头、时序、方向与速度；新物体或变形才需要补充视觉信息。[Gen-4.5 使用说明](https://help.runwayml.com/hc/en-us/articles/46974685288467-Creating-with-Gen-4-5)、[Gen-4.5 图生视频提示指南](https://help.runwayml.com/hc/en-us/articles/48324313115155-Image-to-Video-Prompting-Guide)

**本地适配建议。** 保留完整分镜作为创作内容，Provider 编译器按实际模式取用必要字段。图生视频不要重复改写人物长相、服装、画面布置；编辑现有视频只说明目标变化；文生视频才完整描述视觉与动作。生成输入应能追溯回分镜与绑定素材，编译器不另行加戏。

**限制。** 这是模式级原则，具体措辞要通过 H3 实测。不能直接把 Runway 英文模板当成所有 Provider 的通用格式。

### 4. 用时长和可见动作控制复杂度

**官方机制。** Google 的视频提示指南建议描述可见动作，并考虑动作能否在片段时长内完成。Runway Gen-4.5 允许自然语言时序和近似时间标记，但同样要求动作适配时长。较早的 Gen-4 指南更强调一次生成围绕一个场景，避免复杂的连续场景变化。[Google 视频提示指南](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/video-gen-prompt-guide)、[Runway 图生视频指南](https://help.runwayml.com/hc/en-us/articles/48324313115155-Image-to-Video-Prompting-Guide)、[Runway Gen-4 旧版指南](https://help.runwayml.com/hc/en-us/articles/39789879462419-Gen-4-Video-Prompting-Guide)

**本地适配建议。** 分镜方法先问“观众在这几秒具体看到什么变化”。把“她感到震惊”写成可见表演，例如手停住、抬眼、呼吸停顿；把互不相干的动作拆开，把对白按实际朗读时间安排。若一个生成组承载多个镜头，应明确切点、人物映射和动作边界。检测过载用于帮助 Agent 改善拍法，不应成为固定秒数或固定镜头数门槛。

**限制。** 单镜头偏好不是禁止 H3 的多镜头能力。较新模型能跟随更复杂指令，不代表所有复杂长提示都更可靠。

### 5. 连续性使用实际画面和关系记录

**官方机制。** Flow 可以把暂停帧保存为图片，再用作 Ingredients 或首尾帧。Runway 图生视频指南也给出用上一段最后一帧生成下一段、再在编辑器拼接的方法。[Flow 场景编辑与保存帧](https://support.google.com/flow/answer/16935718?hl=en)、[Runway 延续画面方法](https://help.runwayml.com/hc/en-us/articles/48324313115155-Image-to-Video-Prompting-Guide)

**本地适配建议。** 相邻镜头按需要携带：实际上一镜末帧、角色屏幕方位、视线方向、道具持有者、动作结束状态。连续动作优先用画面接续，换角度则共享身份与空间参考。工具应能把已选视频某一帧登记为本镜参考，并保留用途和帧位置。

**限制。** 末帧有运动模糊、遮挡或手脸错误时不宜继续传播。不能为了首尾相接牺牲导演需要的换机位，也不能把帧相似度当作叙事连续性的全部判断。

### 6. 对白和表演难镜头采用专用驱动方式

**官方机制。** Act-Two 用表演视频驱动角色图或角色视频。官方多角色对白教程先录对白、每个角色分别制作表演，再以共享底片分别驱动并合成；这是明确的人类参与教程。人物图模式能转移身体手势；人物视频模式保留底片环境及运镜，主要改变脸部。底片短于表演会出现来回循环，因此输入时长匹配很关键。[Act-Two 使用说明](https://help.runwayml.com/hc/en-us/articles/42311337895827-Performance-Capture-with-Act-Two)、[官方多角色对白教程](https://help.runwayml.com/hc/en-us/articles/41748090660499-Creating-Multi-Character-Dialogues-with-Act-Two)

**本地适配建议。** 不要让所有镜头都通过“长提示同时猜口型、台词、动作、机位”完成。对白准确性优先的片段可以先确定台词音频与节奏，再走已接入的声音/表演能力；没有这类能力时明确标注为候选 Provider 能力，不能把普通 H3 重试包装成表演驱动。复杂双人对话可选择正反打、反应镜头或有意设计的合成方案，先降低单次任务难度。

**限制。** Act-Two 当前官方说明为单角色输入，最多 30 秒；多角色教程仍有裁剪、合成和录制成本。不能据此承诺本项目已可全自动精确口型。

### 7. 局部修改先选正确的操作

**官方机制。** Aleph 2.0 以编辑动作和精确变化描述为核心，能用编辑后的静帧指导视频变化；额外运动指令只在确实需要新运动时添加。Luma 当前 FAQ 明确区分 `video_edit` 与 `video_reframe`：前者重新解释内容并控制运动保留，后者保留源帧并向外扩画幅。[Aleph 2.0 提示指南](https://help.runwayml.com/hc/en-us/articles/52150503729171-Aleph-2-0-Prompting-Guide)、[Luma 当前 FAQ](https://docs.agents.lumalabs.ai/guides/faq)

**本地适配建议。** 用户说“第 5 镜太长”优先剪辑；“字幕早了”改字幕时间；“对白对、背景不对”考虑背景编辑；“横屏转竖屏”先判断裁切还是扩画幅；只有需要新增画面时才生成。修复输入带上选中媒体、时间段、修改目标、必须保留内容，输出直接更新本镜采用结果和时间线。

**限制。** 局部编辑是否保留无关内容仍需看实际结果。没有接入对应模型时只能使用已有的编辑能力或清楚说明限制，不能从 LTX 原界面按钮推导本项目拥有 Retake/Extend 模型能力。

### 8. 复杂运镜用参考运动减少文字歧义

**官方机制。** Adobe 的 Firefly 帮助支持用参考视频指导摇移、推拉、俯仰与运动路径；该文档更新于 2026-08-18。该模式可同时选首尾帧，参考片段有明确时长与文件限制。[Firefly 运镜参考帮助](https://helpx.adobe.com/firefly/web/work-with-audio-and-video/work-with-video/match-camera-motion-to-reference-video.html)

**本地适配建议。** 先把常用运镜保留为短而具体的专业词汇和可视参考；复杂机位有必要时让用户提供参考，或者复用项目中已有适用片段。能力支持时传入实际运动参考，不支持时仅作为 Agent 观察和编译提示依据，并说明这种区别。

**限制。** 运镜参考是 Firefly 特定模式能力，不能要求现有 H3 接口凭一个视频 URL 就接受该控制。固定机位与简单推进仍应走轻量方案，不需要为每镜追加参考视频。

### 9. 声音与画面在时间线上精确组合

**官方机制。** Firefly 视频编辑器可以把播放头放在目标帧，录一段声音节奏指导效果生成，然后随画面预览并把结果添加到音轨。文档更新于 2026-06-16，编辑器标注 beta。Google Veo 提示指南则将对白、环境声、音效分别描述。[Firefly 时间线声音生成](https://helpx.adobe.com/firefly/web/firefly-video-editor/generate-audio/generate-sound-effects.html)、[Google 视频提示指南](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/video-gen-prompt-guide)

**本地适配建议。** 对白、旁白、音效、环境声、音乐各自保留实际媒体和时间信息。原声已经合适就直接采用；声音不合适时可替换或重混相应轨道，无需重生成画面。声音 QA 读取真实音轨、转写与对齐结果；画面抽帧只能辅助定位动作，不能证明台词、节奏或口型合格。字幕从实际采用的声音对齐生成，再和原台词比较。

**限制。** 声音模型、原生音频与字幕能力需要分别验证。这轮 Adobe 材料确认了音效生成和编辑，没有足够证据将其扩展为该编辑器完整的语音与字幕流程。

### 10. 对话作用在明确选区，常规编辑可直接做

**官方机制。** Flow 帮助列出 Agent 编辑入口：把一个或多个视频加到输入框并描述变化；其片段编辑要求先选具体片段。Scenebuilder 提供排序、裁切、预览等直接操作。这里能够确认的是“选中对象 + 自然语言编辑 + 直接操作”并存。[Flow 编辑与场景构建](https://support.google.com/flow/answer/16935718?hl=en)

**本地适配建议。** 本项目的当前镜头、选中字幕、时间段和资产选择应直接成为请求上下文，让用户不用再解释“刚才那条、右边这个、第几秒”。保存、裁切、音量等确定操作走工具；开放创作要求走 Agent。完成后展示改了哪里、影响哪些采用位置、当前能播放什么。制作任务的工具等待与需要用户选择分开显示，避免让用户通过“继续”驱动普通恢复。

**限制。** 不照搬 Flow 的版本堆叠界面；本项目仍直接更新当前内容，Trace 保留实际过程。闭源内部如何让 Agent 知道选区没有公开，需自行实现前后端契约。

### 11. 方法库按制作问题加载

**官方机制。** Runway 把 References、图生视频、表演驱动、多人对白、视频编辑分为独立指南；LTX 教程也把 Elements、动画、Storyboard 与 Retake 分为不同用途。它们提供的成熟知识是具体任务方法，而不是一篇包办所有环节的通用提示。[Runway References](https://help.runwayml.com/hc/en-us/articles/40042718905875-Creating-with-Gen-4-Image-References)、[Act-Two 多角色教程](https://help.runwayml.com/hc/en-us/articles/41748090660499-Creating-Multi-Character-Dialogues-with-Act-Two)、[LTX 教程](https://ltx.io/blog/ltx-studio-tutorial)

**本地适配建议。** 将有效方法整理为短的可组合 recipe，例如“人物参考选择”“连续动作接镜”“对白表演”“局部视频修复”“成片声音整理”。每项只写适用条件、必需输入、常见失败原因、可用操作与完成判断；按本次任务读入所需项。Provider 的限制、负面提示规则、可用输入模式保留在适配知识层，项目 Style 继续由用户内容决定。

**限制。** 官方指南拆分并不证明其内部用了 Skill 系统。这是对公开知识组织方式的本地设计建议。避免为每个 recipe 新建一个长期 Agent，也不把 recipe 变成强制审批流程。

### 12. 可并行的生产任务异步执行，模型负责需要判断的节点

**官方机制。** Luma 当前 Quickstart 和 FAQ 公开的是标准异步 REST 行为：提交 generation，按 ID 查询状态，完成后取得输出；并发配额和速率限制由客户端与套餐决定。没有要求语言模型反复参与轮询。[Luma Quickstart](https://docs.agents.lumalabs.ai/)、[Luma FAQ](https://docs.agents.lumalabs.ai/guides/faq)

**本地适配建议。** 只要镜头输入已经具体且依赖就绪，Provider 排队、轮询、下载、落盘、媒体探测应由后台任务推进。Agent 在选择拍法、修复内容或处理语义问题时再介入。独立镜头并行，共享参考或相接动作按实际依赖等待；不要为每一次查询任务状态消耗一次完整 Leader/Worker 对话。

**与当前代码诊断对齐。** [现场诊断](01-current-diagnosis.md)已确认 H3 等待不再需要模型决策；299 次 `task.advance` 不能当成 299 次模型调用。此项下一步对应的具体缺口是等待仍占 Worker 槽位、整批结果返回及后继唤醒，不是从零实现确定性轮询。

**限制。** 这是从公开 API 对本项目的工程推论，不是 Luma 内部调度架构事实。并行度应服从当前 Provider 限制；增加 Worker 数不能缩短外部队列等待，也可能增加重试和交叉覆盖。

## 公开专业提示词：应该借结构，不应该复制成长篇总咒语

### 已确认的差异

| 任务 / Provider | 官方方法 | 对本项目的含义 |
| --- | --- | --- |
| Veo 文生视频 | 电影摄影、主体、动作、环境、风格/氛围；音频单独说明对白、环境声、音效 | 分镜字段可以完整，实际请求只包含本镜必要内容 |
| Runway Gen-4.5 图生视频 | 重点写运动、时序、方向、速度；图像承载外观 | 不让编译器重复重写人物或把图文写出矛盾 |
| Runway Aleph 2.0 编辑 | 编辑动作 + 精确变化；仅有需要时补额外运动 | 修改请求携带具体差异，保留原本好的底片 |
| Luma 当前 API | 正向描述；没有 `negative_prompt` 参数；清楚比冗长重要 | 不应在通用 Skill 强制所有模型使用负面词段 |
| Veo 负面提示 | 使用不想要的元素列表，而非否定命令 | 负面提示应是 Provider 规则，不能和 Runway/Luma 混用 |

来源：[Veo 3.1 官方专业指南，2025-10-15](https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-veo-3-1/)、[Google 视频提示指南，2026-09-03 更新](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/video-gen-prompt-guide)、[Runway 图生视频](https://help.runwayml.com/hc/en-us/articles/48324313115155-Image-to-Video-Prompting-Guide)、[Aleph 2.0 编辑](https://help.runwayml.com/hc/en-us/articles/52150503729171-Aleph-2-0-Prompting-Guide)、[Luma FAQ](https://docs.agents.lumalabs.ai/guides/faq)。

少量官方原文体现其表达力度：Runway Gen-4.5 写的是 “Effective image to video prompts focus almost exclusively on motion.”；较早 Gen-4 对固定机位的例子是 “Locked camera. The camera remains still.”。这类提示将动作说清，远比“电影级、震撼、高质量、不要出错”更可执行。[当前指南](https://help.runwayml.com/hc/en-us/articles/48324313115155-Image-to-Video-Prompting-Guide)、[Gen-4 指南](https://help.runwayml.com/hc/en-us/articles/39789879462419-Gen-4-Video-Prompting-Guide)

### 本项目可改写的三个短模板

以下由本次调研编写，**不是厂商内部提示词，也未经 H3 真实验证**。意图是给现有技能和编译器明确职责，不要求新建三套 Agent。

**镜头制作方法：**

```text
根据当前镜头的叙事目的、明确对白、可用媒体与实际时长，确定最少必要的拍摄动作。
写可观察的主体动作、表情、机位和时序；人物、道具和空间关系保持与选中参考一致。
选择本镜确实需要的身份、构图、连续性或表演参考，并说明它们各自控制什么。
优先使用已有可用结果；内容不足才准备新的参考或生成输入。
若动作在当前时长内不合理，调整拍法或拆分本镜相关动作，保留用户要表达的内容。
输出保存到当前镜头和引用关系；任务无需用户确认时直接推进。
```

**图生视频编译示例：**

```text
输入首帧已确定两位人物、服装和办公室构图。
左侧人物把文件夹轻放在桌上，右侧人物先看文件，再抬眼看向左侧人物。
镜头缓慢向前推进；两人的站位保持稳定。动作在本片段内自然完成。
```

这段示例只说明运动表达方式。最终传给 H3 的参考编号、时段语法、对白和音频要求应遵守 H3 适配规则，不能把说明文字照搬进每个实际请求。

**局部修复方法：**

```text
输入：当前采用媒体与时间段、发现的具体问题、用户要求保留的内容、实际可用工具。
选择能完成修改且影响范围最小的操作：编辑时间线、修正音轨/字幕、局部媒体编辑或重新生成。
只修改相关对象和采用位置。使用源媒体与明确修改描述，避免重新设计已经合适的内容。
检查本次问题是否解决，以及与相邻镜头、声音、字幕是否仍然衔接。
保存可用结果；报告实际改动和剩余问题。不要以泛泛的“质量还可提高”反复重做。
```

## 对现有产品最有价值的投入顺序

1. **先改善当前 H3 路径的输入与返工选择。** 核查图片实际用途、镜头复杂度、分镜到生成提示的变化、台词实际时长，确保字幕/音轨/裁切类问题直接走编辑。这些方法不要求更换 Provider 或界面。
2. **把已采用媒体变成下一步的可用控制输入。** 复用视频帧、关联相邻镜头状态、保存具体选择，使 Agent 不再从聊天中重建连续性。
3. **补齐能够减少整镜重抽的操作。** 先用现有编辑、音频与字幕工具；再对真实失败集中出现的需求评估视频局部编辑、表演驱动、运镜参考等 Provider 能力。按问题接入，不按产品数量堆供应商。
4. **用小而完整的成片任务比较。** 同一脚本、同一参考、同一交付标准，记录可采用镜头比例、无效返工、人工打断、实际耗时与费用；分清媒体质量失败、编排 bug、工具输入错误、Provider 失败。先完成代表任务再决定是否扩大实验。

上述顺序是本次调研建议，尚未改变代码或实施路线。当前已有的 `@` 引用、局部剪辑和任务系统是否真正覆盖这些行为，需与源码和 Trace 审核结果合并判断。

## 资料时效与证据边界

下列资料均在 2026-09-11 打开。没有明确出版/更新时间的页面记为“未核得”；访问日期不能代替功能上线日期。

| 资料 | 页面日期 | 本文使用范围 |
| --- | --- | --- |
| [Flow 生成](https://support.google.com/flow/answer/16353334?hl=en) | 未核得 | Ingredients、首尾帧、参考输入建议 |
| [Flow 编辑](https://support.google.com/flow/answer/16935718?hl=en) | 未核得 | 选中片段、Agent 编辑、序列、保存帧；产品帮助机制 |
| [Runway References](https://help.runwayml.com/hc/en-us/articles/40042718905875-Creating-with-Gen-4-Image-References) | 未核得 | 素材输入与草图控制 |
| [Runway Gen-4.5](https://help.runwayml.com/hc/en-us/articles/46974685288467-Creating-with-Gen-4-5) | 未核得 | 当前模式区分；未把价格作为采购结论 |
| [Runway 图生视频指南](https://help.runwayml.com/hc/en-us/articles/48324313115155-Image-to-Video-Prompting-Guide) | 未核得 | Gen-4.5 提示方法与连续画面 |
| [Runway Gen-4 指南](https://help.runwayml.com/hc/en-us/articles/39789879462419-Gen-4-Video-Prompting-Guide) | 未核得；旧模型方法 | 对比提示原则；不作为最新规格 |
| [Act-Two](https://help.runwayml.com/hc/en-us/articles/42311337895827-Performance-Capture-with-Act-Two) | 未核得 | 表演输入、模式与限制 |
| [多人对白教程](https://help.runwayml.com/hc/en-us/articles/41748090660499-Creating-Multi-Character-Dialogues-with-Act-Two) | 未核得 | 人工录制、分角色驱动与合成方法 |
| [Aleph 2.0 提示指南](https://help.runwayml.com/hc/en-us/articles/52150503729171-Aleph-2-0-Prompting-Guide) | 未核得 | 局部编辑描述 |
| [Aleph 2.0 / Edit Studio 发布](https://runway.com/news/introducing-aleph-2-and-edit-studio) | 2026-05-21 | 先静帧预览再传播；效果属于厂商陈述 |
| [LTX Studio 官方教程](https://ltx.io/blog/ltx-studio-tutorial) | 未核得 | 图到视频、Elements、Storyboard；未检视云端内部实现 |
| [Luma 当前 Quickstart](https://docs.agents.lumalabs.ai/) / [FAQ](https://docs.agents.lumalabs.ai/guides/faq) | 未核得 | Ray 3.2 / 当前异步 API 与模式；不能推断多 Agent 架构 |
| [Luma 旧 Modify API](https://docs.lumalabs.ai/docs/modify-video) | 未核得；页面指向新文档 | 仅作迁移线索，旧 Ray 2 时长/参数不作为当前规格 |
| [Firefly 运镜参考](https://helpx.adobe.com/firefly/web/work-with-audio-and-video/work-with-video/match-camera-motion-to-reference-video.html) | 更新 2026-08-18 | 参考驱动与模式限制 |
| [Firefly 时间线声音生成](https://helpx.adobe.com/firefly/web/firefly-video-editor/generate-audio/generate-sound-effects.html) | 更新 2026-06-16 | beta 视频编辑器的音效时序与轨道操作 |
| [Veo 3.1 官方专业指南](https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-veo-3-1/) | 发布 2025-10-15 | 专业提示结构和组合流程，非今天的全部规格 |
| [Google 视频提示指南](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/video-gen-prompt-guide) | 更新 2026-09-03 | 动作、时间、声音和负面提示方法 |

闭源产品的后台状态组织、Agent 数量与通信协议、完整系统提示词、质量评分器、自动返工策略、真实端到端成功率在本次公开材料中均为 **UNKNOWN**。可以借用已公开的交互和输入输出方法，不能把未知内部实现写成已验证的架构。
