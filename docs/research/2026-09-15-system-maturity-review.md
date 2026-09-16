# Short Drama Studio：系统成熟度与视频平台对照研究

后续进度：用户已授权按建议顺序实施，四批改进于 2026-09-15 完成当前工作树验收，见 [实施与验收记录](2026-09-15-maturity-improvements.md)。下文保留修复前的研究发现、复现结果和优先级；当前能力以实施记录与产品交付清单为准。

研究日期：2026-09-15。研究对象为当前工作树（main，HEAD e91794b，包含原有未提交修改），覆盖产品入口、项目与媒体状态、Agent/Skills/Tools、H3 执行、观察与修复、剪辑声音、导出恢复和交付证据。外部对照使用当日可读取的官方产品文档、官方发布说明及公开源码。

本次新增研究报告和两个最小复现附件，没有修改产品代码、运行用户制作任务或调用收费 Provider。产品服务当时未运行；前端发现来自源码调用链，未将其表述为本次实际 UI 验收。平台能力属于公开资料核实，没有进行同题付费画质对比，也无法据此判断闭源内部的 Agent、提示词或恢复实现。

## 1. 核心判断

**系统已经具备真实制片与剪辑的完整骨架，当前成熟度仍不足以证明稳定、自助、少返工地完成作品。最值得改进的是内容保护、精确修改、动态视听质量判断和制作效率。**

已交付的真实案例、当前代码能力和最新版本的整片成功率，需要分别看待。原 131 秒成片是真实成果；局部字幕/声音修改和导出也有真实验收；但这些不能代替当前版本从空项目开始的完整质量结果。本次核对的最近一轮独立空项目验收记录只到视频前准备，随后修复没有对应的新一轮全流程实跑证据。[产品交付记录](/Users/liyifan/Documents/ChatGPT/ai短剧调研/docs/product-delivery-checklist.md:42)

本次还确定性复现了两个当前缺陷：

- **取消已经返回成功，H3 任务仍可能继续提交。**
- **没有被任何片段采用的旧素材缺失，也会阻断当前剪辑保存和导出。**

这两项与模型好坏无关，应优先修复。剧本草稿保护、后端重启后的工作区保留、Agent 修改后的剪辑刷新，也存在明确源码缺口。

## 2. 整个系统分别成熟到什么程度

下表是证据分层，不给没有测量依据的百分制分数。

| 领域 | 当前已有能力 | 当前主要缺口或验证边界 |
| --- | --- | --- |
| 产品与目标 | 剧本/镜头/成片、共享资产、常驻 Agent；目标决定完成边界；仅剧本任务已真实结束于剧本 | 剧本草稿切页丢失；恢复过程可能清空未保存输入 |
| 内容与引用 | ProjectDocument 与本集时间线为统一真相；具体媒体、用途、正文位置与时段绑定；跨集共享 | 未采用媒体缺失仍全局阻塞；跨集替换的影响位置在界面展示不足 |
| Agent 执行 | Leader/Worker 工单、四项并行、逐个回收、媒体轮询释放槽、已开始操作先落盘再确认暂停、同任务恢复 | 取消存在竞态；未知提交候选查询只有第一页；完整交付仍缺最新独立验收 |
| 上下文与 Tool | 实际 tokenizer、258k 总预算、200k 整理至160k、精确回读、紧凑回执、完整 Trace | 已实现机制是否减少真实往返尚未完整测量；收尾回执可更直接附当前输入状态 |
| Skills 与 Prompt | 27种方法；角色/阶段加载；来源与运行内容分开；专业摄影、表演、声音、局部修复知识 | 目录/加载验收覆盖充分，各体裁的真实效果覆盖不足；不能从方法数量推导制作能力 |
| 图片与视频 | 图片生成/编辑、多参考；H3 组准备与执行、首尾帧、多画幅；实际采用帧可复用 | 保留原视频的生成编辑和专用表演控制未接通；剩余一致性问题缺少实测闭环 |
| 观察与修复 | 时间区间抽帧、真实引用对比、候选比较、音频转录/对齐；局部修改同步相关内容 | 稀疏静帧不足以判断连续动作、口型与视听节奏；复杂运动的精确诊断较弱 |
| 剪辑与声音 | 多轨、裁切、拆分、变速、局部镜头同步、字幕/原声及真实导出 | 编辑器不主动接收 Agent 新修订；混音缺少峰值/包络处理；常用文字与转场未接通 |
| 运行与交付 | 本机 macOS 包、当前修订导出、解码校验、完整过程记录 | 导出任务持续展示/取消薄弱；素材重定位与项目携带不完善；其他平台未验收 |

