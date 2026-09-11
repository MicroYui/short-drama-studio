# 开源视频制片实现：哪些可以拿，哪些不能整体搬

调研日期：2026-09-11。范围：官方仓库、当前源码、提示词、工作流与许可；没有安装依赖、运行外来代码、调用媒体 Provider，或替这些项目做真实成片验收。下文的“已实现”指看到了相关源代码及调用位置，不等于在本机验证过效果或无人值守稳定性。

## 结论

没有找到一个经公开证据证明“高质量、低返工、快、可靠”同时成立、且可原样替换本产品的开源短剧系统。最有价值的不是再加几位导演/编剧 Agent，而是挑选具体机制：

1. **ViMax 的相同任务内持续保留 Tool schemas、参考图选择、机位依赖与就绪镜头并行执行。** 不搬它的整体循环、强制工作流确认或每换机位先生成一段过渡视频。
2. **Toonflow 的分任务专业知识和资产状态衍生表达。** 不搬固定六阶段、逐段确认、强制监督层、一次读取后禁止再读、依赖前端 socket 的状态真相。
3. **MiniMax 官方 H3 输入编译规则。** 本项目已经吸收了大部分核心结构；应检查真实请求是否遵守，补一套可执行样例和失败定位，而不是再次全文注入。
4. **NarratoAI 的局部确定性校验和带具体错误的有限修复。** 对时间、字幕、来源 ID 等技术合同有价值；它的解说节奏规则不能成为所有短剧的硬门槛。
5. **MoneyPrinterTurbo 的素材缓存、音频预览复用、以实际音频文件定时长。** 可以减少确定性环节的重复劳动；它主要解决素材型视频，不能据此推断多角色剧情视频质量。

本项目已经有 Typed ToolRegistry、精确工件引用、图像观察、参考重排、关键帧比较、输入兼容性检查、导演修复、视频检查、ASR 字幕对齐、持久任务与四项并行。问题不能简单归因于“没有这些 Tool/Skill”。下一步应先证明它们在真实轨迹里被正确、及时、低开销地调用。

## 仓库与复制边界

以下 commit 是本轮只读获取源码的定位点，不构成文档冻结机制。普通更新继续以新结论覆盖旧结论。

