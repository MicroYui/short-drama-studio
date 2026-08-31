# Phase 26：成熟平台、前沿论文、开源实现与 H3 低重试策略

## 结论

Phase 24–25 只完成了第一轮方向性调研和 P0 落地，不能算对成熟平台、前沿论文、开源工程和低重试机制的完整研究。本阶段补齐后的结论是：

1. 成熟产品的共同优势不是某一条神奇 Prompt，而是把**资产、镜头意图、参考媒体、关键帧、候选 Take、局部修复和时间线**都做成可复用工件；
2. 降低 H3 重试的核心不是固定多生成几版，也不是设置更严的总分门，而是先选择**最小充分输入路线**，生成后定位失败发生在哪个时间段、属于哪一类，再改变正确的控制变量；
3. 角色三视图、场景九宫格、机位预演和视频参考都应由 Agent 按当前镜头需要检索或建议，不能成为所有项目的固定步骤；
4. 当前 Harness 已具备项目隔离、资产版本、H3 四类路线、官方 Prompt IR、顺序候选、抽帧观察、Director 修复、Take 比较和局部影响计划，底座方向正确；主要缺口集中在**参考图视觉重排、关键帧相对比较、问题到修复方式的类型化规划、跨镜状态轨迹检查、可检索的 H3 经验 Playbook**；
5. 论文中大量提升来自模型内部训练、latent 搜索或专用视频编辑模型，不能直接移植到 AceData 上的黑盒 H3。可迁移的是接口层的规划、检查、选片、经验学习和局部重算机制；
6. 真实重试降低比例仍为 `UNKNOWN`。Phase 25 的 Fake Provider 在“首个候选全部通过”时把固定 40 次请求降为 20 次，只证明顺序停止语义正确，不证明真实 H3 的平均节省比例。

本阶段没有调用 MiniMax-H3、GPT Image 2 或其他付费媒体 Provider。

## 证据口径

| 标签 | 含义 |
| --- | --- |
| `CURRENT-IMPLEMENTED` | 当前仓库代码已经实现并有离线测试覆盖 |
| `DOC-CONFIRMED` | 模型或 API 官方文档明确写明 |
| `SOURCE-CONFIRMED` | 在固定 commit 的开源代码中确认 |
| `PRODUCT-CLAIM` | 闭源厂商公开产品说明，未独立验证其内部实现和实际效果 |
| `PAPER-REPORTED` | 论文作者报告的方法或结果，尚未在当前 H3 上复现 |
| `INFERRED` | 根据现有证据做出的产品或架构推断 |
| `UNKNOWN` | 必须通过当前 Provider、固定镜头和人工验收才能确认 |

“完整调研”在这里指足以决定当前黑盒 H3 Harness 下一步设计，不代表穷尽所有视频论文或逆向闭源平台内部算法。

## 成熟产品实际在做什么

### 共同产品机制

公开资料虽然不能证明闭源平台内部算法，但六类产品机制高度一致：

1. **先建立可复用主体**：角色、服装、场景、道具、声音和风格不只存在于 Prompt，而是可以命名、保存和跨镜调用；
2. **把静态状态和动态过程分开**：图片或关键帧负责身份、空间、构图和动作状态，运动 Prompt 或参考视频负责动作过程与摄影机路径；
3. **按模型能力选择输入模式**：纯文本、首帧、首尾帧、Element/Ingredient、音频参考、视频参考和局部编辑不是同时堆满，而是按任务切换；
4. **先预览再投入高成本生成**：Storyboard、Multiframes、Previs、Sketch、Camera Preset 等先明确镜头关系和终态；
5. **保留替代 Take 和局部修改**：失败后重做当前镜头或当前时间段，不重做整集；
6. **在时间线上完成最终判断**：单条视频好看不等于能剪进成片，平台把镜头顺序、声音、转场和相邻状态放在同一编辑环境里检查。

### 产品证据矩阵