主要依据：[交付清单](/Users/liyifan/Documents/ChatGPT/ai短剧调研/docs/product-delivery-checklist.md:15)、[Research 已实现改造](/Users/liyifan/Documents/ChatGPT/ai短剧调研/docs/research/2026-09-11-mature-video-production/09-implementation-delivery.md:5)、[当前方法库](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/README.md:5)、[当前 H3 规格适配](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/providers/h3_output_format.py:5)。

**已有成熟机制应保留。** 本次核查确认，工具定义保留、逐个 Future 回收、实际前镜边界、精确选区、当前字幕对齐、单镜头保存、已选 DRAFT 使用和保存路线复用已经实现。旧报告里的“缺少生成中草稿”“只有11种方法”“只支持9:16”“只按前镜设计推测实际结果”等结论已过时。当前工作树还包含 H3 图片减包、等值 GenerationPlan 复用等修改，已按现状纳入判断。

## 3. 当前明确存在的问题

证据强度分为：**本地复现**、**源码确定**、**历史真实记录**、**效果待验证**。P1 表示优先解决的正确性或交付问题；P2 表示直接影响体验、效率或常用能力。

### 3.1 P1：取消成功后仍可能提交 H3

准备阶段仍持有 QUEUED 的旧任务对象。取消入口保存 CANCELLED，准备函数随后将旧对象写成 SUBMITTING；推进锁与取消没有采用一致的任务串行化边界。下一次公共服务推进重新读取任务后仍能进入提交。

**本地复现结果：CANCELLED → SUBMITTING → ACKNOWLEDGED，Fake 提交次数从0变为1。** 所有数据位于临时库，供应商为现有 Fake，未产生真实提交。

影响：用户已经收到取消确认，系统仍可能生成并产生费用。建议统一取消与推进的状态写入边界，并在准备写回时核对最新状态；保持慢上传在锁外，不要用长时间大锁解决。

证据：[取消入口](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/h3_worker.py:139)、[准备写回](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/h3_worker.py:457)、[公共服务取消](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/service.py:13977)、[复现脚本](/Users/liyifan/Documents/ChatGPT/ai短剧调研/output/system-maturity-review-2026-09-15/reproduce-h3-cancel-during-prepare.py)、[实际结果](/Users/liyifan/Documents/ChatGPT/ai短剧调研/output/system-maturity-review-2026-09-15/reproduce-h3-cancel-during-prepare-result.json)。

### 3.2 P1：剧本草稿和故障恢复时的输入保护不完整

场次草稿只在 SceneEditor 内部保存。切换剧本到镜头/资产后组件卸载，未保存正文随之消失；工作台的未保存统计只包含分镜。保存期间正文仍可输入，但请求成功后无条件清空草稿，也存在丢掉后续输入的条件。

原生 App 在后端 restarting/dead、connected 变为 false 时用启动页替换整个工作区。分镜草稿、局部剪辑和聊天输入都依赖内存状态；后端恢复不等于这些输入恢复。这是原生后端状态变化的路径，不泛指所有 SSE 短暂断线。

建议按集/场次保留轻量草稿，统一未保存状态；保存只确认所提交快照；首次打开后在重连时保留工作区，用状态条表达连接状态。必要草稿可以轻量持久化，无须增加内容版本界面。

证据强度：源码确定，尚未本次 UI 复现。[场次草稿与保存](/Users/liyifan/Documents/ChatGPT/ai短剧调研/frontend/features/short-drama/StudioScript.tsx:9)、[工作台草稿统计与页面切换](/Users/liyifan/Documents/ChatGPT/ai短剧调研/frontend/features/short-drama/StudioWorkspace.tsx:131)、[原生工作区卸载](/Users/liyifan/Documents/ChatGPT/ai短剧调研/frontend/App.tsx:48)。

### 3.3 P1：无关旧素材丢失，会阻断当前保存与成片导出

编辑工程首次投影将所有对象的历史媒体版本加入 assets；保存验证对全部 assets 解析文件路径，没有限于实际采用媒体。导出又调用相同验证。

