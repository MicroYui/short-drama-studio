# 可用能力池与供应商基线

快照日期：2026-08-14。价格、配额、模型别名和接口参数都可能变化，实施前必须再次核验。

本章只说明“这些资源能做什么”，不要求为了完整性全部接入。启用标准是：固定样片质量更好，或质量不劣且一致性、可控性、稳定性或返工能力更强。新增复杂度与成本需要记录，但不因其存在就否决质量更高的路线。

## 1. GPT-5.6-sol：可选的规划与编排模型

`DOC-CONFIRMED`：GPT-5.6-sol 支持文本输入/输出、图片输入、函数调用和 Structured Outputs，不直接生成音频或视频。官方页面给出的上下文窗口为 1,050,000 tokens，适合承载完整剧本、项目圣经、分镜、历史候选和 QA 反馈，但不应把所有内容无差别塞进一次请求。[OpenAI 模型页](https://developers.openai.com/api/docs/models/gpt-5.6-sol)

用于自建控制面时的建议职责：

- 一句话扩写和剧本诊断；
- 结构化角色、场景、道具、服装、声线与风格圣经；
- 分镜、镜头覆盖、轴线、视线和动作连续性规划；
- 为入选的图像、视频和音频模型编译各自的提示词与参考绑定；
- 检查台词是否被擅自改写；
- 读取候选帧或 QA 结果，给出具体重试变量；
- 通过工具调用编排任务，但不直接充当任务数据库。

建议使用 JSON Schema 约束中间工件。模型负责语义判断，数据库和状态机负责事实、版本与任务终态。

## 2. GPT Image 2：可选的静态资产与关键帧控制

`DOC-CONFIRMED`：GPT Image 2 支持图像输入和输出，可通过 `v1/images/generations` 生成、通过 `v1/images/edits` 编辑；固定快照包含 `gpt-image-2-2026-04-21`。它不支持音频或视频，也不提供 Structured Outputs。[模型页](https://developers.openai.com/api/docs/models/gpt-image-2)

官方指南区分两种使用方式：[Image API](https://developers.openai.com/api/docs/guides/image-generation) 更适合一次生成/编辑，Responses API 更适合多轮迭代编辑和保留图像上下文。两者都可控制质量、尺寸、格式和压缩；Image API 的 `n` 可一次返回多候选。

GPT Image 2 可从首轮就作为质量增强候选，生成以下产物：

1. 角色定妆与多视角参考；
2. 服装变体；
3. 场景主视图、反打方向和关键空间锚点；
4. 重要道具的外观与状态变体；
5. 每个镜头的首帧、必要时的尾帧；
6. 出现身份漂移时的局部修复或重绘候选。

不要在图中生成最终字幕。字幕应在后期以真实字体渲染。

如果这些资产让下游视频更好，或画面质量持平但跨镜一致性与可控性更稳，就默认使用；既可覆盖全部适合的镜头，也可只覆盖高风险镜头。只有已有素材明显更好，或图像锚点导致表演受限、风格转换损失、角色误绑定、动态质量下降时才跳过。

### 待验证项

- 多个角色同时进入一张关键帧时的身份稳定性；
- 同一角色跨景别、角度、光照和表情的可重复性；
- 参考图数量增加后，模型是否开始混淆角色/服装属性；
- 竖屏构图中的全身、双人和过肩镜头是否稳定；
- 直接 API 与 Responses 多轮编辑，哪种更省候选和人工时间。

## 3. MiniMax-H3：当前唯一视频生成引擎

### 3.1 已确认能力

`DOC-CONFIRMED`：MiniMax 的 [Video Generation Guide](https://platform.minimax.io/docs/guides/video-generation) 将 H3 描述为同时理解文本、图片、视频和音频的原生视听模型：

- 模型名：`MiniMax-H3`；
- 输出分辨率：768P 或 2K；
- 时长：4–15 秒整数；
- 支持 9:16；
- 支持 T2V、首帧、尾帧、首尾帧和 reference-to-video；
- reference 模式最多 9 张图片、3 段视频、3 段音频；视频与音频各自合计不超过 15 秒；
- 实际视频任务通过 `https://api.minimax.io/v2/video_generation` 异步提交并返回 `task_id`。

内容项可使用 `text`、`image_url`、`video_url` 和 `audio_url`，媒体角色包括 `first_frame`、`last_frame`、`reference_image`、`reference_video` 和 `reference_audio`。

### 3.2 关键限制

`DOC-CONFIRMED`：首/尾帧角色不能和 reference 媒体角色在同一个请求中混用。这会形成两条镜头路线：

| 路线 | 可用输入 | 强项 | 代价 |
| --- | --- | --- | --- |
| Frame route | 文本 + 首帧/尾帧 | 动作、构图和剪辑点衔接 | 不能同时直接传角色/声线 reference |
| Reference route | 文本 + reference 图片/视频/音频 | 身份、场景、动作或声线复用 | 不能同时钉死首/尾帧 |

工作流必须显式记录每镜采用哪条路线及理由。

### 3.3 Context-IR 不是视频生成端点

`DOC-CONFIRMED`：`POST /v2/h3_context_ir` 接收同类多模态上下文并返回语义增强后的结构化 prompt，**它本身不生成视频**。[H3 Context-IR API](https://platform.minimax.io/docs/api-reference/video-generation-v2-h3-context-ir)

推荐流程是：

1. GPT-5.6-sol 生成内部 `ShotPlan` 与 `ReferencePlan`；
2. 可选调用 Context-IR 得到 H3 官方格式的增强 prompt；
3. 比较自编译 prompt 与 Context-IR prompt，保留用户台词和约束；
4. 再调用 `/v2/video_generation` 生成视频。

不能把 Context-IR 返回成功误记为“视频生成成功”。

### 3.4 H3 Prompt Guide 可复用的结构

MiniMax 官方 [Base Prompt Guide](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md) 和 [Reference Prompt Guide](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md) 值得直接编译：

- `[Shot N] At MM:SS.mmm` 描述多镜头时间；
- `(S1)`、`(S2)` 作为稳定说话人 ID；
- `<d>[Chinese] ...</d>` 承载原语言对白；
- 对首帧、尾帧、主体、图片、视频和音频做明确引用；
- 将视觉动作、对白、环境音和非画内音乐分区；
- 在 reference 模式记录 `fully_preserved`、`partially_preserved`、`attribute_transfer` 或 `weak_reference`。

这些是提示结构，不是质量保证。官方指南说用户措辞应被保留，仍必须用生成结果做逐字验证。

### 3.5 H3 内部的音频路线优先级

当前固定采用 H3 生成视频，先把原生音频作为质量基线，再与 reference audio 和外部主音频路线直接比较：

1. **H3 原生对白/音效**：最少拼装，理论上口型、表情、动作与声音共同生成；
2. **H3 reference audio**：如果能稳定复用声线或直接复用音频，则用于跨镜角色声线；
3. **外部 TTS → H3 reference audio**：仅在严格台词或专名发音不达标时尝试；
4. **外部 TTS + 后期口型修复**：最后的兜底，而不是默认主链路；
5. **纯画外音/反应镜头**：对口型风险很高的镜头可在叙事允许时使用，但不能用来掩盖所有对白。

### 3.6 仍为 `UNKNOWN` 的高价值问题

- 原生中文对白的逐字正确率和断句；
- 同角色跨任务声线一致性；
- reference audio 能否只迁移声线而说新台词；
- 多说话人分配、插话与正反打；
- 参考视频的动作、剪辑节奏和声音分别能控制到什么程度；
- 768P 的实际质量与稳定率；1080P/2K 不进入当前 PoC；
- H3 当前官方价格、失败是否计费、取消和退款语义；
- 同一输入重复三次的稳定率。

价格页面当前没有清楚列出 H3，因此不写死单价。执行时记录账户/接口价格快照用于运营核算，但不以单价替代质量判断。

## 4. Azure Speech：可选精确音频与 QA

Azure 不再是预设的音频生成器，而是以下场景的可选能力。

### TTS

`DOC-CONFIRMED`：[Azure TTS](https://learn.microsoft.com/en-us/azure/ai-services/Speech-Service/text-to-speech) 支持 SSML，可控制发音、停顿、语速、音高、音量、语言、说话风格和角色；具体风格取决于所选 voice。[SSML 发音控制](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup-pronunciation) 可处理专名与多音字。

TTS 可提供 word-boundary 事件；部分 voice 还可返回 viseme ID 与时间偏移。[Viseme 文档](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-speech-synthesis-viseme) 对 2D/3D 角色口型更直接，对真人视频仍需视频模型或口型修复模型。

### STT

`DOC-CONFIRMED`：[Batch Transcription](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/batch-transcription-create) 可输出词级时间戳，支持说话人区分等属性。它适合：

- 检查 H3 原生对白是否漏字、错字或串角色；
- 从通过验收的原生音频估算字幕时间；
- 为人工审片标出高风险词句。

### 启用条件

出现以下任一需求时应评估 Azure 主动生成声音；若最终音画质量不劣且控制更强，即可采用：

- H3 对专名、数字、否定词或法律/产品台词不够准确；
- 角色必须跨集保持稳定声线；
- 需要精确控制时长、停顿和情绪；
- H3 能稳定接受外部主音频；
- 后期字幕或对口型修复需要可靠的时间事件。

## 5. LTX Desktop 内置 LTX-2.x：当前只用应用代码

`SOURCE-CONFIRMED`：LTX Desktop `v1.1.0` 的本地与 API contract 围绕 LTX-2.3 fast/pro、`ltx-2.3-22b-distilled`、单张图片和单段音频构建。它可以做文本/图像/音频到视频、Retake 和 Extend，但不是供应商中立执行器。应用可作为我们的桌面壳，不意味着 LTX 模型自动成为媒体主链。

`DOC-CONFIRMED`：当前 [LTX-2 官方仓库](https://github.com/Lightricks/LTX-2) 已推荐 LTX-2.5，并列出文本/图像到视听视频、关键帧插值、音频驱动、局部 Retake、变换和 dubbing 等 pipeline；LTX-2.3 仍受支持，但 2.3 与 2.5 的 checkpoint/LoRA 不互换。这些是模型仓库能力，不能反推 LTX Desktop `v1.1.0` UI 已经暴露全部能力。

LTX-2.x 不进入当前模型范围，也不执行质量对比。未来若明确重新开放视频模型扩展，才重新检查：

1. 最终产品形态对模型权重 License 可接受；LTX Desktop 应用代码是 Apache-2.0，但 [LTX-2.3 权重另有 Community License](https://huggingface.co/Lightricks/LTX-2.3/blob/main/LICENSE)；
2. 目标部署能承受本地模型资源。Desktop README 对 CUDA 路线要求至少 16 GB VRAM，Windows 本地模式还建议至少 160 GB 可用磁盘；Apple Silicon 路线启动时要求约 15 GB 可用内存；
3. 在与届时基线的同镜头实验中，视听质量、中文台词、身份/场景连续性、Retake 效果和稳定率达到入选线。

当前实现删除或禁用 LTX 模型生成路径，保留 Electron/编辑器/媒体层，只接入 H3 视频 Adapter；GPT Image 和音频能力按各自实验接入。供应商中立 contract 是未来扩展边界，不是当前建设多视频模型的要求。

## 6. 执行器适配层需要表达的能力

不要用一个只有 `prompt + image` 的通用接口掩盖差异。最小能力模型应包括：

- `input_modes`：T2V / I2V / FLF / reference；
- `media_roles`：first/last/reference image/video/audio；
- `incompatible_roles`；
- `duration_range`、`ratios`、`resolutions`；
- `native_audio`、`multi_speaker`、`dialogue_language`；
- `task_type`、`poll/callback/cancel`；
- `price_snapshot` 与 `billing_status`；
- `provider_task_id`、`request_fingerprint`、`model_snapshot`；
- `output_metadata`：实际时长、尺寸、fps、音轨信息。

另外要记录 `execution_kind = direct_model | image_guided | audio_driven | hybrid_repair`、是否能单镜返工和可导出的工件。这样规划器才能基于质量证据和能力路由，而不是为了使用某个资源生成一条供应商无法执行的链路。
