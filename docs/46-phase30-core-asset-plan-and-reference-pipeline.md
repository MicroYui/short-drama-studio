# Phase 30：核心资产计划与多参考闭环

> 状态：调研结论与后端合同已落地，离线契约回归已补齐，待 AgentRun 真实语义规划验证。本阶段不接入 LTX 模型，不移植 ComfyUI 前端，不调用付费图像或视频 Provider。

## 1. 结论

当前后端的 `Asset` / `AssetVersion` / `CoreAssetBrief` / `ReferencePack` / `ReferencePlan` / Image Task 原子能力不需推倒重写。需要补齐的是三个成熟制片语义：

1. 在最终剧本与分镜之后，先产生一份版本化 `AssetPlan`，显式记录哪些对象要建锚点、哪些保留为文本、哪些复用已有媒体，以及每个生成工作项的依赖、最终 prompt、有序参考和下游用途。
2. 将“语义资产可以拥有多张有效参考”与“一次 Provider 调用只选最少足够的子集”分开。参考板是人工审阅或单图 Provider 的可选降级形态，不是多视图资产的唯一机器表达。
3. 让 Asset Worker 依据当前项目的 Method Skill 和完整分镜语义自主选择轻量图、短剧角色参考卡、六视图卡、场景 coverage、道具 master 或不建卡。这些形态由结构化 brief/plan 显式声明，不由题材关键词、正则或固定镜头数路由。

## 2. 成熟实现中直接采用的部分

### MiniMax Design 短剧 Workflow

本地保存的 MiniMax Design 3.0.11 `drama-series` Workflow 已经给出可直接迁移的资产方法：

- 只为跨镜复用、身份/空间/状态连续性确实需要的对象建锚点；一次性路人、群像或普通背景物可保留为 text-only。
- 短剧持续角色默认为一张 16:9 双区图：左侧 50% 大幅正脸特写，右侧 50% 为正面/侧面/背面全身三列；同一人、同一造型与比例，中灰 `#7c7c7c` 背景、柔和棚拍光、无场景、文字、标签和水印。
- 复用场景默认为一张 16:9 的 2×2 四向图：正面/反面/左侧/右侧，四格必须是同一空间的布局、家具、材质、光线、时刻和尺度，且是空场景。
- 关键道具先生成单物、单角度、中性背景的 master，再让持有/穿戴角色状态和破损/掉落状态引用它。
- 同脸换装/伤势/人格变体、道具生命周期和场景不可逆状态使用 base-first 依赖链；互不依赖的 base 可并行。
- Planner 定义稳定 Work Item ID、最终 prompt、有序 refs 和依赖；媒体执行工具只执行明确请求，不在 Tool 内重新发明创意。

这些是安装包中可观察 Workflow 配置，不是 Design Agent Team 的开源实现。当前项目可直接使用其 Markdown 方法，但编排仍落在已有 `AgentRun / ProductionManager / WorkOrder / ToolRegistry`。

### MiniMax-H3 公开方法

