# Leader 已读正文丢失：真实机制、采用方案与验证

本轮只修复 Leader 的读取结果连续性。当前《一起签》运行仍是前置链路调试，恢复同一项目不计为新的稳定验收；没有调用真实 Provider、提交 H3 或修改生产项目数据。

## 真实问题

2026-09-12 02:57–03:00，北京时间，`project-5b8286dc63e6` / `studio-job-20f086971171` 的 Leader 在 r4 已执行 `artifact.read_current(scene-5441e73077d5)` 和 `artifact.get_production_document(production-document-4e543fd40d4a)`。之后只查询一次 H3 capability，r6 又要求读取同一场正文和分镜。此次恢复共 6 次语言请求、5 次 Tool，其中这两个读取各执行两次；实际供应商统计 146,421 input / 9,805 output tokens，219.46 秒。两位 Worker 没有新增 turn。Worker 调度饥饿由另一项调度修复处理，不能全部归因于上下文。

具体证据位于 `output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/`：

- `20260911T185748371840-52c0a288-model.input.json`：两个已成功读取的结果只剩 `recent_actions.summary`。场次读取在原始项目要求、索引处结束，未到正文；分镜读取在版本元数据处结束，未到 payload。`artifact-23/24` 有精确 ArtifactRef，但没有进入加载的 16 个工件正文。
- 对应 `model-f9e3fab7b8da` 输出请求 `agent.get_tool_attempt` 和 `route.h3_capability_profile` 的 schema。紧接的 `model-84074f888f77` 输入只增加这两份定义，因此这一对调用是正常工具发现，不是协议修复。其额外回读意图源于前述正文缺失。
- `20260911T185857760994-8b674067-model.input.json` 与 `20260911T185927817533-4c4266be-model.output.json`：capability 之后再次计划读取同一正文与分镜。

源码有三处共同原因：

1. `ProductionManager._execute_step` 只保存面向展示的结果摘要，普通结果先截到 1,600 字符。
2. `ContextCompiler._bounded_actions` 再将该摘要截到 1,000 字符。这种截取依赖 JSON 字段顺序，恰好先保留了外围元数据。
3. Leader 没有 Worker 已有的按对象读取工作集；工件候选先取旧 run/project memory，近期读取引用未优先进入前 16 项。`tool_attempt_refs` 原本是 Worker 的工单尝试引用，Leader 不能伪造一个 Worker 尝试来补救。

完整请求当时仅约 2.4 万输入 tokens，远未触发 258k 窗口整理，因此提高窗口或调整溢出阈值不能修复本问题。

## Primary 研究与本次取舍

Anthropic 的上下文工程方法强调保留最小但足够的高价值内容，并以精确引用在需要时读取外部内容。本次采用两者的组合：刚读到、当前仍需要的正文直接进入下一次决策；超出普通工作集预算的较早正文保留可分页引用。没有让每次已知读取都必须再多调用一次回读 Tool，也没有回灌整个项目或 Trace。[Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)。

Anthropic 的 Tool 设计文档要求返回与任务相关、可解释的内容，支持范围选择和分页，并用实际调用 transcript、正确性、耗时及 token 消耗检验改造。本次复用当前 Worker 的场次/镜头范围投影、文档单个版本 payload 投影和读参默认值归一化，不以生硬截断 JSON 代替结构化选择。[Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)。