**本地复现：同一资产两版 PNG，唯一活动片段只采用新版；删除临时目录里的旧版后，验证、保存、导出前置检查全部抛 FileNotFoundError。** 去掉内存中未使用旧库项，或恢复其文件，验证立即通过。没有删除用户文件、创建导出任务或调用 Provider。

建议区分“素材库中暂时找不到的文件”和“当前交付实际必需的文件”；未采用的缺失项保留可定位状态，导出对当前采用且启用的素材给出具体名称、位置及重新链接入口。

证据：[所有历史媒体进入编辑库](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/studio_editorial.py:247)、[无差别文件验证](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/studio_editorial.py:102)、[导出复用验证](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/studio_export.py:117)、[复现脚本](/Users/liyifan/Documents/ChatGPT/ai短剧调研/output/system-maturity-review-2026-09-15/reproduce_unused_missing_media.py)、[实际结果](/Users/liyifan/Documents/ChatGPT/ai短剧调研/output/system-maturity-review-2026-09-15/unused-missing-media-reproduction.json)。

### 3.4 P2：Agent 改完了，打开的全片剪辑器仍可能显示旧内容

剪辑 store 在项目ID、镜头焦点或采用位置改变时创建；父项目的新 editorial revision 不触发刷新。用户在全片编辑器发消息让 Agent 修改，后台已保存新字幕/片段，编辑器仍保留旧 store；继续编辑才可能遇到409。

建议无本地改动时应用新修订并保留播放头和选区；存在本地改动时做对象范围合并或显示准确更新提示。现有冲突合并已经有基础，优先将它接到主动同步。

证据强度：源码确定，尚未本次 UI 复现。[store 加载条件](/Users/liyifan/Documents/ChatGPT/ai短剧调研/frontend/features/short-drama/StudioEditor.tsx:55)、[已有冲突合并](/Users/liyifan/Documents/ChatGPT/ai短剧调研/frontend/features/short-drama/StudioEditor.tsx:160)。

### 3.5 P2：声音“能导出”与“能交付”之间仍有距离

当前混音逐段用固定 volume 相加，最后硬限制到 Int16。对白、原视频声音、配乐同时存在时，超出的峰值直接削平。技术校验主要确认时长、尺寸、帧率与音轨存在，不说明响度、削波或对白可懂度合格。

此外，正式资产页能播放/导入声音、展示使用状态，但旧 VoiceReferencePanel 的手工声线选择、绑定/解绑与录音没有迁入 StudioWorkspace。后端和 Agent 已有声音绑定，缺的是正式产品的手工入口。

建议先补峰值检测、合理输出余量和简单音量包络/淡入淡出；按需要增加对白优先的配乐压低。把已有角色声音关系放到资产详情，便于试听与微调。

证据强度：源码确定；本次没有重新听审旧成片。[混音](/Users/liyifan/Documents/ChatGPT/ai短剧调研/electron/export/audio-mix.ts:108)、[导出技术检查](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/studio_export.py:256)、[正式资产入口](/Users/liyifan/Documents/ChatGPT/ai短剧调研/frontend/features/short-drama/StudioAssets.tsx:91)、[旧声音操作组件](/Users/liyifan/Documents/ChatGPT/ai短剧调研/frontend/features/short-drama/VoiceReferencePanel.tsx:239)。

### 3.6 P2：导出生命周期与常用后期能力未完全收口

导出进行中状态主要存在当前编辑器会话，重开时没有恢复该任务；成片页主要取 completed 结果，正在进行和失败任务缺少持续入口。后端能把中断导出标失败，当前产品链没有对应的取消导出接口。

转场、文字层、调整层在导出前明确拒绝。字幕与 PNG 图版可以使用，但片头文字和基本溶解仍需要绕路。UI 的导出请求固定24fps，后端已有更宽帧率合同。

建议让成片页持续显示最近导出状态、失败原因和重试；补本地取消；先接常用标题与淡化/溶解及输出帧率。显式拒绝不支持效果是正确行为，不能改成静默跳过。

证据：[编辑器导出状态](/Users/liyifan/Documents/ChatGPT/ai短剧调研/frontend/features/short-drama/StudioEditor.tsx:239)、[导出恢复](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/studio_export.py:271)、[效果限制](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/studio_export.py:127)。

### 3.7 P2：共享资产的作用范围和未知提交的找回不够清楚

