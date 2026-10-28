# 06 开源、论文与技术博客：读机制、作比较、留下可验证的结论

[返回学习地图](README.md)

本轮核对了一手论文、作者/维护方文档及官方工程博客。阅读清单服务于具体问题，不是“我看过很多框架”的名词表。先完成第 01–04 章，再按这里的问题阅读，最后用第 07 章闭卷检验。

## 1. 先说清历史借鉴与这次补课

| 来源 | 仓库里有无历史借鉴记录 | 可以怎样表述 |
| --- | --- | --- |
| Codex compact.rs、OpenAI Agents SDK CompactionSession、官方 compaction/memory 示例 | 有，[Harness 文档](../agent-harness-context-and-lifecycle.md)明确列出 | 借鉴意图保留、输入整理、失败恢复与工件保存；没有把服务端 compact 作为必需依赖 |
| LangGraph Persistence | 有，同上 | 借鉴检查点与恢复观念，当前运行时仍是自有 SQLite 模型 |
| Anthropic Context Engineering、子任务上下文 | 有，同上及素材 Worker 记录 | 借鉴按需装配、隔离上下文和轻量引用，不等于复制整个 SDK |
| AutoGen Core、Dask Futures、Temporal 消息处理 | 有，[增量规划文档](../incremental-worker-planning.md)逐项解释 | 借鉴消息身份、按完成顺序消费、短状态提交与异步交错处理 |
| Anthropic Writing Effective Tools | 有，[素材 Worker 优化](../research/2026-09-12-generation-quality/25-asset-worker-context-efficiency.md)明确引用 | 围绕真实 Trace 调整初始合同与返回内容 |
| MiniMax H3 公开方法与仓库保留的制片资料 | 有，[Skills 重构说明](../agent-skills-and-model-redesign.md)与[来源入口](../../backend/short_drama/skills/README.md) | 按用途、角色和阶段做本地适配，保留出处；方法导入不等于软件接口已接通，更不等于复用了私有 Agent 引擎 |
| ReAct、Reflexion、Generative Agents、MemGPT、Voyager、LongMemEval | 本轮未找到已据此实现完整算法的证据 | 这次用来解释、对照与设计扩展，不写成当时采用过的技术路线 |
| τ-bench、τ²-bench、SWE-agent、OSWorld、2026 年 Agent 评测/长任务文章 | 本次扩展阅读 | 用来补评测、游戏迁移和接口设计，不计入项目已经实现的能力 |

历史研究文件曾保存到 output 上游目录，当前检出没有那些原始快照。下面指向公开原始来源；GitHub `main` 与滚动文档代表本轮查阅时版本，不保证等于 9 月研究时的每一行代码。

## 2. 第一组：先把长程执行与恢复读懂

### A1. Codex compact.rs 与 OpenAI Agents SDK compaction

