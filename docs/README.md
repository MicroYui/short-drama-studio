# 调研总览

## 调研问题

目标是探索一套能够接收“一句话”或“一段剧本”，生成完整、衔接、流畅、质量上乘的抖音短剧的自建工作流。当前已知可用的候选资源包括：

- 推理与编排：GPT-5.6-sol；
- 图像：GPT Image 2；
- 视频：MiniMax-H3；
- 音频备选：Azure TTS/STT；
- 允许研究闭源产品的方法，也允许基于成熟开源项目改造。

这份清单不是为了“凑齐模型”，但也不追求最少调用。**质量是约束，资源是变量**：某项能力只要提高质量，或质量不劣且增强一致性、控制力或稳定性，就可以使用。GPT Image 2 不必等直接视频失败后才加入；若图像锚点对下游无负面影响，就可作为角色、场景和关键帧的默认候选。Azure 同理，是否加入取决于最终音画质量而不是预算。即梦、可灵、LTX Studio 和纳逗 Pro 仍只用于学习方法，最终系统不调用、不依赖这些闭源产品。

首版运行在单用户、受信环境中，不设计注册、登录、账号体系、RBAC、多租户、团队成员或分享权限。生产流程中的“批准/确认”是内容状态，不是访问控制。外部模型 API 凭证只作为 Provider 配置处理。

当前已经完成调研、实验设计、首轮架构决策、LTX Desktop Phase 0 基线、Phase 1 Mock 垂直切片、Phase 2 MiniMax-H3 768P Provider、Phase 3 Azure GPT Image 2 资产工作台，以及 Phase 4 H3 Direct、Frame、Reference 与通用 Director 修复实验。S08 最终拆为两个覆盖镜头并由用户按整体可用标准通过，旧异常 Take 已退出正式时间线。Phase 5 已实现四种输入归一化、`AutonomousRun`、一键 Mock 草稿和长镜头/镜间观察工具。Phase 6 已实现证据优先的 H3 制片路线 Agent，不新增 H3 请求地复用 9 条已通过真实 Take，自动完成 9 镜观察、57.13 秒全 H3 TimelineManifest 和 1080×1920 技术导出。Phase 7 已把资产准备、H3 异步候选、视频观察、通用 QA、Director 有界修复、Take 选择和导出串为可恢复的真人 `AUTO_DRAFT`。Phase 8 已在真实 GPT Image 2、GPT-5.6-sol 和 H3 768P 下完成第一次 9 镜、54.31 秒无人值守单集；技术 QA 全部通过。E007 仍缺整片人工观看、另外两次独立运行和三人盲评。

工程实施状态见 [Phase 0：LTX Desktop 上游基线](09-phase0-upstream-baseline.md)、[Phase 1：供应商中立 Mock 垂直切片](10-phase1-mock-vertical-slice.md)、[Phase 2：MiniMax-H3 768P Provider 与真实证据门](11-phase2-minimax-h3-provider.md)、[Phase 3：资产工作台与 GPT Image 2](12-phase3-asset-workbench.md)、[Phase 4：H3 视觉路线质量实验](13-phase4-h3-visual-route-experiment.md)、[成熟 AI 短剧制作法与参考资产包](14-mature-production-workflow-and-reference-packs.md)、[Phase 5：无人值守运行器与视频观察工具](15-phase5-autonomous-runner-and-video-observation.md)、[Phase 6：真实 H3 制片路线与整集复用闭环](16-phase6-real-route-planner.md)、[Phase 7：从零真人 AUTO_DRAFT](17-phase7-real-autonomous-draft.md) 和 [Phase 8：首次真实无人值守 AUTO_DRAFT](18-phase8-real-autonomous-validation.md)。

## 证据等级

文档对结论使用以下标签，避免把宣传、源码和实际效果混为一谈：

| 标签 | 含义 |
| --- | --- |
| `DOC-CONFIRMED` | 官方产品/API 文档明确写明 |
| `SOURCE-CONFIRMED` | 在固定 commit 的开源代码中确认 |
| `PRODUCT-CLAIM` | 厂商官方宣传或产品说明，尚未独立实测 |
| `INFERRED` | 根据交互或架构做出的合理推断，不代表内部实现 |
| `UNKNOWN` | 资料不足或效果必须通过固定输入实验验证 |

“平台支持口型”“模型支持参考音频”只说明存在入口，不自动证明中文逐字正确、声线跨镜一致或时间同步达到成片标准。

## 核心发现

### 1. 质量来自中间工件，而不是一次生成

即梦、可灵、LTX Studio、纳逗 Pro 的公开能力，以及 Toonflow、ViMax、Jellyfish、Wind Comic、LumenX、LTX Desktop 的源码，都指向同一模式：角色、场景、道具、风格、镜头和时间线必须变成可编辑、可复用、可复核的项目资产。生成模型只是制片系统中的一个执行器。