共享图片替换会遍历全部集，资产详情的使用位置却主要遍历当前集，用户无法充分看到这次修改涉及哪些集和镜头。建议在现有资产关系中补齐跨集位置和修改回执，无需新增审批。

SUBMIT_UNKNOWN 的候选查询固定50条，HTTP offset 固定0；候选按模型、时长、尺寸等规格筛选。目标在后续页时当前列表找不到，同规格任务多时辨认成本较高。建议分页并按真实提交时间、已有预览等信息帮助找回。当前未知状态不盲重发、不自动采用任意候选的处理应保留。

证据强度：源码确定。[跨集替换](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/studio_media.py:83)、[当前集使用位置](/Users/liyifan/Documents/ChatGPT/ai短剧调研/frontend/features/short-drama/StudioAssets.tsx:45)、[候选查询](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/h3_worker.py:240)、[固定 offset](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/providers/minimax_h3_client.py:258)。

## 4. 更影响作品质量的能力差距

### 4.1 动态质量判断仍偏弱

当前视频观察支持具体源区间、参考对比和实际时窗，这是正确基础。但视频先生成360像素宽的候选帧，最终默认14帧、最多24帧，语言模型获得接触表及参考图片。提示词明确承认无法以此验证声音和两帧之间的动作。

对身份、构图、道具状态，抽帧有价值；对打斗接触、手部交换、流体效果、运镜速度、口型和表演节奏，稀疏静帧可能漏掉决定性问题。音频转录/对齐已经存在，也不能单独证明嘴型与声音同步。

建议在现有观察 Tool 中围绕具体失败时间段提供更密集、原尺寸的观察与对齐声音；仍不足时，再评估能消费连续视频与音频的理解接口。最终播放验收继续保留。不要把每条素材都送入固定反复 Critic。

证据：[观察范围与帧数](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/models.py:3211)、[抽帧实现](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/video_inspection.py:368)、[静帧输入边界](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/video_inspection.py:815)。这是能力边界；本次没有用它计算新的漏检率。

### 4.2 真正保留原视频的生成编辑尚未接通

系统已经能裁切、换采用区间、拼接，并针对受影响的 H3 组重新生成。导演修复也已返回真实 H3 准备入口。但 video.retake/extend/gap_fill 仍明确是 Mock；没有已接通的“以现有视频为主要输入、只改变指定内容”的真实执行能力。

因此，“保留表演和运镜，只去掉背景物体”“只改中间两秒动作”“延长结尾而保持对白”等任务，目前要依赖剪辑绕路或整组重新生成，保留已有成果的控制力有限。