MiniMax 公开 H3 Context IR 接受多模态内容并异步产出增强视频提示词，是 Provider 提示词编译能力，文档没有宣称它提供 Leader 工作记忆或 Agent 历史读取。这不能修复本轮本地数据丢失，也没有理由在每次 Leader 决策前增加一次付费 Context IR。现有可选适配继续保留原有边界。[Create H3-Context-IR Task](https://platform.minimax.io/docs/api-reference/video-generation-v2-h3-context-ir)。

## 实施

- `agent_runtime/leader_context.py` 在 Leader Tool 返回后、任何摘要截断前，将原参数和完整 typed result 保存到现有 `agent_context_snapshots`。Action 仅增加 `tool_result_context_ref`，不新建历史系统或改写旧 Trace。成功/失败最终状态保存按 ID 重读该 Action，保留引用。
- `ActionDigest.tool_result_ref` 提供 `agent.read_context` 的实际参数和 `/result` JSON pointer。短展示摘要与完整可回读数据分开；分页不会依赖摘要尾部恰好留下一个 ID。
- `LanguageProductionPlanner.propose_plan` 从持久 Action 快照构建读取工作集，直接复用 Worker 的读取身份和正文投影函数。不同场次、文档、镜头、视图、范围和分页分别保留；同范围新读取替换旧读取，schema 默认值显式/省略不产生重复条目。重新规划、进程重开和超过 8 次后续动作均不清空这些对象。
- 正常工作集按近期优先保留最多 32,000 tokens 的读取正文；超额记录保留原读参、来源 Action、观察时间、精确快照和分页入口。此数字仅是正文选择预算，整个请求仍经过原有 `ModelInputCompiler` 的实际 token 预算和可回读 offload。没有全局 last-N 截掉不同对象。
- `ContextCompiler` 在显式请求之后优先选取近期 Action 的工件，避免旧 memory 的前 16 项挤掉刚读取或写入的对象。同一对象优先最新观察到的引用；显式请求的不可变引用仍优先。
- 独立复核还确认了第 17 个已读对象的交接接缝：工作集正文可见，但旧校验仅认可 ContextPack 的 16 项摘要与 8 个近期动作。本次工作集另外提供所选成功读取实际返回的工件引用，Planner 仅将这组本次已见引用加入校验，避免为交接已知对象而再次回读。后续 Worker 的 manifest 仍由当前工件解析器验证，不扩大到整个项目，也不接受模型新造引用。

历史 Action 若已经只保存截断摘要，本次不会声称原文可恢复，也不扫描旧 Trace 虚构快照。旧任务在首次恢复后可能需要一次真实补读；修复后的读取才能持续保留。快照明确是观察时结果，当前内容若已改变，写入前仍需按原有当前内容/expected 字段检查。

## 验证与边界

新增 [test_leader_read_context.py](../../../backend/tests/test_leader_read_context.py) 使用真实 Service、Registry、PM `advance`、SQLite 持久化与 Fake Planner，覆盖：

- 20 个旧工件之后读取新场次和文档，经过 capability、新计划、10 个后续动作、重开 Service，下一次模型输入仍直接携带正文和最后一镜实际内容。
- 同 Tool 的不同对象、视图和分页保留；同范围新读取替换旧读取，显式默认值不增加副本。
- 普通预算不足时优先近期正文，旧正文可以经过实际 `read_context` 的多页读取逐字重建，Action 摘要后精确入口仍可达。
- 历史截断 Action 不伪称拥有完整可恢复数据。
- 17 个实际存在的文档、9 个后续 checkpoint 后，原先被挤出摘要区的已读引用可在一次 Planner 决策中直接交给 Worker；Worker context 继续解析实际保存版本。

另从当前生产 SQLite 以 `mode=ro` 读取 `ProjectDocument`，复制到临时库，用实际 Registry→PM 只执行该场次、该分镜和 H3 capability 三个本地只读 Tool，未在生产库创建 Action：场次正文 550 字逐字相等，分镜 payload 逐值相等。读取投影约为 1,925 / 20,666 / 963 tokens；含引用和参数的最终工作集约 24,509 tokens。把工作集加入同一历史 Leader 请求后，本地 o200k 估算从 24,109 增至 46,708 tokens；此前正文缺失，本次增加的是刚读取的必要内容，不能宣称单次输入 token 降低。

本地回归：新增读取工作集与原 tool-context、Runtime、完整 Production Manager 联合 210 项通过（122.98 秒）；随后补齐第 17 个引用的交接接缝，最终针对回归 18 项通过（18.59 秒，包括新 5 项、原 tool-context、不可用引用与 Decision 拒绝）。相关源文件及专测 Pyright 为 0 errors / 0 warnings，`git diff --check` 通过。独立只读复核检查最终 Action 状态保留引用、普通/增量规划入口、预算后回读和交接校验，没有剩余可复现阻断。

这验证了真实内容的输入连续性。恢复后的重复读取次数、全程耗时与模型是否正确利用信息，仍需下一次真实产品运行观察；同项目调试不代替用户要求的全新项目视频前验收。

## 当前目标的桌面显示

03:38 实际桌面核对发现，暂停卡片仍展示初始 `StudioJob.objective`（完整成片），而 AgentRun 和三个 Worker 的当前目标均已正确变为视频前准备。历史请求不能充当当前状态。公开的 Temporal Query 方法同样将“只读当前 Workflow 状态”与发送更新、保留历史分开。[Temporal Workflow message passing](https://docs.temporal.io/develop/python/workflows/message-passing)。

活动 API 现从最新任务关联的 AgentRun 读取 `goal_summary`，以可选 `current_goal` 投影给任务卡片；原始 objective、聊天和 Trace 均保留，不为显示修改运行数据。旧任务或尚无 AgentRun 时仍显示原始要求。五项既有活动/草稿回归通过，其中扩展原活动测试确认新的有效目标可见、原始 objective 不变且该只读投影没有写回持久任务。统一加载后再检查真实桌面。

03:55 运行 API 已返回当前视频前目标；03:57 在同一 Electron 窗口只读确认，暂停卡片显示该目标及不创建视频任务的边界，原始聊天中的完整成片要求仍保留。实际 UI 与 API 均已核对，没有通过修改旧聊天来修正显示。