### 2. 连贯性至少有五层

1. **身份连续性**：脸、发型、身材、服装、声线；
2. **场景连续性**：空间结构、光线、色彩、时间、背景物；
3. **动作连续性**：人物位置、朝向、眼线、手中道具、动作起止；
4. **镜头连续性**：景别、机位、轴线、焦点、运镜、剪辑节奏；
5. **叙事连续性**：信息增量、因果、情绪曲线、对白与反应镜头。

只做角色参考图最多解决第一层的一部分。

### 3. H3 是当前唯一视频模型，路线仍需实测

MiniMax 官方资料显示 H3 是原生视听模型，支持 4–15 秒、768P/2K、9:16，以及图片/视频/音频参考。官方 Prompt Guide 还提供稳定说话人编号、原语言对白和多镜头时间描述。当前视频生成只接 H3，不讨论其他视频模型候选；但 H3 内部仍要实测 Direct、Frame、Reference、原生音频和外部音频路线，不能把“唯一可用”误写成“所有能力已验证”。原生音频应先作为基线，不默认叠加外部 TTS。

但以下能力仍为 `UNKNOWN`：

- 中文对白能否逐字、逐标点稳定复现；
- 同一角色能否跨多个独立任务保持声线；
- 多人对话是否稳定分配给正确角色；
- 参考音频究竟是直接复用、声线参考，还是两者都可稳定控制；
- 口型在正脸、侧脸、快速台词和多说话人场景下的质量；
- 原生音乐、环境音和对白是否能够后期分轨或干净重混。

因此 Azure 不是默认必经链路，而只是需要精确语音、时间戳、字幕或 QA 时的备选。

### 4. H3 路线有一个会影响连续性设计的模式限制

H3 的首帧/尾帧角色与 reference image/video/audio 角色不能在同一请求中混用。如果采用 H3，工作流不能简单地把“上一镜尾帧 + 角色参考图 + 声线参考”全部塞进一次请求，必须按镜头目标路由：

- 动作接续优先：走首/尾帧模式；
- 身份、场景、声线复用优先：走 reference 模式；
- 或在实验证明确有增益时，用 GPT Image 2 或已有素材把角色、服装、构图与上一镜状态固化进关键帧，再走首帧模式。

这也是必须保留 `ReferencePlan` 而不能只存一段 prompt 的原因。

### 5. 底座与实施路线已经确定

开源审查显示各方向仍有不同缺口：

- Jellyfish：控制面和任务模型最好，但声音、字幕和最终合成仍明显不完整；
- LTX Desktop：桌面媒体壳、Take/Retake/Gap Fill、专业时间线和真实 FFmpeg 导出最好，但短剧领域模型缺失，项目元数据在 `localStorage`，生成状态主要在内存，Provider contract 强绑定 LTX；
- Wind Comic：成片模块最丰富，但系统面太宽，当前 GPT Image 与 MiniMax 适配器不能代表现有候选能力；
- LumenX：产品形态和生成工作台成熟，但 Provider contract 偏现有模型，部分导出参数仍是占位或未应用。

首版已经确定采用“LTX Desktop 壳 + 自建短剧/任务核心”：保留桌面媒体壳、Take/Retake/Gap Fill、时间线与 FFmpeg 能力，用 SQLite/API 和持久化 Worker 接管项目与任务状态。Jellyfish 只参考领域与任务语义，并在 LTX Desktop 出现已定义的结构性失败时回退；LumenX 不进入底座实现。真实实验只比较 H3 内部 Direct、Frame、Reference、镜头分组和声音路线，以及 GPT Image 辅助是否带来增益，不测试 LTX 模型。闭源平台只贡献设计规律，不进入底座或运行时。

## 研究限制

- 闭源平台结论主要来自官方页面与官方指南；按项目边界不登录、不调用，也不把其效果和价格纳入生产决策。
- 开源结论基于 [固定 commit](sources.md)，没有安装全部依赖或运行其真实付费 Provider。
- 当前已有固定资产下的多路线、多样本和人工审片证据，但仍不足以把某条路线提前写成所有镜头的稳定默认；S08 两镜覆盖方案已经通过，原单镜失败沉淀为能力画像、通用 QA、Director 修复策略和回归案例。
- 视频观察工具已在真实单集生成长镜头时间序列和相邻镜头边界证据，并发现整片总览中的身份漂移、画外道具误入和边界状态问题；跨项目召回率、漏报率和默认阈值仍是 `UNKNOWN`。
- 代表样本中的普通话声音与口型总体可用，但跨镜声线稳定、严格逐字正确率和完整单集音画一致性仍需最终八镜成片验证。
- 成本与配额高度时效化；本轮保留 `UNKNOWN` 并在实施时记录，但它们不是质量路线的主要决策因子。