| 产品 | 公开机制 | 对当前 Harness 的意义 | 不应推断的内容 |
| --- | --- | --- | --- |
| [纳逗 Pro 工作台](https://nadoupro.iqiyi.com/docs/workspace-guide) / [AI 导演手册](https://nadoupro.iqiyi.com/docs/ai-director-guide) | 剧本提取角色、场景、道具；角色以“脸、身、用”组织，可生成正脸、全身/三视图和表情资产；场景可建立多角度资产；分镜卡包含景别、运镜、角度、时长、台词；另有导演、美术、长镜头、剪辑和素材检索 Agent。`PRODUCT-CLAIM` | 资产包需要覆盖身份、全身妆造、场景布局和镜头局部，而不是只存一张人物图；这些资产应由 Agent 按镜头检索 | 不能据此断言三视图对 H3 必然优于正脸加全身图，也不能复制其内部 Agent Prompt |
| [可灵 VIDEO 3.0](https://kling.ai/quickstart/klingai-video-3-model-user-guide) / [Element Library](https://kling.ai/quickstart/klingai-element-library-3-user-guide) | 可从角色视频或 2–4 张图片建立 Element，并可绑定声音；支持 Multi-Shot、Element Reference、起始帧和最长 15 秒生成。`PRODUCT-CLAIM` | “主体”应是可复用的多模态实体，参考数量根据视角和任务选择；声音输入与角色资产可以关联但不必强制 QA | 可灵的 Element embedding、声音绑定和 Multi-Shot 内部实现不可获得，不能假设 H3 有同等控制力 |
| [LTX Studio](https://website.ltx.studio/) / [Retake](https://ltx.io/blog/retake-ai-directing-tool-ltx-studio) / [Flows](https://ltx.studio/blog/ltx-studio-flows) | Dynamic Storyboard、Elements、Camera/Keyframes、Timeline、alternate takes、Retake；Flow 可复用节点结果并只重跑改动部分。`PRODUCT-CLAIM` | 关键资产、镜头、Take 和局部重算是持久化工件；Change Planner 应只重算受影响分支 | 闭源 Studio 的模型路由和商业云缓存策略不是 LTX Desktop 开源代码事实 |
| [Google Flow](https://support.google.com/flow/answer/16353334?hl=en) | Named characters、Ingredients、首尾帧、参考媒体、视频编辑和扩展；官方建议参考图干净、风格一致且 Prompt 不与参考冲突。`DOC-CONFIRMED` | 输入质量检查应寻找参考间冲突和冗余；Agent 需要说明每份参考负责什么，而不是无差别上传 | 不应把 Veo/Gemini 的参考能力等同于 H3 |
| [Runway References](https://help.runwayml.com/hc/en-us/articles/40042718905875-Creating-with-Gen-4-Image-References) / [Video Prompting](https://help.runwayml.com/hc/en-us/articles/39789879462419-Gen-4-Video-Prompting-Guide) | 可命名复用角色、物体、环境和风格参考；Sketch 可指定构图、视线和物体位置；图生视频 Prompt 主要描述运动，并建议逐项增加运动变量。`DOC-CONFIRMED` | 先通过图像把身份和构图落稳，再让视频 Prompt 聚焦主体运动、摄影机运动和环境运动；复杂 Prompt 应分层编译 | Runway 的三参考上限和 Gen-4 工作方法不是 H3 合同，应只迁移原则 |
| [Adobe Firefly Motion Reference](https://helpx.adobe.com/firefly/web/work-with-audio-and-video/work-with-video/match-camera-motion-to-reference-video.html) | 用 5–10 秒参考视频引导摄影机运动，并可同时使用首帧/尾帧工作流。`DOC-CONFIRMED` | 低保真机位预演是一类可选控制媒体，适合摄影机路径难以用文字稳定表达的镜头 | H3 `reference_video` 是否能像 Firefly 一样只迁移摄影机运动仍为 `UNKNOWN` |
| [Dreamina Storyboard / Multiframes](https://dreamina.capcut.com/create/storyboard-ai) | 多关键帧按顺序组织，每段转场可有独立运动描述，并允许预览后局部调整。`PRODUCT-CLAIM` | 关键帧之间必须视觉兼容；同一段中“静态锚点”和“连接动作”应分别描述 | 公开页面不能证明其人物一致性算法，也不代表即梦国内版本功能完全相同 |

闭源产品对当前项目最重要的启发不是“再做一个画布工作流”，而是把这些能力作为 Agent 可调用的 Tools 和持久化工件提供。用户不给中间指令时，Harness 可以自动组合；用户要求修改角色衣服、某一镜头或全部相关镜头时，同一批 Tools 仍可局部工作。

## 开源工程源码审查

### MiniMax-H3 官方仓库

固定 commit：[`d21241f0a4b3acbb34c97dae47fa417b7065e438`](https://github.com/MiniMax-AI/MiniMax-H3/commit/d21241f0a4b3acbb34c97dae47fa417b7065e438)。

官方仓库把完整系统拆为 Context-IR、768P Base 和 2K Regenerate，并明确把 Context-IR 视为最终质量的关键输入处理层。官方 `h3-prompt-writing` Skill 进一步定义：

- T2VA/I2VA/FL2VA/L2VA/Ref2VA 应使用不同 Prompt 结构；
- 首尾帧路线需要描述可观察的中间变化并逐步落到终态；
- 摄影机运动应表达为运动类型、幅度和速度；
- 多镜头需给出递增切点，切镜应带来新的状态、空间或信息；
- 对白使用稳定说话人 ID，画外音需明确在场人物闭嘴；
- Reference 模式要为图片、视频和音频定义明确角色与保留范围。

这些规则已经在 Phase 25 的 `H3PromptCompilerSkill` 中落地。AceData 是否在服务端再次运行官方 H3 Context-IR 仍未公开，因此客户端继续生成结构化 Prompt 是当前唯一可控的语义层。`DOC-CONFIRMED` / `CURRENT-IMPLEMENTED`

### LTX Desktop

固定 commit：[`68cd86c15e5fd25f56229ea63c0dbcb0338f7812`](https://github.com/Lightricks/LTX-Desktop/commit/68cd86c15e5fd25f56229ea63c0dbcb0338f7812)。

源码中可直接确认：

- `backend/services/prompt_enhancement/system_prompt.py` 把多关键帧视为带时间和强度的视觉事实，要求先描述每个静态状态，再描述连接动作；同一人物不能变成复制体，不能瞬移，附加文字不能覆盖关键帧；
- `backend/services/prompt_enhancement/i2v_frames.py` 对关键帧排序并标记帧号、时刻和 lock strength；
- `backend/handlers/retake_handler.py` 与 `backend/services/retake_pipeline/ltx_retake_pipeline.py` 支持只重做所选时间段，并能分别处理音频和视频；
- `backend/handlers/suggest_gap_prompt_handler.py` 同时观察前片尾帧和后片首帧，为中间缺口生成连接 Prompt；
- `frontend/views/editor/useRegeneration.ts` 保留生成参数和 alternate takes，使单镜可以重新生成和切换版本。

可迁移的是“关键帧是时序事实”“替代 Take”“局部时间段重做”和“前后边界共同参与补缝”。LTX 模型内部的 temporal mask、keyframe strength 和 Retake conditioning 不能直接复制到 H3。`SOURCE-CONFIRMED`

### ViMax

固定 commit：[`05a48943878312d88fe5a016c12a9654940ecc43`](https://github.com/HKUDS/ViMax/commit/05a48943878312d88fe5a016c12a9654940ecc43)。

源码中最值得迁移的部分：

- `agents/reference_image_selector.py` 在候选较多时先做文本过滤，再做多模态筛选；优先与目标机位和构图相近、时间更近且不重复的参考，每个角色画像最多选一个最合适角度；
- `agents/camera_image_generator.py` 建立 camera tree，先从父机位生成包含切镜的过渡视频，检测切点后提取新机位首帧，再进行图像修正；这是“先对运镜和机位关系做粗预演”的具体实现；
- `pipelines/script2video_pipeline.py` 将规划和渲染分开，缓存中间工件，缺失时才继续生成，并为人物生成正、侧、背参考；
- `agents/best_image_selector.py` 比较候选的角色、空间和描述准确性。

不能照搬的部分：它强制为所有角色生成三视图、总是选择一个“相对最好”的候选、缺少视频 QA/修复闭环，最终主要是顺序拼接。当前 Harness 应保留 `NONE_ACCEPTABLE` 和按镜头需要决定视图。camera tree 可作为可选 `MotionPrevisTool` 思路，不进入默认前置。`SOURCE-CONFIRMED`

### Toonflow

固定 commit：[`e03cf590eb0cab63534a4040db9acb4ec95b42a6`](https://github.com/HBAI-Ltd/Toonflow-app/commit/e03cf590eb0cab63534a4040db9acb4ec95b42a6)。

可迁移机制：

- `src/utils/agent/memory.ts` 用 project/agent/episode `isolationKey` 隔离记忆，并组合短期消息、摘要和向量召回；
- `src/utils/agent/skillsTools.ts` 先公开 Skill 摘要，匹配后再 `activate_skill`，需要时才读取具体资源；
- 不同 Agent 通过 Skill 和 Tool 委派脚本、分镜和生产子任务；
- 视频 Prompt 可按模型和模式选择不同文件。

不应照搬：`production_agent_decision.md` 定义固定六阶段、强制停顿和确认，一些 supervision Skill 还包含大量静态红线；视频质量失败主要只表现为任务状态，没有真正的生成后观察和修复规划。这与当前“Tools 可组合、默认无人值守、用户可随时修改”的目标相冲突。`SOURCE-CONFIRMED`

### ComfyUI

固定 commit：[`d8e7bbc9d586d95f758d6b0ed23d519088be578a`](https://github.com/comfy-org/ComfyUI/commit/d8e7bbc9d586d95f758d6b0ed23d519088be578a)。

`comfy_execution/caching.py` 根据节点类型、当前输入、`IS_CHANGED` 和有序祖先输入构建执行签名；`execution.py` 只执行当前输出所需且缓存失效的依赖。可迁移的是**语义依赖失效和工件复用**，不是把产品改造成节点图，也不是引入 hash 冻结。当前 `ChangeIntent → ProjectIndex → ImpactPlan` 已经具备更适合短剧对象模型的局部影响语义。`SOURCE-CONFIRMED`

### AESR

固定 commit：[`a2b3d010e4b66eed0398ead1b592bc9038f8f6db`](https://github.com/oceanflowlab/AESR/commit/a2b3d010e4b66eed0398ead1b592bc9038f8f6db)。仓库当前没有明确软件 License，因此本阶段只研究机制，不复制实现代码。

源码确认了论文所述的两个闭环：

- 全局：Generator 使用 Playbook 改写 Prompt；Analyzer 检查成片；Reflector 把具体失败抽象为通用经验；Curator 只向 `strategies/templates/pitfalls` 增量加入非冗余条目；
- 单样本：VLM 输出问题类型和时间段；`missing_visual_element`、`motion_state` 走问题片段抽帧、选择编辑底图和 GPT Image 修帧；`motion_process` 只进入视频编辑 Prompt。

最终公开 Playbook 包含 68 条 strategy、6 条 template 和 5 条 pitfall，但不少内容是 Seedance 2.0、身份挑战和 ArcFace 指标特化。例如“不要多视图”“尽量限制大角度转头”不能直接成为真人短剧的通用规则。它还固定为每个样本生成五条路径后打分选优，属于竞赛型 test-time scaling，不是低重试方案。可迁移的是**经验抽象、时间定位和按问题选择修复介质**。`SOURCE-CONFIRMED`

### MSVBench 与 MultiShotMaster

- [MSVBench](https://github.com/HITsz-TMG/MSVBench) 固定 commit [`309318bf29594fe21a487d4fd78284faef0d777b`](https://github.com/HITsz-TMG/MSVBench/commit/309318bf29594fe21a487d4fd78284faef0d777b)，把评测拆为视觉质量、故事对齐、视频一致性和运动质量；源码包含状态变化保持、镜头视角对齐、角色/脸/背景/衣服颜色/相对大小一致性、摄影机控制、动作强度和物理交互等独立模块。它适合离线回归和整集抽样，不适合作为每个镜头都运行的重型在线门。`SOURCE-CONFIRMED`
- [MultiShotMaster](https://github.com/KlingAIResearch/MultiShotMaster) 固定 commit [`1df812dda262639e4a3ca6e4a1da9000b0a8e124`](https://github.com/KlingAIResearch/MultiShotMaster/commit/1df812dda262639e4a3ca6e4a1da9000b0a8e124)，用 global caption 固定主体、场景和风格，再用 per-shot caption 与 shot frame groups 生成多镜头；当前开源多参考版本仍未发布，而且方法依赖 Wan 模型训练与 latent 分段，不能用于黑盒 H3。可迁移的是 global invariants 与 per-shot delta 分离。`SOURCE-CONFIRMED`

## 前沿论文的可迁移边界

| 工作 | 论文贡献 | 可落到当前 Harness | 不能直接迁移 |
| --- | --- | --- | --- |
| [AESR](https://arxiv.org/abs/2608.20749) | 面向闭源视频模型的 Agentic Playbook、时间定位、关键帧语义修复和多路径选优。`PAPER-REPORTED` | H3 专属经验库；问题类型 + 时间段 + 修复建议；终态/缺失问题优先修视觉锚点，过程问题优先修运动描述 | Seedance 2.0 编辑能力、固定五路径和竞赛权重；论文 50 个视频消融中自动指标并非所有维度都由完整 AESR 最优，因此不能宣称固定链路必胜 |
| [Video-T1](https://arxiv.org/abs/2503.18942) | 把 test-time scaling 视为搜索，Tree-of-Frames 在未完成轨迹上剪枝。`PAPER-REPORTED` | 把候选数当上限，按检查结果继续或停止；不同阶段使用不同观察重点 | AceData 不暴露 latent、噪声轨迹或部分去噪帧，不能实现论文式 ToF |
| [VideoReward](https://arxiv.org/abs/2501.13918) | 人类 pairwise、多维视频偏好和 reward-guided generation。`PAPER-REPORTED` | `CompareTakesTool` 采用相对比较，并分开视觉、运动和文本/意图维度 | 不训练 Flow-DPO/Flow-NRG，也不把未校准分数跨调用比较 |
| [MSVBench](https://arxiv.org/abs/2602.23969) | 分层脚本与参考图驱动的多镜头评测，覆盖视觉、故事对齐、连续性和运动；作者报告与人类排序 94.4% Spearman。`PAPER-REPORTED` | 建立单镜、边界、场景和整集四层检查；离线回归按模块选用，不追求一个总分 | 其大型模型和多个感知模型成本较高，不能直接放入每次 AUTO_DRAFT |
| [PersonaShot](https://arxiv.org/abs/2608.16717) | 人物连续性分为镜内、跨镜和序列轨迹，并覆盖物理、情绪和电影语法。`PAPER-REPORTED` | 除脸和服装外，记录人物姿态/持物/情绪/视线/屏幕方向的边界状态 | 论文很新，当前未在本项目数据上验证其 evaluator |
| [FramePack](https://arxiv.org/abs/2504.12626) | 固定上下文下的长视频帧打包、早期终点和抗漂移。`PAPER-REPORTED` | 长镜头分段时提前定义边界终态，保留最近状态和少量关键历史，而不是把全部历史塞入 Prompt | 模型结构和采样顺序不可用于 H3 API |
| [VideoDirectorGPT](https://arxiv.org/abs/2309.15091) | LLM 先产生实体、场景和空间布局，再生成多场景视频。`PAPER-REPORTED` | 延续当前 Bible、Scene、Shot 和 ReferencePlan 的分层规划 | 其布局到视频模型的专用接口不可用 |
| [DrawVideo](https://arxiv.org/abs/2605.23508) | 全局多镜头与局部单镜草图共同控制长视频。`PAPER-REPORTED` | 对高风险镜头可选生成构图/空间草图或关键帧，分开 appearance、spatial state 和 motion | 不能把每镜画草图设为固定步骤 |
| [MultiShotMaster](https://arxiv.org/abs/2512.03041) | 模型内部支持 global caption、per-shot caption 和可变镜头时长。`PAPER-REPORTED` | H3 Prompt IR 同样应分开整段不变量和每镜增量 | 需要训练开源 Wan 模型，不属于当前 Provider 路线 |

MotionCtrl、CameraCtrl、CamI2V、ReCamMaster、VACE、StoryDiffusion 和 ConsisID 等研究继续证明显式摄影机轨迹、参考主体和跨帧注意力可以改善控制，但提升主要发生在模型内部。它们对当前项目的作用是定义未来 `MotionPrevisTool`、参考视频和资产输入的表达方式，不能作为 H3 已经具备对应能力的证据。

## 当前 Harness 能力审计

| 能力 | 当前状态 | 缺口或风险 |
| --- | --- | --- |
| 项目/剧本资产隔离 | `CURRENT-IMPLEMENTED`：项目 ID、资产版本、PlanningRun、Task、Take、Timeline 和 inspection 都持久化隔离 | Agent 长期经验与项目内资产必须分层：通用 H3 经验可以跨项目，人物/场景媒体绝不能跨项目误召回 |
| Production Skills | `CURRENT-IMPLEMENTED`：抖音短剧和通用视频读取不同 Writer/Critic/Director 策略 | H3 模型经验还主要散落在 Prompt 和硬编码 capability profile 中，缺少可检索、带来源的 Playbook |
| H3 Prompt IR | `CURRENT-IMPLEMENTED`：T2VA/I2VA/FL2VA/Ref2VA、对白、时间线和媒体标签 | AceData 上相对旧自由 Prompt 的真实净增益仍为 `UNKNOWN` |
| ReferencePlan / Asset Catalog | `CURRENT-IMPLEMENTED`：结构化需求、批准资产、跨项目素材目录和同源派生 | 检索仍偏结构化/文本；缺少对小候选集的视觉重排、冗余和冲突判断 |
| 关键帧生成与单图检查 | `CURRENT-IMPLEMENTED`：关键帧 brief、候选、资产语义检查和批准状态 | 缺少同时比较多张候选的 `KeyframeCompareTool`；单张检查难以稳定判断“哪张更适合视频化” |
| 路线选择 | `CURRENT-IMPLEMENTED`：Direct、首帧、首尾帧、Reference 和可选 video guide | 尚未形成“最小充分控制”策略；有时可能为了参考而加入无益输入 |
| 顺序候选 | `CURRENT-IMPLEMENTED`：候选数是上限，首个通过即停止；结构性失败交给 Director | 真实 H3 各失败类型下追加候选的成功概率和平均调用数仍需采样统计 |
| 视频观察 | `CURRENT-IMPLEMENTED`：技术探测、均匀 + 高变化抽帧、镜内 contact sheet、相邻镜头边界、带时间范围的 observation | `QaIssue` 本身还没有时间段、证据 observation 和推荐修复介质的关联；长镜头中的短暂错误可能漏检 |
| Take 相对比较 | `CURRENT-IMPLEMENTED`：只从 QA-passed 候选选择，允许 `NONE_ACCEPTABLE` | 与人工选片的一致率仍为 `UNKNOWN` |
| Director 修复 | `CURRENT-IMPLEMENTED`：Prompt Retry、换关键帧、拆镜、换机位，结果可被用户忽略 | 缺少一个通用的 typed repair planner，将问题发生层和最可能有效的控制变量对应起来 |
| 整集 Reviewer | `CURRENT-IMPLEMENTED`：场景和整集审阅、关联镜头局部返工 | 目前更偏故事/镜头问题聚合，尚未系统覆盖人物物理状态、情绪轨迹和电影语法的跨镜变化 |
| 语义修改与局部重算 | `CURRENT-IMPLEMENTED`：`ChangeIntent → ProjectIndex → ImpactPlan` | 通用 Change Executor 尚需把计划自动执行到资产、关键帧、视频和时间线；无需引入 ComfyUI 节点图 |
| 机位预演 / video reference | `CURRENT-IMPLEMENTED`：完整前镜、尾窗和低保真预演均可作为候选 | 真实单样本显示错误视频参考会继承错误动作；收益依赖镜头类型，不能默认启用 |

## 可落地的低重试闭环

低重试不是“尽量少调用模型”，而是在质量目标不降低的前提下，减少**明显不可能成功的请求、同类盲抽和无关分支重做**。

### 一、生成前：选择最小充分路线

Director Agent 根据现有 ShotPlan、ReferencePlan、批准资产和前镜状态给出建议；这些建议不写死、不阻止用户强制执行原方案。只有 H3 的 4–15 秒整数、媒体数量、媒体类型和 frame/reference 互斥等技术合同是不可绕过的 Provider 校验。

| 镜头需要 | 默认优先路线 | 生成前应确认 |
| --- | --- | --- |
| 只需氛围、建立空间或通用 B-roll，无严格角色/终态 | Direct | Prompt 是否已包含可见主体、动作、环境和摄影机，不额外塞参考 |
| 角色身份、服装、场景和开场构图重要，终态可自由 | First frame | 首帧是否好看、无错误文字、动作起点明确，人物视角与后续动作兼容 |
| 必须落到明确姿态、物体状态或构图终点 | First + last frames | 首尾是否同一身份/空间/道具；中间路径是否能在 4–15 秒内自然完成；不兼容时建议换尾帧、拆镜或换覆盖 |
| 需要同时复用身份、环境、道具、声音或参考运动 | Reference | 每份媒体的职责是否清楚；选择最相关且不冲突的少量参考，不追求用满 9 图/3 视频/3 音频 |
| 摄影机路径或连续动作难以用文字表达 | 可选 reference video / Motion Previs | 引导视频是否只包含要继承的运动；是否会把前镜动作、错误构图或错误状态一起带入 |

三视图不是默认答案。Agent 应按目标构图选择：正面近景优先正脸特写，站姿和服装优先全身图，侧身/背身镜头才需要相应角度；复杂场景先检索 Master Wide 或匹配机位的局部视图。缺少合适视图时，Agent 可以建议从真实素材派生、GPT Image 编辑或让用户上传，但用户可忽略。

### 二、生成前：参考和关键帧质量控制

建议的最小工具组合：

1. `ReferenceVisualRerankerTool`：先用项目隔离的结构化/BM25 召回，再对由上下文窗口和候选质量决定的小型候选集做多模态重排；输出所选媒体、每份职责、冗余项、潜在冲突和置信度；
2. `KeyframeCompareTool`：同时查看同一镜头的多张候选、批准角色/场景/道具和前镜边界，按身份、空间、动作起点/终态、构图、可视频化程度和错误文字相对比较；允许 `NONE_ACCEPTABLE`；
3. `InputCompatibilityCheck`：不增加门/区域等领域字段，只观察首尾帧和参考是否支持当前镜头意图；输出“可直接生成 / 建议换关键帧 / 建议改路线 / 建议拆镜”，且保持 advisory。

这一步使用语言/视觉模型和已有图片，不消耗 H3。它优先拦截“错误的门、错误把手、冲突服装、两套布局、终态无法到达”这类输入层失败，而不是付费生成后继续抠 Prompt。

### 三、生成后：先定位，再决定是否追加候选

`InspectVideoTool` 应把每个问题关联到：

- 通用失败类别；
- 近似时间段和可见证据；
- 影响层：单帧外观、运动过程、运动终态、镜头边界、故事/对白语义或纯技术；
- 建议改变的控制变量；
- 是否适合“同路线再抽一版”。

| 观察到的问题 | 优先动作 | 不推荐的默认动作 |
| --- | --- | --- |
| 随机瑕疵、局部构图或表演气口，输入和语义都正确 | 同路线追加一个候选，再相对比较 | 立即重写整个镜头或重做资产 |
| Prompt 漏掉动作完成态、说话人、声音来源或切点 | 重新编译 H3 Prompt IR | 不改 Prompt 只盲抽多版 |
| 身份、服装、场景或道具参考错误/互相冲突 | 换参考或重做关键帧 | 继续叠加文字约束 |
| 缺失视觉元素或动作终态错误 | 编辑/替换目标关键帧；必要时改首尾帧路线 | 只加入更多抽象形容词 |
| 速度、节奏、中间动作或摄影机运动错误，但终态正确 | 改运动描述，或在有证据时使用 motion guide | 为过程问题重做所有静态资产 |
| 复合手物接触、转身、跨区域或多角色动作过载 | Director 比较拆镜、换覆盖机位、缩短动作链 | 针对门、把手等单例扩充通用数据模型 |
| 相邻镜头物理状态、情绪、眼线或屏幕方向跳变 | 修改后镜首帧/参考，或换一个更容易衔接的覆盖镜头 | 只看当前 Take 的单镜得分 |
| 多个候选都有结构性矛盾 | `NONE_ACCEPTABLE`，返回 Director | 强选“相对最好但不可用”的 Take |
| 当前候选已满足故事动作、连续性和成片质感 | 立即停止 | 为了凑固定候选数继续花费 |

### 四、把失败沉淀为模型经验，而不是样片特化

引入轻量的 `H3CapabilityPlaybook`，但不建设动态 Skill Registry，也不把它变成硬门。它属于 H3 模型专属知识，由 Agent 在编译路线或修复时按镜头特征检索，最小结构为：

- `strategy`：某类镜头如何选择路线、参考和 Prompt；
- `template`：H3 官方 Prompt IR 中可复用的表达结构；
- `pitfall`：已观察到的通用失败、适用条件和更有效的修复方式；
- `evidence`：来源是官方文档、开源工程、论文还是本项目 H3 Take；
- `confidence`：作为建议权重，不作为执行门。

学习闭环为：

`H3 Take → InspectVideo 时间定位 → Director 选择修复 → 新 Take/人工结果 → Reflector 抽象通用经验 → Curator 去重后写入 Playbook`

单个门场景只能沉淀为“复杂手物接触 + 转身 + 空间穿越容易使接触点、朝向和终态漂移”，不能沉淀为门轴、把手内外或推拉方向专用字段。新经验可以立即作为低置信建议使用，之后根据更多跨镜头证据更新；用户始终可以忽略。

### 五、局部重算和工件复用

低重试还包括不重复生成已经正确的部分：

- 修改人物服装只失效实际使用该服装版本的资产、关键帧、Take 和时间线分支；
- 修改某句对白只重编相关镜头和字幕，不重做无关场景图；
- 修改一个尾帧只重做依赖该尾帧的 H3 Take，不重做角色母版；
- 选用新的 Take 后旧 Take 保留为替代版本，但不进入正式 Timeline；
- 项目资产、抽帧、报告、Prompt IR 和 Provider task 都按真实项目 ID 持久化，不从其他剧本目录误检索。

这里复用 ComfyUI 的“只执行失效依赖”原则，但继续使用现有版本关系和 `ImpactPlan`，不增加节点工作流、文档冻结或 hash 审批层。

## 长镜头和多镜头连续性

H3 单次只能生成 4–15 秒整数时长。一个剧情镜头或导演意义上的长镜头可以超过 15 秒，但生产上必须拆成多个 H3 generation chunk。拆分不是把它变成不相关镜头，而是让 Agent 持续维护：

- 当前人物身份、服装、持物、姿态、情绪和视线；
- 当前场景区域、相对位置、光线和摄影机方向；
- 上一 chunk 的边界终态与下一 chunk 的开场动作；
- 哪些信息必须连续，哪些位置可用遮挡、切近景、反应镜头或环境覆盖隐藏接缝。

建议四层检查：

1. **镜内**：动作过程、接触、身份、构图和瞬时变形；
2. **chunk/镜头边界**：前一尾部与后一开头的物理状态、情绪、眼线、屏幕方向和声音延续；
3. **场景序列**：人物和道具状态是否按剧情持续变化，是否出现状态重置；
4. **整集**：故事可理解性、角色弧线、场景回访、节奏和剪辑覆盖。

`InspectVideoTool` 负责前两层，Scene/Episode Reviewer 负责后两层；Agent 可以按风险抽取更密集帧或调用专门检查项。长镜头不需要增加一套独立 workflow，只需要让现有 Tools 接受更长的连续性上下文和多个 generation chunk。

机位预演应保留为可选升级：当摄影机路径本身是主要风险，或同一空间需要建立多个覆盖机位时，Agent 可以先用低成本图像、草图、简单动画或已有视频建立 guide；普通对白近景、静态建立镜头和已有合适关键帧的镜头不必先预演。

## 下一步实施顺序

### Phase 27：生成前视觉输入质量（已实现）

不调用真实 H3：

1. 实现 `ReferenceVisualRerankerTool`，复用当前 Asset Catalog、项目隔离和 `ReferencePlan`；
2. 实现 `KeyframeCompareTool`，允许 `NONE_ACCEPTABLE`；
3. 增加首尾帧/参考的通用兼容性建议，不增加场景专用字段，也不阻断用户；
4. 用现有真实资产、历史关键帧和 Fake Provider 验证 Tool 合同、持久化和用户覆盖。

实现结果见 [Phase 27：生成前视觉输入质量 Tools](38-phase27-visual-input-quality-tools.md)。

### Phase 28：类型化视频诊断与修复规划

不要求新增 H3：

1. 将 `QaIssue` 与 observation 时间段、证据和 `repair_modality` 关联；
2. 实现独立 `RepairPlannerTool`，比较同路线追加、重编 Prompt、换参考、换关键帧、motion guide、拆镜和换覆盖；
3. 建立轻量 H3 Capability Playbook 和增量 Curator，保留来源、适用条件和置信度；
4. 增加跨镜物理/情绪/电影语法的场景级检查，但保持 advisory。

### Phase 29：小规模真实 H3 低重试验证

在用户确认后才提交付费请求。选择少量代表性镜头覆盖：简单主体运动、精确终态、复杂手物接触、相邻镜头续接和摄影机路径。每镜先生成一条，只有检查结果说明同路线追加仍有价值时才生成第二条；结构性失败直接换控制变量。比较：

- 旧自由 Prompt 与官方 H3 Prompt IR；
- 原检索与视觉重排后的参考；
- 单图检查与 KeyframeCompare；
- 固定双候选与自适应停止；
- 自动建议与人工选片/修复判断的一致性。

记录真实指标：首条可用率、每个入选 Take 的 H3 请求数、各失败类别的修复成功率、被避免的无效请求、人工覆盖率和最终镜头质量。没有这些数据前，不宣称具体百分比节省。

### Phase 30：长镜头与多镜头回归

在单镜低重试闭环稳定后，再验证 15 秒以内 H3 多镜头、超过 15 秒的 generation chunks、边界续接、可选 motion previs 和整集状态轨迹。MSVBench/PersonaShot 的模块只挑与当前真人短剧相关的轻量检查，不整体引入重型 benchmark。

## 当前明确不做

- 不接入闭源平台，也不依赖其运行时；
- 不替换当前 LTX Desktop 底座，不整体魔改 Toonflow、ViMax 或 ComfyUI；
- 不训练新视频模型、VideoReward 或 MultiShotMaster；
- 不把三视图、九宫格、机位预演、reference video 或固定候选数设为所有镜头必经步骤；
- 不建设动态 Skill Registry、节点图编辑器、hash 冻结、文档审批门或样片专用 schema；
- 不让 Critic 仅凭分数自动扩大重做范围；
- 不恢复声音 QA。角色音频输入继续作为可选 H3 reference audio 能力。

## 仍为 UNKNOWN

- AceData 是否调用了官方 H3 Context-IR，以及官方 IR 相对旧 Prompt 的真实净增益；
- H3 Reference 模式中多角度人物图的最佳组合，正脸 + 全身是否普遍优于三视图；
- GPT Image 编辑后的关键帧对 H3 视频质量、身份和运动自然度的净增益；
- `reference_video` 对摄影机路径、人物运动和声音分别保留到什么程度；
- GPT-5.6-sol 对关键帧和 Take 的相对比较与人工选择的一致率；
- 分层 QA 能否稳定找到短暂运动错误，以及它会增加多少语言模型成本；
- Capability Playbook 的哪些经验能跨剧本稳定复用；
- 在真实三分钟成片上，低重试闭环能把每个入选 Take 的平均 H3 请求数降到多少。

## 本轮源码快照

| 项目 | Commit | 本轮用途 |
| --- | --- | --- |
| [MiniMax-H3](https://github.com/MiniMax-AI/MiniMax-H3) | `d21241f0a4b3acbb34c97dae47fa417b7065e438` | 官方模式、Prompt Skill、Context-IR 和多模态角色 |
| [LTX Desktop](https://github.com/Lightricks/LTX-Desktop) | `68cd86c15e5fd25f56229ea63c0dbcb0338f7812` | 多关键帧、Retake、Gap 和 alternate takes |
| [Toonflow](https://github.com/HBAI-Ltd/Toonflow-app) | `e03cf590eb0cab63534a4040db9acb4ec95b42a6` | Skill 渐进加载、项目记忆与固定 workflow 反例 |
| [ComfyUI](https://github.com/comfy-org/ComfyUI) | `d8e7bbc9d586d95f758d6b0ed23d519088be578a` | 依赖缓存与局部执行原则 |
| [ViMax](https://github.com/HKUDS/ViMax) | `05a48943878312d88fe5a016c12a9654940ecc43` | 参考筛选、三视图和 camera tree 预演 |
| [AESR](https://github.com/oceanflowlab/AESR) | `a2b3d010e4b66eed0398ead1b592bc9038f8f6db` | 黑盒模型 Playbook、时间定位和语义修复 |
| [MSVBench](https://github.com/HITsz-TMG/MSVBench) | `309318bf29594fe21a487d4fd78284faef0d777b` | 多镜头分层评测与状态持续性 |
| [MultiShotMaster](https://github.com/KlingAIResearch/MultiShotMaster) | `1df812dda262639e4a3ca6e4a1da9000b0a8e124` | global invariants、per-shot captions 和模型内部边界 |