| 项目 | 本轮源码定位 | 已看到什么 | 许可及直接复用判断 |
| --- | --- | --- | --- |
| [ViMax](https://github.com/HKUDS/ViMax) | `05a48943878312d88fe5a016c12a9654940ecc43`，2026-07-29 | 论文系统之外已有 agent loop、TUI/WebUI、会话与产物目录、镜头流水线 | [MIT](https://github.com/HKUDS/ViMax/blob/05a48943878312d88fe5a016c12a9654940ecc43/LICENSE)，保留版权及许可即可按条款改编；建议抽取机制 |
| [Toonflow 官方主仓](https://github.com/HBAI-Ltd/Toonflow-app) | `e03cf590eb0cab63534a4040db9acb4ec95b42a6` | Electron/后端、生产与编剧 Agent、Markdown 专业知识、供应商与 socket 工具 | 当前主仓 [Apache-2.0](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/LICENSE)；旧 fork 的 AGPL 说明不能代替当前主仓许可；保留实际复用文件的归属及 Notices |
| [MiniMax-H3](https://github.com/MiniMax-AI/MiniMax-H3) | `d21241f0a4b3acbb34c97dae47fa417b7065e438`，2026-08-15 | 五种 H3 模式的官方编译指南、八类制片 Skill；不是 MiniMax Design 私有应用源码 | README 指向 [H3 Community License](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE)，不是 MIT/Apache；本轮未发现 Skills 独立的宽松许可声明，不能自动按宽松开源素材再分发 |
| [火宝短剧](https://github.com/chatfire-AI/huobao-drama) | `ff9b046f8950e16e131aa13b016c11269d9ed6ca`，2026-09-11 | Nuxt/Hono/Mastra/SQLite/Electron；四类 Agent、可编辑 skills、批任务 | [CC-BY-NC-SA 4.0](https://github.com/chatfire-AI/huobao-drama/blob/ff9b046f8950e16e131aa13b016c11269d9ed6ca/LICENSE)；非商业限制，不能作为商业产品直接复制候选。可研究思路后独立实现 |
| [NarratoAI](https://github.com/linyqh/NarratoAI) | `8c6dd58185de44afa2aa2bd7ffce86d7a2b78f86`，2026-09-07 | 影视/短剧解说、字幕、配音、剪辑；细分 Prompt 管理与局部校验 | [MIT](https://github.com/linyqh/NarratoAI/blob/8c6dd58185de44afa2aa2bd7ffce86d7a2b78f86/LICENSE)，可按条款抽取功能；不建议整体替换本项目领域模型 |
| [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) | `436b0e9cc830ef7639e33388917e389cf30d3307` | 素材检索/生成、TTS、字幕、剪辑任务，及缓存和任务文件维护 | [MIT](https://github.com/harry0703/MoneyPrinterTurbo/blob/436b0e9cc830ef7639e33388917e389cf30d3307/LICENSE)，适合具体素材/音频/组装模块 |
| [MovieAgent](https://github.com/showlab/MovieAgent) | `d84041bed8bc8be460664528d9e9924cdde384ba`；API 返回最近 push 为 2025-03-26 | 论文配套的脚本→场→镜头推理、模型桥与评价 ToolBox | 官方 GitHub license 元数据为空；本轮目录未找到顶层通用 LICENSE。不能据“公开代码”推断可直接商用复制，当前只推荐方法参考 |

许可这里仅用于筛选能否直接拿代码/正文，不扩展为全面合规审计。H3 API 服务条款、模型权重条款和文档再分发范围不能混为一谈。当前项目保存的 MiniMax Design 3.0.11 workflow 来自安装包，其再分发边界应独立确认；公开 H3 仓库不会自动授权复制 Design 私有前端。

## ViMax：最值得拆开吸收的机制

### 1. Tool 定义在一次工作中持续可见

[`AgentLoop.stream_events`](https://github.com/HKUDS/ViMax/blob/05a48943878312d88fe5a016c12a9654940ecc43/agent_runtime/loop.py#L44) 在 turn 开始获得 `tool_schemas`，循环中的每次 `llm.complete` 复用同一份定义。返回 Tool 结果后直接继续决策，不插入一次“再发现同一个 Tool”的模型请求。

本项目工具更多，适合借用的是生命周期：**WorkOrder 相关的 schema 在本工作项内保持可用；新能力按需扩充；完成/切换范围时释放。** 不需要照抄全目录常驻，也不需要引入新的 Agent 框架。成本低，应优先于增加新专业角色。

不能整体照抄：该 loop 每轮将 Tool result JSON 追加到模型消息，普通循环还是按 Tool 顺序执行；其 `partition_calls` 存在于 registry，不代表这个 `stream_events` 已经并行执行一批 Tool。上下文 token 估算也有 `len(text)//4`，不能替换本项目的 o200k 计数。

### 2. 参考选择与空间依赖是实际的生产数据

[`ReferenceImageSelector`](https://github.com/HKUDS/ViMax/blob/05a48943878312d88fe5a016c12a9654940ecc43/agents/reference_image_selector.py) 先在候选很多时做文字筛选，再把小集合送入多模态选择；优先当前角度、同机位、最近状态和非冗余图，输出选定索引与说明每张图用途的编辑 prompt。调用位置在 [`script2video_pipeline.py`](https://github.com/HKUDS/ViMax/blob/05a48943878312d88fe5a016c12a9654940ecc43/pipelines/script2video_pipeline.py#L400)。

本项目已有 `qa.rerank_reference_visuals`、ReferencePlan/ReferencePack。可补的是让选择结果明确回答：该图负责身份、空间、姿势、首尾状态中的哪一项；与其他图是否重复或冲突。只在候选多且选择困难时做额外一次视觉调用，不让每镜强制经历两轮筛选。

[`CameraImageGenerator.construct_camera_tree`](https://github.com/HKUDS/ViMax/blob/05a48943878312d88fe5a016c12a9654940ecc43/agents/camera_image_generator.py#L121) 表达机位覆盖关系、父镜头、缺失的可见信息，并做引用范围/自指/环检查。它让近景、反打镜头从已经成立的空间构图派生，减少每镜从头猜位置。

本项目当前已有动作、道具与角色连续性表达；本轮源码检索没有发现等价的 camera parent tree。可在现有 Shot/ReferencePlan 上加**可选空间锚点关系**，不需要另造项目状态或语义画布。对同场两人对话，先确定一个可用的双人空间锚点，再派生中近景与反打；无关系的镜头保持独立。

重要限制：ViMax 的子机位实现会先生成一段过渡视频，再从其中取新机位图。这会增加付费操作与关键路径时间，也可能传播上游缺陷。优先试验本项目现有图片派生/裁切/参考选择，只有确有增益才引入视频辅助机位。

### 3. 沿真实依赖交错执行

[`Script2VideoPipeline.__call__`](https://github.com/HKUDS/ViMax/blob/05a48943878312d88fe5a016c12a9654940ecc43/pipelines/script2video_pipeline.py#L191) 同时启动机位关键帧任务和各镜视频任务。视频任务等待自身首帧；需要时再等尾帧，就绪即提交，不等待全片关键帧全部完成。相关位置：[`generate_frames_for_single_camera`](https://github.com/HKUDS/ViMax/blob/05a48943878312d88fe5a016c12a9654940ecc43/pipelines/script2video_pipeline.py#L323)、[`generate_video_for_single_shot`](https://github.com/HKUDS/ViMax/blob/05a48943878312d88fe5a016c12a9654940ecc43/pipelines/script2video_pipeline.py#L491)。

可迁移目标是现有持久化 WorkItem 调度：**一个生成组的真实输入齐全即进入 Provider 队列；其他组可以仍在写分镜或补资产。** 本项目已有四项并发，先检查实际计划是否仍人为按全阶段串行分派。不能把内存 `asyncio.Event` 当成崩溃恢复方案，也不应取消现有 Provider 对账。

### 4. 评价代码的反例也很重要

[`BestImageSelector`](https://github.com/HKUDS/ViMax/blob/05a48943878312d88fe5a016c12a9654940ecc43/agents/best_image_selector.py) 给出身份、空间、目标符合度的比较维度；但它要求总选一个相对最好项，索引无效时回退第 0 张。本轮全仓 Python 检索只找到该类定义，没找到主流水线调用。因此不能宣称当前 ViMax 成片路径已经自动做候选图验收。

本项目已经允许全拒绝，这一点应保留。新增质量评估应返回**具体问题、所在时间/位置、可用与否、最小修复对象**，而不只给一个分数或一个最佳索引。

## Toonflow：可用的专业知识和不适合搬的运行方式

### 值得改编的文件

- [`storyboard_table_techniques.md`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/data/skills/production_skills/storyboard_table_techniques.md)：镜头是否真的需要拆、动作与视线接续、空间朝向、单镜事件密度、剪辑余量。将数字阈值作为体裁/模型经验建议，保留用户的节奏与风格决定权。
- [`storyboard_prompt_techniques.md`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/data/skills/production_skills/storyboard_prompt_techniques.md)：将文字分镜转成具体画面、建立多角色站位和构图表达；适合补充关键帧 brief 的成功/失败示例。
- [`production_execution_derive_assets.md`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/data/skills/production_execution_derive_assets.md)：分清人物/场景基础身份和本场状态变化。与本项目 canonical/base 派生机制重叠，优先借用更具体的例子，不再建第二套资产系统。
- [`skillsTools.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/utils/agent/skillsTools.ts#L180)：工具加载技能正文及资源；[`productionAgent/index.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/agents/productionAgent/index.ts#L378) 按项目 art/story 组合知识目录。本项目已有 role/stage manifest，只有被真实失败证明需要的知识才补充；无需再复制一套 loader。

### 不搬整体 Agent 架构的原因

[`production_agent_decision.md`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/data/skills/production_agent_decision.md) 把制作规定为六阶段，多处要求用户确认，并对部分阶段自动调用监督层。它会增加本用户希望减少的交互和往返。

[`production_execution_storyboard_table.md`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/data/skills/production_execution_storyboard_table.md) 要求一次读完后禁止再读、一次性输出完整分镜表，并嵌入固定语速、长台词阈值、禁止光影和配乐等要求。甚至 [`production_agent_supervision.md`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/data/skills/production_agent_supervision.md) 按新字段口径不再检查站位/视轴连续性。专业规则多不等于规则一致或画面质量高。

[`productionAgent/tools.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/e03cf590eb0cab63534a4040db9acb4ec95b42a6/src/agents/productionAgent/tools.ts#L88) 的 `get_flowData` 通过前端 socket callback 获取数据；本轮检查到的 Promise 没有就地超时。部分生成调用立即报告启动，将结果依赖交给前端。这与本项目“后端 ProjectDocument 是唯一真相、断线可续”的方向不相同。可研究交互表达，但不建议搬来解决可靠性。

## MiniMax-H3：优先核对编译事实，别再叠 Skill

官方 [`h3-prompt-writing/SKILL.md`](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/SKILL.md) 按输入关系选择 T2VA/I2VA/FL2VA/L2VA/Ref2VA，再按模式读单份参考。最有用的是 [`base-en.txt`](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/references/base-en.txt) 与 [`ref-en.txt`](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/references/ref-en.txt)：

- 画面/动作/摄影机/声音按播放时间描述，而不是讲剧情梗概；对白保持原语言。
- 区分上传文件标签与实际复用的主体；人物照片不自动变成视频首帧。
- 逐项说明保留/转移关系，区分复制原音频和仅参考声线/节奏。
- Ref2VA 的六段输入关系一致；说话人全局编号与音频来源一致。

本项目 `skills/providers/minimax_h3/references/reference_mode.md` 已有六段、Subject/Picture 区分、保留/转移/排除项；`common_compilation.md` 等已负责编译。可直接推进的是**把现有校园案例每个实际上传图片、编译标签、镜头动作、对白时长、输出问题对齐检查**，找发生在哪一层；不要把上游全文再塞给 Leader/Worker。

官方 [skills README](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/README.md) 明确八个风格 Skill 为 Hub 画布工作流设计。角色/场景卡、分镜方法和检查清单能改编；Hub Tool 名、画布操作、确认门和模型回退不直接迁移。模型训练/推理目录也不解决本产品当前的交互/恢复缺陷，无需为本次目标集成权重。

## NarratoAI 与 MoneyPrinterTurbo：成熟的是更窄的执行问题

### NarratoAI：错误必须可定位，修复应有限且局部

[`short_drama_narration_validation.py`](https://github.com/linyqh/NarratoAI/blob/8c6dd58185de44afa2aa2bd7ffce86d7a2b78f86/app/services/short_drama_narration_validation.py#L301) 校验源视频、时间戳范围、重复 ID、片段重叠与配音文字密度，产出具体条目错误；[`script_repair.py`](https://github.com/linyqh/NarratoAI/blob/8c6dd58185de44afa2aa2bd7ffce86d7a2b78f86/app/services/prompts/short_drama_narration/script_repair.py) 让模型围绕原候选、错误和可用字幕窗口修复。它不是泛泛地再问一遍“请检查质量”。该校验还夹带固定开场/原声比例风格要求，这些不应移入通用系统。

更清楚的实际有限修复调用在 [`subtitle_translator.py:_translate_chunk`](https://github.com/linyqh/NarratoAI/blob/8c6dd58185de44afa2aa2bd7ffce86d7a2b78f86/app/services/subtitle_translator.py#L145)：分批保留字幕 ID，校验返回集合，失败只把本批、上次输出和错误交回，达到上限停止。本轮确认 `webui/components/script_settings.py` 调用了该字幕翻译服务；没有把单个 repair helper 的存在夸大成全制片自动恢复。

本项目已有大失败候选持久化、ToolAttemptRef 和局部重试。值得吸收的是把相同手法用于普通输出合同错误：先用代码查确定性问题，向原 Worker 返回最小缺项/冲突与精确位置；只有创作原因才升级 Leader 重新决策。别把所有格式、ID、时长问题都派给全新 Critic。

### MoneyPrinterTurbo：减少可以确定性避免的重复

[`task_artifacts.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/436b0e9cc830ef7639e33388917e389cf30d3307/app/services/task_artifacts.py) 使用同目录临时文件替换任务清单；[`material_cache.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/436b0e9cc830ef7639e33388917e389cf30d3307/app/services/material_cache.py) 让相同素材搜索跨入口/重启复用，并合并进程内相同搜索条件的并发请求。可以作为本项目 stock/source 搜索和可再生媒体元数据缓存的参考，不要用缓存直接认定创意输入未变。

[`task.py:generate_audio`](https://github.com/harry0703/MoneyPrinterTurbo/blob/436b0e9cc830ef7639e33388917e389cf30d3307/app/services/task.py#L481) 支持直接用已有音频，测量实际写出的音频时长，避免只拿最后一条字幕 cue 的终点当总时长；同文件还对完整 voice preview 做匹配后复用。此类改进能直接减少音画错位和重复 TTS。

本项目已有 MasterAudio、ASR/字幕对齐和现有媒体复用。先对照实际音频/导出故障补差异即可。不能为了“成熟”再搬整套 FFmpeg 管线、Streamlit 界面或任务模型。

## 火宝与 MovieAgent：重要的对照，但不能当替换底座

火宝的 [`video-prompts.ts`](https://github.com/chatfire-AI/huobao-drama/blob/ff9b046f8950e16e131aa13b016c11269d9ed6ca/backend/src/services/video-prompts.ts) 按镜头依次运行提示词 Agent，最终重新读库确认真实保存，这个完成判断值得参考。但其批任务是进程内 `Map`，源码注释明确重启丢失；每镜 `maxSteps=8` 不是持久恢复。相关 [`read_storyboard_context`](https://github.com/chatfire-AI/huobao-drama/blob/ff9b046f8950e16e131aa13b016c11269d9ed6ca/backend/src/agents/tools/storyboard-tools.ts#L110) 使用空输入 schema，读取整集剧本/资产；[`skills.ts`](https://github.com/chatfire-AI/huobao-drama/blob/ff9b046f8950e16e131aa13b016c11269d9ed6ca/backend/src/agents/skills.ts#L103) 按前缀把全部 Skill 正文拼入。这些设计不能解决本项目已知的上下文与往返问题。

MovieAgent 的 [`run.py`](https://github.com/showlab/MovieAgent/blob/d84041bed8bc8be460664528d9e9924cdde384ba/movie_agent/run.py#L122) 初始化编剧、场景规划、镜头规划三类 Agent，均可使用 `use_history=False`；[`system_prompts.py`](https://github.com/showlab/MovieAgent/blob/d84041bed8bc8be460664528d9e9924cdde384ba/movie_agent/system_prompts.py#L458) 将单镜的叙事作用、情绪、构图框、摄影和对白结构化。可以借鉴“在便宜的文本/构图阶段确定关键事实”，但本项目已经有对应职责与数据。

[`base_agent.py`](https://github.com/showlab/MovieAgent/blob/d84041bed8bc8be460664528d9e9924cdde384ba/movie_agent/base_agent.py) 主要是同步 LLM 调用和 JSON 解析；[`tools.py`](https://github.com/showlab/MovieAgent/blob/d84041bed8bc8be460664528d9e9924cdde384ba/movie_agent/tools.py#L245) 的生成链还存在注释掉的音频调用。评价工具列表包含 VBench 维度，不能推出主制作循环有逐镜自动质量闭环。公开论文/演示与稳定桌面制片产品之间仍有明显工程距离。

## 面向本项目的可搬清单与顺序

估算仅表示相对改动面，不承诺工期；不需要在调研阶段重构代码。

| 优先级 | 借鉴内容 | 在本项目落点 | 现有与新增边界 | 验证方式 |
| --- | --- | --- | --- | --- |
| P0 | 一次工作内持续保留所需 Tool schemas；结果回来直接执行下一动作 | `production_manager.py`、Worker request builder、工具发现缓存 | 修改生命周期；保持现有目录发现能力 | 从现有 Trace 回放看重复发现次数、实际动作/语言调用比、同工作项请求输入 |
| P0 | 确定性错误回原 Worker，给具体字段/ID/范围的局部修复 | ToolResult、ToolAttemptRef、现有保存/编译入口 | 已有大候选修复，先扩大正确使用而非增加 Agent | 原案例错误可在原工作项处理；不产生新全计划；一次修复成功率 |
| P1 | 参考选择解释身份/空间/动作/首尾职责，去掉冲突和冗余图 | `visual_input_quality.py`、`reference_planner.py` | 已有 Tool，补字段/样例或调用条件 | 选图数、身份/站位错误、输入规划→真实上传一致性；先只读旧案例 |
| P1 | 空间锚点的局部派生与跨镜连续性摘要 | Shot/ReferencePlan、关键帧 brief、资产派生 | 可选新关系；不造第二份内容真相 | 一个对话场的相同素材小对比；看身份/轴线/道具状态，不能仅凭审美分数 |
| P1 | 当前生成组就绪即提交，交错规划/资产/生成 | 现有 WorkItem DAG、四项并发、Provider worker | 优先修计划粒度与真实依赖；保留持久状态 | 关键路径、Provider 闲置等待、全阶段屏障、失败后无关组是否继续 |
| P2 | 实际音频时长、已有 preview/media 的可靠复用 | `media.py`、音频适配与时间线 | 对现有能力做差异检查 | 实际文件时长与时间线一致；字幕未结束就切走/尾静音导致的返工 |
| P2 | 从专业知识抽取十余个小型成功/失败示例 | 现有按角色/阶段加载的 Skill references | 少量补充，不复制所有章节/禁令 | 在固定旧问题上比较“原 prompt / 仅补示例”，保持模型与输入相同 |

建议的新能力应以现有领域类型表达。例如一次镜头验收的结果可以包含：`shot_id`、`usable`、`issue_kind`、`time_range`、`observed_fact`、`repair_target`、`suggested_action`。这只是待讨论的输出契约；优先核对现有 `video_inspection` / `DirectorAdvice` 是否已经足够，避免再建一套 QA 产品。

适合改写到本项目的短专业提示词（下述是针对本项目的建议文本，不是上游原文）：

> 先说明这个镜头必须让观众看见什么结果。保留当前剧本事实、说话人和人工修改。检查本镜开头、关键动作、结尾与相邻镜头的状态是否接得上。已有素材能承担的身份、空间和动作信息直接复用；只为确实缺失的部分申请新素材。若内容过载，提出最少的拆分；若结果有问题，指出实际观察到的位置与最小修复对象。没有观察到画面或声音时，不给相应质量结论。

这类提示词必须与实际 Tool、字段和输入对应。单靠加入“资深导演”“几十年经验”“严格自检”，没有证据能保证减少返工或缩短耗时。