Runway Edit Studio 和 Luma Modify 给出了可学习的入口：原片＋具体修改＋参考/关键帧＋保留强度或选定时间段。它们并不保证逐像素不变，公开文档也有模式与时长限制。适合先选择一种高频修复任务做窄范围比较，再决定是否增加 Provider。[Runway 编辑指南](https://help.runwayml.com/hc/en-us/articles/51683104370451-Creating-with-Edit-Studio)、[Luma Modify 指南](https://lumalabs.ai/learning-hub/ray3-modify-user-guide)

本地依据：[Mock 修订入口](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/tools/video/__init__.py:335)、[当前真实修复边界](/Users/liyifan/Documents/ChatGPT/ai短剧调研/docs/research/2026-09-11-mature-video-production/09-implementation-delivery.md:31)。

### 4.3 专业方法足够多，真实效果证据仍集中在少数任务

当前导演方法已经具体处理视线轴线、身份与服装、手物接触、动作覆盖、中文图版、跨镜对白、软参考与首尾帧差别；后期职责也要求按实际声音和当前剪辑工作。这些并非只剩空泛 Prompt。

问题在于：27种方法的主要验收是加载、映射、请求内容和 Fake 任务，新增软件工作流还有明确的外部依赖。不能把“导入 Blender/Houdini/After Effects 方法”当作软件连接器已可执行，也不能把方法通过测试当作广告、MV、动作短片都已成熟。

建议优先维护少量真实成功/失败例，围绕具体工具能力写修复策略；每种重要体裁至少形成可核对的成片样本。已有 H3 能力档案目前主要是预设风险类别加失败计数，可逐步补成功率、采用比例和有效修复经验，无须另建知识平台。

证据：[导演方法](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/shared/roles/director.md:3)、[后期方法](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/skills/shared/roles/post_director.md:3)、[27方法验收范围](/Users/liyifan/Documents/ChatGPT/ai短剧调研/docs/agent-skills-and-model-redesign.md:196)、[当前经验档案](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/quality_learning.py:157)。

### 4.4 Agent 收尾与真实效率需要用新结果校准

本次核对的第六轮独立空项目验收记录如下；这些是修复前基线，不能当作当前版本性能值：

| 项目 | 真实记录 |
| --- | --- |
| 墙钟时间 | 1小时48分28.81秒 |
| 内容库存 | 18镜、6组、26次成功图片任务 |
| 语言请求 | 409次，全部 Astra/xhigh |
| Tool | 315次：303成功、10 FAILED、2 INVALID_ARGUMENTS |
| 资产检查 | 39次 |
| 累计 Provider 输入 | 27,700,908 tokens，为409次累计 |
| 最终结果 | 自然 completed，但2组来源过期；0视频任务、0Take |

随后修复了交接、回读、附图、来源与路线复用等问题；第七批明确没有再加载或实跑完整任务。当前不能给出“已经提速多少”“不再返工”的结论。[第六轮记录](/Users/liyifan/Documents/ChatGPT/ai短剧调研/docs/research/2026-09-12-generation-quality/70-sixth-fresh-pre-video-end-to-end.md:68)、[修复与停止点](/Users/liyifan/Documents/ChatGPT/ai短剧调研/docs/research/2026-09-12-generation-quality/72-seventh-pre-video-consolidation.md:61)

收尾回执还可以更直接地附带“这次声称可执行的路线对应当前哪些来源、检查结果如何”。通用完成校验主要验证工件/版本/文件存在；具体 route 的当前 source_checks 在另一读取入口。把当前事实直接交给 Leader，可以减少再查一次，也减少把“已存路线”误解为“当前可执行”的机会。最终完成边界仍由任务语义决定。[完成校验](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/agent_runtime/completion.py:62)、[路线摘要](/Users/liyifan/Documents/ChatGPT/ai短剧调研/backend/short_drama/agent_runtime/artifact_resolver.py:1857)

效率改进应定位没有新增判断的语言往返、重复读取与重复编译；保留必要创作判断和已指定的 Astra/xhigh 配置。Anthropic 的官方工具设计建议同样强调有价值的工具边界、紧凑返回和真实任务结果评估，可用于迭代现有 Harness。[工具设计方法](https://www.anthropic.com/engineering/writing-tools-for-agents)

## 5. 成熟平台提供了哪些可借鉴的工作方式

以下是能力对照，不是质量排行榜。“未核实”表示资料不足，不表示对方没有能力。产品、模型、API和开源桌面代码分开判断。

| 对照对象 | 官方可核实的工作方式 | 对本项目最有价值的借鉴与边界 |
| --- | --- | --- |
| Runway | 命名参考与 @；Edit Studio 选择代表关键帧、先预览图、再对选定视频区间生成修改；工作流可保留既有输出 | 借鉴保留成果的修复入口和可选预览。当前 Single edit 可用，Multi-edit/Expand 尚未提供；历史/工作流不证明崩溃恢复。[References](https://help.runwayml.com/hc/en-us/articles/40042718905875-Creating-with-Gen-4-Image-References)、[编辑](https://help.runwayml.com/hc/en-us/articles/51683104370451-Creating-with-Edit-Studio)、[Workflows](https://help.runwayml.com/hc/en-us/articles/45763528999699-Introduction-to-Workflows) |
| Google Flow | Ingredients、实际帧另存作参考/首尾帧、场景排序裁切；当前支持矩阵按模型分别列生成、编辑与延长 | 借鉴输入用途、实际边界和能力矩阵。Omni 编辑与 Veo Extend 不能混成全模型通用；不同模式受来源/地区限制。[场景编辑](https://support.google.com/flow/answer/16935718?hl=en)、[能力矩阵](https://support.google.com/flow/answer/16352836?hl=en) |
| Luma Dream Machine | Ray3 Modify 使用原视频、角色参考和关键帧；Adhere/Reimagine 控制结构保留程度 | 适合参考动作/画面保留与修改强度表达。强 Reimagine 可能改变原有运镜；指南说明超过5秒输入的 end keyframe 取5秒位置，不能自行解释成全片尾帧。[官方指南](https://lumalabs.ai/learning-hub/ray3-modify-user-guide) |
| Kling 3.0 / Omni | 官方发布说明多镜头、Elements参考、逐镜头时长/景别/角度/运镜，以及原生音画与多角色发声 | 借鉴真正进入生成输入的分镜控制。来源为厂商发布说明，指南本次未能读取；不据此断言当前所有套餐或API可用。[快手官方发布](https://ir.kuaishou.com/news-releases/news-release-details/kling-ai-launches-30-model-ushering-era-where-everyone-can-be) |
| Seedance / 即梦生态 | Seedance官方描述文本、图像、视频、声音联合参考，镜头编辑与续接；也明确承认多主体一致性、细节、声音和复杂编辑仍有限 | 借鉴动作/镜头参考与按缺陷选模式。模型发布资料不等于即梦全部产品或当前火山API合同；本次API文档读取失败，未据此添加规格结论。[官方发布](https://seed.bytedance.com/en/blog/seedance-2-0-official-launch) |
| Adobe Firefly / Premiere | 在时间线选区生成，结果成为可编辑clip；独立字幕轨、转录修正和SRT；Generative Extend有声音限制 | 借鉴生成结果直接回时间线、字幕独立可改和后期交付。Extend不延长对白，含音乐片段不支持Generative Extend，不可泛化为任意声画续写。[时间线生成](https://helpx.adobe.com/premiere/desktop/edit-projects/edit-with-generative-ai/generative-media-tool-overview.html)、[字幕](https://helpx.adobe.com/premiere/desktop/add-text-images/insert-captions/about-captions.html)、[Extend](https://helpx.adobe.com/premiere/desktop/edit-projects/edit-with-generative-ai/generative-extend-overview.html) |
| LTX Studio / Desktop | 云端Studio有Elements、角色声音与镜头/时间线工作流；Desktop公开源码明确仍为Beta | 借鉴镜头与资产对象组织。云端能力不等于开源桌面底座，也不等于本仓库已接入；当前产品继续遵守不分发LTX模型的边界。[Studio](https://ltx.io/blog/top-ltx-studio-features)、[Desktop源码](https://github.com/Lightricks/LTX-Desktop) |
| ViMax | 公开的概念/剧本/小说到视频流程、镜头参考与Agent loop；当前loop复用工具定义、处理图像结果与上下文压缩 | 可读源码帮助比较工具循环和视觉交接，当前项目已具备其中多项机制。没有同条件证据说明整体替换会更快或更稳。[仓库](https://github.com/HKUDS/ViMax)、[loop源码](https://github.com/HKUDS/ViMax/blob/main/agent_runtime/loop.py) |
| Toonflow | 公开短剧工作区、剧本/分镜/媒体Agent与Markdown方法，提供多平台分发 | 可研究具体制作组织；其画布和多层Agent不必迁入本产品。当前LICENSE有附加限制，不能当作纯Apache-2.0代码直接搬用。[仓库](https://github.com/HBAI-Ltd/Toonflow-app)、[完整LICENSE](https://github.com/HBAI-Ltd/Toonflow-app/blob/master/LICENSE) |
| ComfyUI | /prompt入队、prompt_id、队列/历史/中断和WebSocket事件；具体媒体能力取决于节点/模型 | 继续作为可选执行器。不能由queue/history推导跨进程持久恢复，也不应另建项目内容真相。[官方服务接口](https://docs.comfy.org/development/comfyui-server/comms_routes) |
| Kdenlive | 缺失文件定位、自动备份、代理预览和原素材导出、归档项目并收集媒体 | 对本项目最直接的是重新链接素材、收集当前项目、可靠恢复。可学习产品行为，不必引入其版本/备份界面。[文件恢复](https://docs.kdenlive.org/en/user_interface/menu/file_menu.html)、[代理](https://docs.kdenlive.org/en/project_and_asset_management/project_settings/proxy_settings.html)、[归档](https://docs.kdenlive.org/en/project_and_asset_management/file_management/archiving.html) |

这组资料支持一个产品判断：**最值得投入的是让用户修改已有作品时少损失成果、少重做、准确看到变化。** 当前“剧本/镜头/成片＋资产＋Agent”的方向可以继续承载这些能力。

开源复用建议：ViMax当前MIT，可对明确小模块评估移植；Toonflow当前完整LICENSE含附加条款，应作为方法对照，不能沿用旧报告的纯Apache判断。ComfyUI/LTX已有集成边界继续保留。源码开放、产品成熟和适合直接移植是三件需要分别验证的事。[ViMax许可](https://github.com/HKUDS/ViMax/blob/main/LICENSE)、[Toonflow当前许可](https://github.com/HBAI-Ltd/Toonflow-app/blob/master/LICENSE)

## 6. 建议的改进顺序与验收方式

| 顺序 | 具体工作 | 完成后应看到的结果 |
| --- | --- | --- |
| 第一批：内容与任务正确性 | 取消状态竞态、未使用素材阻塞、剧本草稿、重连保留工作区 | 取消确认后不会继续提交；无关素材不挡导出；切页/恢复不丢输入 |
| 第二批：修改反馈与后期交付 | 编辑器应用Agent新修订、导出状态恢复/取消、声音峰值与包络、角色声音入口 | Agent改完立即可见；能跟踪本次导出；叠音不过载；角色声音可试听调整 |
| 第三批：降低真实返工 | 针对复杂动作的视听观察；沿用实际帧；按缺陷选择最小修复；当前路线状态直接交接 | 问题定位到片段与时间，保住可用部分，减少重复读取/编译/生成 |
| 第四批：补常用创作与迁移 | 简单标题/转场、帧率、跨集影响范围、缺素材重定位/收集项目 | 常用成片包装能完成；项目可移动并可修复媒体关系 |
| 单独评估的扩展 | 一种保留原视频的生成编辑能力，依据高频失败选择Provider | 同一输入、同一修改目标下，实际更好地保留已有画面，代价可记录 |

后续验证建议使用六个小而真实的代表任务：单集完整短剧、双人连续对白、复杂接触/动作片段、已有片段定向修改、跨集共享资产修改、保存/断线/取消/导出恢复。先复用已有素材验证确定性操作；确需模型生成的项目再单独实跑。本次研究没有启动这些任务。

每次保留原始目标与实际产物，记录：最终可用性、采用素材占比、返工次数与范围、人工介入原因、墙钟时间、语言/Tool次数和实际Provider用量、暂停恢复结果。局部后期与整片生成分别比较；并发累计耗时不相加当墙钟；生成成功率不等于成片合格率。

已有Fake和源码检查适合验证正确性；实际视频播放与用户观感决定成片质量。方法和Prompt应围绕具体失败持续修改，避免用增加角色、增加方法数量或扩大上下文代替效果验证。

## 7. 研究附件与边界

- [H3取消竞态脚本](/Users/liyifan/Documents/ChatGPT/ai短剧调研/output/system-maturity-review-2026-09-15/reproduce-h3-cancel-during-prepare.py)及[结果](/Users/liyifan/Documents/ChatGPT/ai短剧调研/output/system-maturity-review-2026-09-15/reproduce-h3-cancel-during-prepare-result.json)。
- [未使用媒体缺失脚本](/Users/liyifan/Documents/ChatGPT/ai短剧调研/output/system-maturity-review-2026-09-15/reproduce_unused_missing_media.py)及[结果](/Users/liyifan/Documents/ChatGPT/ai短剧调研/output/system-maturity-review-2026-09-15/unused-missing-media-reproduction.json)。
- 当前产品事实与历史案例，以[交付清单](/Users/liyifan/Documents/ChatGPT/ai短剧调研/docs/product-delivery-checklist.md)、[已落地改造](/Users/liyifan/Documents/ChatGPT/ai短剧调研/docs/research/2026-09-11-mature-video-production/09-implementation-delivery.md)、[第六轮记录](/Users/liyifan/Documents/ChatGPT/ai短剧调研/docs/research/2026-09-12-generation-quality/70-sixth-fresh-pre-video-end-to-end.md)、[第七批修复](/Users/liyifan/Documents/ChatGPT/ai短剧调研/docs/research/2026-09-12-generation-quality/72-seventh-pre-video-consolidation.md)交叉核对。
- 两份复现只证明对应确定性缺陷。其他源码发现未冒充UI实测；旧真实制作未冒充当前版本结果；官方功能未冒充同条件性能结论。
- 当前本地包的签名、公证、Windows/Linux与公开分发仍是后续发布项；本次没有扩大为企业协作、权限或安全体系设计。