[H3 公开仓库](https://github.com/MiniMax-AI/MiniMax-H3) 和 [3D Animation Short Generator Skill](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/3d-animation-short-generator/SKILL.md) 验证了“项目简报 → 剧情 → 角色卡 → 场景卡 → 镜头表/分镜 → 单段视频 → 合成”的交接顺序。角色卡承担 identity lock，场景卡只包含环境和固定地标，视频段必须绑定实际批准的卡片，不使用名称或 `@` 别名冒充媒体。

H3 的 Ref2VA 语义还要求区分：用于人物、场景、服饰、道具一致性的媒体是 Subject reference；只有图片本身定义了起始帧、结束帧或镜头构图时才是 Picture/keyframe。角色卡不应自动被当成首帧。

### LTX、Runway、Veo、Kling 与 FLUX.2 的共同启发

- LTX Studio 的 Elements 是可复用语义主体，可保存多张角度、近景、全身、表情和换装参考；Shot 通过 Element 绑定它。
- [Runway Gen-4 References](https://help.runwayml.com/hc/en-us/articles/40042718905875-Creating-with-Gen-4-Image-References)、[Veo API](https://ai.google.dev/gemini-api/docs/veo) 与 [Kling Multi-image Reference](https://ir.kuaishou.com/news-releases/news-release-details/kuaishou-kling-ai-unveils-multi-image-reference-feature-further/) 都不要求“所有信息塞进一张四视图”；它们允许人物、衣服、道具、场景或风格由不同参考承担，但单次调用有模型容量限制。
- [FLUX.2 Prompt Guide](https://docs.bfl.ai/guides/prompting_guide_flux2) 要求说明每张图的职责，例如图 1 锁人物、图 2 锁服装、图 3 锁背景，而不是传递无语义 ID 列表。
- LTX 开源推理层的多关键帧是带时间位置的 `conditioning_media_paths + conditioning_start_frames`，说明时间型关键帧与身份/空间 Subject reference 应继续保持不同合同。

## 3. 本地实现映射

```text
ScriptVersion + ProjectBibleVersion + ProductionSpec + Storyboard
  → AssetPlan (Agent 决定资产深度、形态、复用、依赖与最终 prompt)
  → Asset / CoreAssetBrief / AssetVersion
  → ReferencePack (一个语义资产的多张可用媒体)
  → Shot ReferencePlan (每镜所需职责与 Provider 预算)
  → H3 Subject/Picture 最少充分输入包
```

- `ProjectDocument + versioned Artifacts` 仍是唯一内容真相。
- `asset.plan.current` 是稳定逻辑输出指针，其内容在版本化 `ProductionDocument` 中。
- Asset Worker 产生计划与创意 prompt；通用 Image Tool 执行生成/编辑/拼板；实际媒体检查后再记录批准与 ReferencePack 职责。
- 每个 Image reference 显式携带 identity、wardrobe、pose、location、prop、style 或 composition 等职责；Provider adapter 可投影为自己支持的有序图片列表与 prompt 标签。
- 分镜局部修改后，Leader 根据真实语义影响修订 `AssetPlan`；不因计划内的无关项目而重做全部资产。

## 4. 本阶段验收

离线 Fake Provider 闭环应证明：

1. Agent 从精确 Script/Bible/ProductionSpec/Storyboard 自主建立版本化 AssetPlan，包含明确的 anchor / text-only / reuse 决定。
2. 重要高中生角色能选择短剧双区 16:9 角色卡；食堂能选择同一空间 2×2 coverage；普通菜盆、群像和一次性物件可不建重资产。这些只是当前剧情下的语义决定，不是全局规则。
3. base-first 依赖可恢复；互不依赖的角色/场景 base 可并行，状态变体不先于 base 执行。
4. 一个角色可同时保留正脸、全身、侧面或换装等多张已检查参考；更换 canonical master 不会无条件清空全部有效补充视图。
5. 短剧角色卡和场景卡的结构、画幅和无标签设定有确定性测试；拼板不是必经路径。
6. 每个 Shot 只选择当前镜头需要的参考，角色/场景/道具 Subject 与首尾帧 Picture 不混淆，不超过当前 Provider 能力。
7. 最终 trace 能显示方法 Skill、AssetPlan 版本、WorkOrder 依赖、Tool 调用、AssetVersion、审看决定与镜头绑定；不增加 Critic 循环、固定重试次数、审批门或关键词路由。

## 5. 后续 ComfyUI 衔接边界

开源 ComfyUI 可以后续承担确定性拆图、裁切、放大、去背景和高级生成图。[ComfyUI 官方文档](https://docs.comfy.org/custom-nodes/overview) 区分用于编辑的 workflow JSON 和用于 API 执行的 prompt graph。未来衔接使用稳定模板输入名，不让 Agent 直接操作节点坐标：

```text
AssetPlanWorkItem + AssetVersion
  → typed workflow binding manifest
  → ComfyUI API-format template
  → prompt_id / history
  → output AssetVersion
```

MiniMax Design ComfyUI plugin 中的拆图/裁切 blueprint 可在处理 GPL-3.0 许可边界后迁移；它不包含 Design 的 Asset Planner 或 Agent Team 源码。LTX 开源生成 Workflow 本阶段不接入，LTX Desktop 仍只作为后续编辑器与时间线底座。