来源：[Codex compact.rs](https://github.com/openai/codex/blob/main/codex-rs/core/src/compact.rs) · [SDK CompactionSession](https://github.com/openai/openai-agents-python/blob/main/src/agents/memory/openai_responses_compaction_session.py) · [官方 Memory/Compaction 示例](https://developers.openai.com/cookbook/examples/agents_sdk/building_reliable_agents_memory_compaction)

阅读重点是“替换会话状态”这个动作：保留什么用户信息、怎样拼回运行所需上下文、替换失败如何处理并发与旧状态。SDK 的候选条数策略与本项目完整 token 预算不是同一个触发条件；官方示例也区分上下文、跨运行经验和权威工件。

对照本项目的 `ModelInputCompiler.prepare`：本地先保存精确大结果，再置换为 context_ref；Provider 拒绝时输入必须改变才重试。不要用“就是调用 compact”概括它。

**读完交付：**画出压缩前后哪些信息仍在模型输入、哪些在外部存储、谁能回读；回答“摘要错了但原文还在，任务仍可能怎样失败”。

### A2. LangGraph Persistence

来源：[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)

重点看 thread 内检查点与跨 thread store 的区别，以及持久实现与 RAM 示例的区别。框架提供恢复结构，不会自动知道你的 H3 请求是否在远端已创建。

**与项目比较：**WorkOrder/Turn 检查点解决继续执行；ExperienceMemory 解决跨任务经验；ProjectDocument 负责当前内容。三者都存数据库不表示三者是一份状态。

**读完交付：**列出换用 LangGraph 后可能减少的胶水代码、仍必须保留的领域动作身份/来源校验，以及迁移旧 Trace 与工单的成本。不能只答“框架更成熟所以应该重写”。

### A3. AutoGen Core 的消息通信

来源：[Message and Communication](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/framework/message-and-communication.html)

先区分请求响应与发布消息，观察类型化消息如何交给运行时处理。通信机制并不会替你定义任务所有权、业务完成条件或消息重复时的动作语义。

**读完交付：**把“某 Worker 完成”设计为只含身份和落盘结果引用的消息，再说明收到两次、收到旧计划消息、收到顺序颠倒消息分别怎么处理。对照项目的完成结果消费与 `active_plan_id`。

### A4. Dask Futures

来源：[Futures，重点阅读 Waiting on Futures / as_completed](https://docs.dask.org/en/stable/futures.html)

关注按完成顺序消费结果，以及迭代期间加入后续任务。它帮助理解增量执行为什么不应被整批等待拦住；Dask 不是 LLM 推理或角色一致性的解决方案。

**读完交付：**画 A、B、C 三个任务，其中 D 只依赖 A；比较等待全部结束和 A 完成就派 D 的时间线。然后加一个会占住槽位连续执行的 A，解释为什么还需要公平调度。

### A5. Temporal 消息处理

来源：[Handling Signals, Queries, & Updates](https://docs.temporal.io/handling-messages)

重点是异步处理器可能交错、去重身份的范围与退出前处理器完成条件。不同消息种类、不同 run 的去重保证不同，不把某个 SDK 接口的保证扩大为所有外部请求的 exactly-once。

**读完交付：**在“用户纠错、旧 Worker 返回、后台规划返回”三条并发线上画出允许持锁的短区间。指出本项目的消息应用与 ACK 不是一个覆盖远端模型调用的总事务。

## 3. 第二组：上下文、工具与长期运行的工程文章

### B1. Effective Context Engineering for AI Agents，2025

来源：[Anthropic 原文](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

把注意力放在高价值信息、按需获取、结构化笔记及隔离子任务上下文。压缩是其中一种手段，组织好下一次决定所需的证据更关键。

**本地对照：**同一读取工具对不同镜头不能只保留最后一次；精确相同引用正文可以共享，但不同来源、版本、数组顺序不能粗暴去重。

**练习：**从第 04 章的一次 81k 输入归因表解释：为什么首先删 Skills 不是最有依据的优化。

### B2. Writing Effective Tools for Agents，2025

来源：[Anthropic 原文](https://www.anthropic.com/engineering/writing-tools-for-agents)

关注工具职责、参数可理解性、返回信息与实际 Agent 评测。能通过 Python 类型检查的 API，仍可能让模型选错粒度或反复发现合同。

**本地对照：**把保存过的引用说成“待准备”、把完整扫描范围缩成空回执、把不支持空间 crop 藏在长描述尾部，都能触发无用动作。

**练习：**给一个只换镜头画面的工具写说明、输入与回执；必须明确声音是否保持、实际保存了什么、来源是否仍有效。比较“整份项目更新”合同为什么更难正确调用。

### B3. Effective Harnesses for Long-running Agents，2025

来源：[Anthropic 原文](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)

阅读初始化环境、分段推进和跨会话交接如何配合。长任务不能只依靠不断续写聊天；下一段执行者需要知道完成情况、环境状态和下一项可验证工作。

**本地对照：**把持久工单、working_notes、已完成回执和项目当前内容分别映射过去。不要默认每个创作目标都应被固定成相同阶段列表。

**练习：**给一个新的 Worker 只提供半页交接，要求它能继续指定局部补拍；另列精确引用，确保原始证据仍可读取。

### B4. Harness Design for Long-running Application Development，2026-03

来源：[Anthropic 原文](https://www.anthropic.com/engineering/harness-design-long-running-apps)

这篇扩展到 planner/generator/evaluator、结构化交接和主观质量评分；也讨论重置上下文与原位压缩的取舍。值得比较，不意味着本项目应该新增固定三角色循环。

**练习：**设计“什么时候必须独立检查，什么时候复用已有检查”的规则，并给检查调用计成本。回答“换一个上下文重新开始”如何避免丢掉用户最新纠错。

### B5. How We Built Our Multi-agent Research System，2025

来源：[Anthropic 原文](https://www.anthropic.com/engineering/multi-agent-research-system)

关注可分解任务、独立上下文、委派目标与交接，以及协调开销。研究型任务的并行优势不能直接外推到共享状态、强顺序的游戏动作链。

**练习：**把项目任务分成适合并行和不适合并行两栏；每个分类给出依赖或共享写入理由。论文/博客自身的收益数字不写成本项目实验成绩。

### B6. Towards a Science of Scaling Agent Systems，2026 年 v3

来源：[论文](https://arxiv.org/abs/2512.08296v3)

重点看控制模型、工具、预算和协作拓扑的方法，以及不同任务结构下结果如何变化。研究报告既有正收益也有明显退化，不能抽一个最优数字为“多 Agent 一定更强”背书。

**练习：**设计本项目单 Agent 与多 Agent 的公平对照，至少控制模型配置、可用信息、工具集合、总预算和终态评分。解释预测模型的拟合能力与范围为何也要报告，不能当普适定律。

### B7. Agent Skills：分层发现与可组合知识

来源：[Anthropic Agent Skills 文章](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) · [Agent Skills 公开规范](https://agentskills.io/specification)

这组是本次补充阅读。关注元数据、正文和额外资源的分层披露，比较知识目录规模与单次上下文规模。公开规范中的可选 scripts/references/assets，不等于本项目已提供通用文件执行工具。

**本地对照：**[01C](01c-skills-management.md) 的自有 manifest 按方法 ID、角色和阶段编译片段；磁盘解析缓存与模型按需注入是不同层次。[01D](01d-progress-and-next-action.md) 则说明进度由持久状态接续，不应混进全局方法正文。

**练习：**为同一条经验决定应放在 method、runtime、Provider 适配、Tool 回执还是当前 working_notes，并解释为什么。然后模拟旧工单、方法文件修改、进程重开，回答选择 ID 与实际正文是否可能分别保持和变化。

## 4. 第三组：游戏角色、记忆与自主决策的论文

### C1. ReAct，2022/ICLR 2023

来源：[论文](https://arxiv.org/abs/2210.03629)

要掌握推理—动作—观察的交替如何让计划受环境反馈修正。它描述一种推理与行动组织方式，不等同于持久任务系统，也不自动解决副作用恢复。

**练习：**给项目 Worker 的每一环配上程序状态，再插入“Tool 执行成功但响应格式坏了”。解释仅靠 ReAct 循环为什么可能重复动作。

### C2. Reflexion，2023

来源：[论文](https://arxiv.org/abs/2303.11366)

学习如何把反馈形成可复用的语言经验，而非只在当前回答里自我批评。反思错误也会污染后续尝试，不能把所有失败都总结成普遍规则。

**练习：**把“参考图被 Provider 拒绝”写成一条带证据、适用范围和未知项的经验，避免写成“所有这类图都无法生成”。说明与微调、规则引擎和固定 Critic 循环的区别。

### C3. Generative Agents，2023

来源：[论文全文，重点 Architecture、Evaluation 与 Ablations](https://arxiv.org/html/2304.03442v2)

关注观察、检索、反思与计划如何串起来，而不是只记住 25 个小镇角色。对第 03 章的 NPC 记忆设计，重点比较相关性、重要性、时间与角色已知信息。

**练习：**同一事件让两个角色只观察到不同部分，分别写它们的记忆与下一句话；再由玩家提供假消息，解释为什么信念可以不同于世界真相。

### C4. MemGPT，2023

来源：[论文](https://arxiv.org/abs/2310.08560)

关注分层记忆与显式搬运如何管理有限上下文。外部记忆更大只是存储能力增强，不是模型推理获得无限可靠上下文。

**练习：**比较本项目 context_ref 精确回读与自主记忆管理：谁决定移出、谁决定取回、什么不能被摘要覆盖、怎样发现遗漏导致的错误。

### C5. Voyager，2023

来源：[论文](https://arxiv.org/abs/2305.16291)

重点是自动课程、可执行代码技能库、环境反馈及迭代修正。它的技能是可执行行为知识，本项目的方法库主要是按角色/阶段加载的制片方法，不能因为都叫 Skill 就说实现相同。

**练习：**设计游戏里的一个“从安全地点取回指定物品”技能。写明参数、前置条件、执行成功证据、失败退出与复用范围，再说明是否需要代码生成，为什么。

### C6. LongMemEval，2024/ICLR 2025

来源：[论文全文](https://arxiv.org/html/2410.10813v2)

把五类记忆能力与索引—检索—阅读三个环节结合起来读。特别关注知识更新、时间推理及无证据时不乱答，避免只测名字记忆。

**练习：**为同一 NPC 编五个跨会话问题，分别需要信息提取、多会话综合、时间判断、新事实覆盖和拒答；每题标出最小 gold evidence 与禁止使用的旧事实。

### C7. RAG 与 HNSW：补齐检索基本功

来源：[RAG](https://arxiv.org/abs/2005.11401) · [HNSW](https://arxiv.org/abs/1603.09320) · [hnswlib 参数](https://github.com/nmslib/hnswlib/blob/master/ALGO_PARAMS.md)

区分生成模型利用检索证据，与索引如何找到近邻这两个层次。先用精确近邻建立召回基线，再讨论 ANN 的速度、内存与准确率取舍。

**练习：**解释 k、M、ef_construction、ef；举出一个向量相似度很高却必须排除的旧记忆。再设计 BM25 与向量各能补上对方哪类漏召回。

## 5. 第四组：从任务效果倒推 Eval

### D1. Demystifying Evals for AI Agents，2026-01

来源：[Anthropic 原文](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)

重点是 task/trial/trace/outcome 的区别，以及代码、模型、人工 grader 的职责。对 Agent 应检查真实环境结果，避免把标准工具轨迹当唯一正确解。

**练习：**为“保留全部声音，只换第 3 镜画面”写一个结果评分器：哪些逐字段判断，哪些用真实解码，哪些仍需人看/听。不同层次不得合成一个含糊 PASS。

### D2. τ-bench，2024；τ²-bench，2025

来源：[τ-bench](https://arxiv.org/abs/2406.12045) · [τ²-bench](https://arxiv.org/abs/2506.07982)

先理解工具、对话和规则共同影响任务结果，再关注用户也能改变环境时的通信与协调。对本项目最直接的扩展是把人工编辑插入 Agent 思考与工具执行之间。

**练习：**写三个并发事件顺序，让同一个旧响应在一种顺序里有效、另一种顺序里被拒绝、第三种顺序里只允许保存为历史候选。计算 p=0.9 时三次至少成功一次与三次全部成功的差别。

### D3. SWE-agent，2024；OSWorld，2024

来源：[SWE-agent](https://arxiv.org/abs/2405.15793) · [OSWorld](https://arxiv.org/abs/2404.07972)

前者适合思考 Agent 计算机接口设计；后者适合理解真实软件环境、环境重置与执行结果评分。两者都不支持仅凭“能调用 shell/点击按钮”就声称有成熟 Agent 产品。

**练习：**把一个“找到字幕并修改”的任务分别设计成领域 API、浏览器 DOM、截图坐标三种动作空间。比较观测失效、定位成本、结果确认与恢复机制。

## 6. 阅读顺序与面试前必须留下的材料

先读 A 组，用项目代码讲通恢复；再读 B1/B2/B5，理解调度、工具与上下文成本；随后读 C3/C6，补 NPC 与记忆；最后读 D 组设计评测。其余论文用于加深某个具体问题，不必一天堆完所有标题。

每个阅读主题只留下四项自己的材料：一张运行图、一个反例、一段与当前项目的差异、一个可验证的实验。不要摘抄整篇原文。面试官问“从开源学到什么”，按下面句式回答：

> 我在 X 中看到机制 A，它针对问题 B。项目里遇到的具体表现是 C，所以采用了本地实现 D，而不是把整个框架迁入。用 E 验证了这个范围；F 仍未验证。后来又看了 Y，它给我一个尚未实施的改进方向 G。

例如：从 Dask 的完成顺序消费理解局部结果可以提前释放后续工作；在本项目中落到完成 ID、增量计划和继承归属；之后仍遇到 Worker 槽位公平性，说明消费完成事件与公平运行是两个层次。这样的回答比列出五个框架名更能展示理解。
