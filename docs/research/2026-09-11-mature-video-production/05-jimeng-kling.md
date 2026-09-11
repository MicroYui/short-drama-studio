# 即梦、Dreamina、Seedance 与 Kling：从成熟制作机制反查实现质量

调研日期：2026-09-11。本文补充 [市场制作方法](02-market-workflows.md)，重点追问：**已经有类似 Tool，是否仍因输入不完整、返回不好用、调用方法不对而反复失败？** 当前源码判断以 [实际调用与质量审查](07-implementation-quality-audit.md) 为依据。本文没有接入新 Provider、登录生成、支付或运行真实生成实验。

## 1. 先区分产品、模型和接口

| 对象 | 本次能够确认的范围 | 不能混用的结论 |
| --- | --- | --- |
| 中国即梦 `jimeng.jianying.com` | 官方首页介绍中文提示、首尾帧、运镜，以及局部重绘、扩图、擦除、图层等图像编辑能力 | 首页不能证明内部 Agent 架构、故障恢复实现或自动成片成功率；未检查登录后的当前界面。[即梦官网](https://jimeng.jianying.com/) |
| 海外 Dreamina / CapCut | 官方教程描述 AI Agent 对话、参考输入，以及 Octo 的场景内容、引用对象、生成入口和时间线 | 这是海外产品资料，不能直接写成中国即梦已经采用相同 Octo 界面；本文未以其画布作为本产品改版方向。[Dreamina Video Agent](https://dreamina.capcut.com/ai-video/ai-video-agent) |
| ByteDance Seedance 模型 | 2026-07-31 已发布 Seedance 2.5 公告，介绍长视频、参考和编辑；公告当时称 API 将开放 | 不能停留在 2.0 的四月模型卡，也不能把七月“即将开放”沿用到九月。现在火山已有具体 2.5 接口说明。[官方公告](https://seed.bytedance.com/en/blog/one-take-creation-flexible-referencing-introducing-seedance-2-5) |
| 火山方舟 Seedance API | 当前创建、查询与 2.5 教程正文可以核对参数、任务状态及限制 | API 与应用套餐、素材数量、分辨率支持可能不同；本项目当前真实主链仍为 H3，研究不等于已经接入。[2.5 教程](https://docs.volcengine.com/docs/82379/2607688?lang=zh) |
| Kling 应用 | 3.0 / Omni / Element 官方教程公开具体交互；五月文章说明四月已为 3.0 增加原生 4K | 二月教程的分辨率表不是九月全量能力表。检索未找到可核实的更高主版本公告，不把 3.0 称为绝对最新。[4K 更新](https://kling.ai/blog/kling-ai-introduces-native-4k-video-model) |
| Kling 开放平台 API | 官方文档地址存在，本次只能取得页面壳，未取得可核对的接口正文 | 应用支持某功能，不代表已核实其开放接口、参数和恢复语义；不以第三方接入商的字段替代官方 API。[开放平台入口](https://kling.ai/document-api/api/video/motion-control) |

这些产品可供借鉴的是明确的操作机制。官方样片和“更稳定、更高效”等宣传不足以证明本项目能降低多少返工，也不足以反推其 system prompt、Agent 通信、调度器或项目数据库。

## 2. 八项值得直接适配的机制

### 2.1 任务模式必须进入实际请求，不能只藏在自然语言里

Seedance 2.5 为全模态输入提供 `omni_reference_task_type=auto/reference/edit/extend`。明确指定子任务可提前校验约束；模型处理时仍会结合提示词判断，语义冲突仍可能异步失败。官方提示词指南也区分“用某视频的动作创作新视频”和“修改这段视频”，两者不是同一种生成。[创建 API](https://docs.volcengine.com/docs/82379/1520757?lang=zh)、[提示词指南](https://docs.volcengine.com/docs/82379/2222480?lang=zh)

**对现有实现的复查：** `route.compile_h3_prompt` 的输入是否保留用户要“重生成”“延续”“只改局部”的具体意图及必须保留的内容？如果当前真实 Provider 只能重新生成，应明确编译成受影响生成组的新候选；不能把 `video.retake/extend/gap_fill` 的 Mock 名称当成真实局部编辑。返回应给实际采用的模式、输入媒体用途、请求是否已创建和具体缺项。Agent 不应先等待数分钟，再靠通用错误重写整份 prompt。

若后续接入 Seedance，模式约束放进 Provider 适配层；其要求的编辑关键词只是请求语法，不是用正则决定用户目标的理由。无关模式不注入该套说明。合并当前准备与编译链，可比增加一个“模式判断 Agent”少一轮往返。

### 2.2 参考素材要按用途组织，不默认制作混合拼接的角色卡

Seedance 2.0 官方指南将人脸、姿态、服装等合并在同一张图及脸部占比过小列为身份漂移原因，建议脸部近照与全身服装图独立输入、分别指明用途；随后也提示不建议人物多视图，但没有精确定义多图上传与单图拼接的边界。Kling Element Library 支持同一 Element 的 2–4 张独立主图及补充图。**这些资料不能证明两家对多张独立参考存在直接矛盾，也不能说明 Kling 支持拼接角色卡。** 应区分单张拼接图、独立媒体，以及绑定为一个 Element 的参考组。[Seedance 指南](https://docs.volcengine.com/docs/82379/2222480?lang=zh)、[Kling Element Library](https://kling.ai/quickstart/klingai-element-library-3-user-guide)

**对现有实现的复查：** 先核查 `shot.compile_keyframe_brief`、`qa.rerank_reference_visuals`、`qa.check_input_compatibility` 和 H3 输入是否得到同一批实际文件、相同顺序、相同角色用途。专业审查已发现部分路径只能取得对象名或宽泛上下文；再加“保持一致”不会修好这个问题。输入应表达“脸部身份、服装、构图、首帧、动作”的具体职责；返回应指出哪张图在哪个时段解决哪项要求，以及冲突来自哪里。

已有清楚的人脸或服装图优先复用，不默认生成全套正侧背图；Provider 确需补图时才补。模型上限允许 30 张图片，不意味着每镜应加载 30 张。上下游共享同一媒体解析结果，避免 Agent 人工重复抄文件 ID。上述 Seedance 建议属于 2.0 已核验文档，升级 2.5 后需继续检查适用性，不能扩大成所有模型禁止多角度独立图片。

### 2.3 人物身份与声音绑定是一个制作约束，不是两段互不相关的描述

Kling Omni 可从 3–8 秒人物视频建立包含外观和原声的角色，或替换声音。Video 3.0 教程提示：绑定了 Element 声音后，不要在 prompt 再次指定音色。对话的说话人、台词与实际绑定应保持一致。[Omni 指南](https://kling.ai/quickstart/klingai-video-3-omni-model-user-guide)、[Video 3.0 指南](https://kling.ai/quickstart/klingai-video-3-model-user-guide)

**对现有实现的复查：** 人物对象、声音对象、镜头台词和生成输入已有相似结构，但需要看编译后究竟是否引用当前选中的声音，是否又从旧方法注入矛盾的音色描述。Tool 的可见输入应包含本次说话人和实际声音引用；返回应带实际生成的音轨、台词归属和待核对项，不能把人物视觉一致性检查当成对白检查。

这也不意味着先给所有人物额外录制声音。没有对白的镜头不做声音绑定；当前 Provider 不支持这种绑定时，只在其支持的表达内编译，不创建看似可用的控制参数。字幕问题继续修当前编辑时间线与音频对齐的写回，不能期待原生音频自动解决本地字幕的旧 Manifest 问题。

### 2.4 多镜生成应保留作者的镜头计划，也允许模型在合适范围内完成覆盖

Kling Video 3.0 将自动 Multi-Shot 与 Custom Multi-Shot 分开；后者逐镜指定内容和时长。Omni 示例进一步给出编号镜头、景别、角色引用和对应对白。Seedance 2.0 指南反而提醒精确秒点控制不稳定；2.5 新增时间戳编辑属于另一版本及任务能力。[Video 3.0 指南](https://kling.ai/quickstart/klingai-video-3-model-user-guide)、[Omni 指南](https://kling.ai/quickstart/klingai-video-3-omni-model-user-guide)、[Seedance 提示词指南](https://docs.volcengine.com/docs/82379/2222480?lang=zh)、[2.5 公告](https://seed.bytedance.com/en/blog/one-take-creation-flexible-referencing-introducing-seedance-2-5)

**对现有实现的复查：** 保留 H3 生成组及逐时段能力，检查编译是否把镜头顺序、主要动作、台词归属压成一段难以执行的长文本；以及检查工具是否用单镜设计去评判整组视频，忽略生成组真实边界。已有 `[0–3s]` 一类时段不能机械复制到所有 Provider。

内容确定时，提供可执行的逐镜内容；只确定目标时，可把有限镜头覆盖交给模型。决定分组时看人物数、动作接触、镜头切换与叙事完整性，不按统一秒数拆镜。工具返回实际视频区间、建议采用点及不确定边界，方便沿用当前单镜剪辑，而不是要求用户重新确认整场计划。

### 2.5 动作驱动与对白生成应有不同的输入条件

Kling Motion Control 让角色图片跟随动作参考视频；官方建议脸部清晰、朝向和风格相容，并以一个主要主体为对象。其 FAQ 对说话和口型同步明确导向标准 Video 3.0 生成，不能将 Motion Control 泛化成所有表演任务。[Motion Control](https://kling.ai/feature/ai-motion-control)

**对现有实现的复查：** 当复杂动作反复失败时，现有 Tool 是否只能再写一遍文字，还是能表示具体需要“动作参考”“表演参考”“重新设计覆盖镜头”？动作/人物/首帧不应都落到无用途的图片列表里。输入先表达创作问题，再由真实能力选择路线；如果 H3 不支持视频动作驱动，就保留导演改镜头的选择，后续独立评估专用 Provider，不能伪装已支持。

参考拍摄也有成本。轻微转头、简单推盘不自动要求用户录视频；多次失败且运动过程决定叙事时才考虑它。返回应说明使用了哪个动作区间、哪些主体得到控制；不能只返回一条“表演一致”评分。

### 2.6 局部生成修改要表达“改什么、哪段改、保留什么”

Seedance 2.5 公开了时间戳编辑、主体替换、增删对象及画面修复；其编辑模式要求参考视频，并保持输入视频的画幅和时长。即梦首页的局部重绘、擦除、扩图是图像编辑入口，不能据此推断同样的像素级视频局部能力。[2.5 教程](https://docs.volcengine.com/docs/82379/2607688?lang=zh)、[即梦官网](https://jimeng.jianying.com/)

**对现有实现的复查：** 定向修改不该只传原 prompt 加一句“修好餐盘”。需要当前采用片段、具体时间区间、问题证据、要保留的动作/声音/机位和本次改动。现有 `shot.director_advice` 应继承当前方法、阶段与生成组，避免固定 STORYBOARD 的另一次模型调用重复外层 Worker 判断。修复返回要能映射当前内容与采用区间；如果工具只产一份建议文档，下一名 Worker 仍需重新搬运信息。

先判断裁切、字幕、音量、图版或换采用区间能否完成修改；需要保持原视频运动的生成编辑，才需要真实支持它的 Provider。H3 重生成组是当前可执行路径，但应如实表达会改变的范围。一次局部成功不能作为全组视觉和音频已检查的证明。

### 2.7 任务完成必须落到本地可用媒体；等待不应占用语言决策

火山创建接口返回任务 ID，查询接口返回状态、错误、实际模型及输出。任务查询仅保留近 7 天记录，媒体 URL 有效 24 小时；Seedance 2.5 的视频 URL 还有下载次数限制。因此“供应商 succeeded”与“本地已经可以编辑”是两个要衔接的事实。[查询 API](https://docs.volcengine.com/docs/82379/1521309?lang=zh)

**对现有实现的复查：** 当前 H3 已有确定性轮询和提交不确定状态处理，不把它误列为缺失。应核查终态到下载、媒体入库、解析时长、更新当前镜头和可编辑状态是否完整，下载失败是否只重试下载，以及 Worker 等待期间是否释放调度槽位。结果应给稳定本地媒体引用、失败阶段、可恢复动作和实际用量；不要让 Leader 读整份 Provider 响应猜下一步。

Seedance 可返回真实尾帧，连续镜头需要时可直接使用；本项目相邻边界必须区别设计终态和已采用视频的真实终态。复用已有抽帧与观察结果，只有真的存在连续性依赖才读取或串接前片，不让全部生成任务退化为串行。尾帧保存在已有资产/引用结构中即可，无需新建强制状态表。

### 2.8 对话要知道当前操作对象，直接编辑要回到同一项目事实

Dreamina Octo 官方教程描述在场景块调用生成、添加人物/物体/环境/风格引用、将片段放入时间线；另一份 Agent 教程描述在聊天中提供目标及附件、查看后再指定修改。这能借用的是上下文明确的“对象 + 动作”，不能据此推断其 Agent 消息协议。[Video Agent 教程](https://dreamina.capcut.com/ai-video/ai-video-agent)、[Agent 教程](https://dreamina.capcut.com/resource/ai-agent)

**对现有实现的复查：** 三个内容入口、常驻 Agent、项目资产、镜头 @ 引用已经覆盖对应产品方向。应继续核对选中镜头/采用位置是否进入任务输入，保存后是否更新同一 ProjectDocument 和时间线，运行中的 Worker 是否保护用户已经生效的局部修改。不能因用户切换页面就改写已委派任务的对象。

专业上下文只带当前工作对象、已选媒体、相关边界与未解决事项；详细文件用精确引用回读。官方 Agent 教程的多候选生成上限不是默认生成策略；不为模仿“Agent 工作台”增加画布、更多角色、40 张候选或固定确认轮次。好的交互是 Agent 能解释这次实际改了哪里、影响哪些采用位置、当前剩余什么。

## 3. 可直接借鉴的专业 prompt 写法

以下为本项目重新撰写的示例，不是即梦或 Kling 的私有 system prompt，不直接提交。图片/视频编号由实际媒体绑定产生；其中时间控制、声音和编辑能力必须按选用 Provider 编译。参考来源是上述公开教程。

**参考生成示例：区分身份、服装与动作，不让素材互相竞争。**

> 男生的脸部身份取自图片 1，外套与发型取自图片 2。视频 1 只提供放下餐盘的动作节奏。固定中景：男生把餐盘推向窗口，松手后抬眼看阿姨；阿姨保持原站位，低头看盘。不要复用动作参考里的场景和其他人物。餐盘起始已经装有米饭。

这里的重点是各引用职责和实际起态；是否允许负向表达按 Provider 方法处理。若图片已完整决定服装与构图，应删除重复形容，不为满足模板继续加词。人物图和动作视频本身须可用，文本不能修复拿错文件。

**视频编辑示例：直接指明待编辑片，不写成“参考这段视频重新创作”。**

> 编辑视频 1：把 2.0–3.5 秒出现的空餐盘改为与图片 1 一致的盛饭餐盘。保留人物身份、手部动作、原有机位、切点、对白与总时长；其他时段沿用原内容。

该示例只适用于真实支持时间戳编辑的模式，不能直接当成 H3 已能实现的局部重绘。如果原画面遮挡和手盘关系使局部编辑难以保持自然，应把这个风险作为具体制作判断，选择换采用点或重生成受影响组。

**自定义多镜示例：镜头内容与说话人对应，已绑定声音不重复定义音色。**

> 镜头 1，固定双人中景，3 秒：男生推近餐盘，阿姨低头核对，不说话。镜头 2，阿姨近景，4 秒：她抬眼，稍停后说“这份给你多加一点。”镜头 3，男生反应近景，3 秒：他先愣一下，再轻轻点头。角色分别使用已绑定的男生和阿姨 Element；环境沿用窗口参考。

这是 Kling 自定义多镜形式的适配示例；Seedance 2.0 可保留顺序而不硬套秒点，H3 则按其真实逐时段合同编译。总时长还需容纳真实对白、停顿和可剪辑余量，不能用名义秒数证明口型或字幕已对齐。

## 4. 已核对的接口约束：作为能力资料，不作为新审批门

以下仅为 **火山 Seedance** 的已核实字段。Kling 应用教程不能替代其 API 合同；本次不提供未经核实的 Kling JSON。

| 接口或约束 | 已核实内容 | 应用侧意义 |
| --- | --- | --- |
| 创建/查询 | `POST /api/v3/contents/generations/tasks`；`GET /api/v3/contents/generations/tasks/{id}` | 先落任务 ID，再查询/回收；网络恢复不等于再次提交 |
| 媒体用途 | `content.type` 为 text/image_url/video_url/audio_url；图片用途有 first_frame/last_frame/reference_image，视频/音频有 reference_video/reference_audio | 统一媒体索引也要保留调用用途；首帧、首尾帧与全模态参考模式互斥 |
| 参数校验 | 结构化 body 参数强校验；文字末尾 `--params` 的弱校验可能忽略无效值 | 不让 Agent 靠拼 prompt 绕过能力适配 |
| 错误区别 | `InvalidParameter.TaskTypeConstraint`、`InvalidParameter.TaskTypeMismatch` | 返回具体参数约束或意图冲突，按原因修，不原样重试 |
| 输出关联 | `return_last_frame`；查询的 `content.video_url` / `content.last_frame_url` | 需要连续边界时复用真实结果；落到本地媒体引用 |
| 可选推送 | `callback_url` 通知任务状态变化 | 本地桌面可以继续确定性轮询，无需为复用机制暴露公网服务 |

以上字段来自 [创建 API，2026-09-09 更新](https://docs.volcengine.com/docs/82379/1520757?lang=zh) 与 [查询 API，同日更新](https://docs.volcengine.com/docs/82379/1521309?lang=zh)。Base URL 为 `https://ark.cn-beijing.volces.com`。这张表不是完整接口合同，接入时应读取实际所选模型版本的约束。

几个容易照抄错的限制，单独列明：

- **Seedance 2.5 编辑：** `reference_video` 为 4–30 秒，`ratio=adaptive`，`duration=-1`；首帧/首尾帧和延长也有 adaptive 约束。参考生成、编辑、延长分别处理。[2.5 教程](https://docs.volcengine.com/docs/82379/2607688?lang=zh)
- **素材数量属于不同合同：** Dreamina 的 Seedance 2.0 产品页写总计 12 份参考；火山当前 2.0 接口按 9 图/3 视频/3 音频配置，2.5 为 30/10/10。不可把一个页面的数字写成所有接入的全局上限，也不应默认用满。[Dreamina 产品页](https://dreamina.capcut.com/tools/seedance-2-0)、[2.5 能力表](https://docs.volcengine.com/docs/82379/2607688?lang=zh)
- **高版本不等于所有参数上限更高：** 当前火山表中 2.5 到 1080p，2.0 列有 4k；Kling 3.0 的后续 4K 更新又晚于二月基础教程。不能按版本号猜分辨率，也不能把产品宣传的 4K 当成所有 API 模式均支持。[火山能力表](https://docs.volcengine.com/docs/82379/2607688?lang=zh)、[Kling 4K 文章](https://kling.ai/blog/kling-ai-introduces-native-4k-video-model)
- **低成本预览不是通用模式：** 当前创建文档的 `draft` 明确限定 Seedance 1.5 pro，不能给 2.0/2.5 或 H3 设计必经的 draft-first 流程。即使将来某模式支持，也应只在高风险新动作上比较收益，不增加每镜两次生成。[创建 API](https://docs.volcengine.com/docs/82379/1520757?lang=zh)

## 5. 对本项目的优先顺序

**先修输入、返回和调用路径，再判断是否新增 Provider。** 现有质量审查已定位：部分参考工具与视频检查未共享实际生成组上下文，候选比较的证据范围不完整，导演建议丢失阶段/方法，字幕对齐未写回当前编辑，真实终态与设计终态未充分区分。这些问题会使新模型继续吃到错误或过量输入；它们不是“功能已存在所以无需改”。

第一步适合保留当前 H3，在同一条“当前生成组 → 具体媒体 → 编译 → 观察 → 局部修复”链上改好用途、顺序、范围、引用和可执行回执，并去掉本次无判断价值的模型搬运。第二步再用现有案例确认哪些缺陷必须依靠新的模型能力：真正保留原运动的视频编辑、动作驱动、跨片稳定音色等。只有这些场景才值得开展有限 Provider 对照。

比较结果至少同时记录实际采用率、重生成次数、语言轮次、素材准备时间、视频等待、局部修复后保留率与用户必要交互。公开教程能够给出操作方法，不能代替这组实际结果；本轮没有测得新增 Provider 的速度或质量收益。

## 6. 来源日期与核验边界

| 官方资料 | 日期/版本 | 本次核验 |
| --- | --- | --- |
| [即梦首页](https://jimeng.jianying.com/) | 无正文日期 | 普通公开 HTML 中的产品正文；未登录应用 |
| [Dreamina Video Agent / Octo](https://dreamina.capcut.com/ai-video/ai-video-agent) | 无可靠正文日期 | 已打开教程；交互描述是产品官方说法，未实操生成 |
| [Dreamina AI Agent](https://dreamina.capcut.com/resource/ai-agent) | 无可靠正文日期 | 已打开；未据泛化 Agent 介绍推断内部系统 |
| [Dreamina Seedance 2.0](https://dreamina.capcut.com/tools/seedance-2-0) | 当前读取页 | 海外产品页面，独立于火山 API 合同 |
| [Seedance 2.5 发布](https://seed.bytedance.com/en/blog/one-take-creation-flexible-referencing-introducing-seedance-2-5) | 2026-07-31 | 已打开；其中仍承认复杂运动物理、多主体交互局限 |
| [创建任务](https://docs.volcengine.com/docs/82379/1520757?lang=zh) / [查询任务](https://docs.volcengine.com/docs/82379/1521309?lang=zh) | 官方文档元数据均为 2026-09-09 更新 | 浏览工具正文为脚本壳；普通 HTTP 获取公开页面后读取内嵌公开文档 MDContent，未使用登录信息 |
| [Seedance 2.0 提示词指南](https://docs.volcengine.com/docs/82379/2222480?lang=zh) | 2026-09-07 更新 | 同上，已核对具体正文与反例 |
| [Seedance 2.5 教程](https://docs.volcengine.com/docs/82379/2607688?lang=zh) | 2026-09-11 更新 | 同上；能力表、任务模式及参数约束 |
| [Kling Video 3.0](https://kling.ai/quickstart/klingai-video-3-model-user-guide) / [Omni](https://kling.ai/quickstart/klingai-video-3-omni-model-user-guide) | 2026-02-06 | 已打开官方指南，部分旧 app 域名会跳转 kling.ai |
| [Kling Element Library](https://kling.ai/quickstart/klingai-element-library-3-user-guide) | 2026-02-05 | 已打开；图像/视频/绑定模式的数量不得混作统一限制 |
| [Kling Motion Control](https://kling.ai/feature/ai-motion-control) | 无可靠正文日期；页面称 3.0 | 已打开；单主体与对白适用性来自 FAQ |
| [Kling 4K 更新](https://kling.ai/blog/kling-ai-introduces-native-4k-video-model) | 2026-05-20，正文称 04-23 上线 | 浏览提取失败，普通公开 HTML 可读到文章；不使用“世界第一”等宣传作结论 |

本次未取得 Kling API 正文，也未看到上述闭源产品可确认的内部 Agent 通信、上下文压缩或恢复源码。没有证据支持“复制其多 Agent 架构就能获得同等结果”。可直接采用的是本报告逐项给出的输入语义、引用职责、交互范围和任务完成机制；收益需在当前产品中测量。
