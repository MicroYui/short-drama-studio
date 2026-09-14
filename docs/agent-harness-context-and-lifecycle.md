# 制作助手：持续执行、上下文与子任务管理

2026-09-09。用户要求将右侧制作助手完善为可以根据目标持续工作的 Agent Harness。本轮在现有 AgentRun、ProductionManager、WorkOrder、ToolRegistry 上实现，不迁移整套框架。完整 Trace 和已生成的校园案例保留。

## 用户体验与完成边界

用户发出目标后，不需要逐步点击“继续”。只写剧本的目标在剧本完成时结束；要求完整视频时继续完成所需制作。新消息由 Agent 结合原目标判断是补充、局部修改、提问还是变更目标。暂停按钮让已经开始的调用完成并落盘，然后暂停后续动作。已保存的人工修改继续由内容修订检查保护。

任务内容、完整执行记录、当前模型输入分开管理。模型输入不是聊天记录的无限拼接，也不能因为压缩而把旧读取快照当成当前项目真相。

2026-09-11 Research 后续改造已接入同一 Harness：已发现 Tool ID 持久保存并从当前 Registry 重建定义；常用工具按角色加载；默认参数规范化后按对象、视图及分页范围保留读取。执行器使用持久 Future 池逐项回收，媒体与 Context IR 等待保存下次查询时间并释放槽位。审阅只阻塞相关 WorkOrder、对象和真实依赖；没有增设固定审批流程。详见 [Research 改造交付与验收](research/2026-09-11-mature-video-production/09-implementation-delivery.md)。

这次真实验收还修复了暂停确认的竞态：Runner 重入时不能在旧 Future 仍运行的情况下直接标为 paused。已开始的复合 Tool，包括其内部必要模型请求，完成并保存回执后才结束；下一次 Tool/决策才接收暂停。未开始的工具先检查执行边界再标为 IN_PROGRESS。确定性测试覆盖暂停重入、内部请求、任务完成瞬间与恢复，防止误报卡住或重复执行已完成写入。

2026-09-10 真实制作中，发现 `artifact.read_current` 的不同镜头读取仍按工具名互相替换，造成同一 Worker 交替回读两个镜头。已按项目、场次和镜头范围保存各自最新的读取结果；旧观察保留所选正文，项目索引与 Bible 不随每镜重复。使用同一真实 WorkOrder 重建输入后，第 5、6 镜和其他已读内容能够同时存在；22 项相关回归通过，重复读取不会累积旧正文。该修复不改变完整 Trace，也不把读取快照当成绕过当前保存校验的依据。

用户随后明确运行上下文上限为 **258,000 tokens**。计入运行提示词、实际工具 schema、当前工作输入，并预留本次 `max_output_tokens`（含推理）及 1,024 tokens 的消息封装余量。默认在输入达到 200,000 tokens 时整理至约 160,000 tokens，提前避开上限；不以固定字符数替代 token 计数。使用本地 o200k tokenizer 估算文本，供应商返回的实际 usage 单独记录；兼容代理的最终计数仍以供应商为准。若供应商另行拒绝上下文，重新整理后只重试一次，不能原样反复发送。

## 已阅读的成熟实现

2026-09-12 补齐部分分工完成后的增量规划：已落盘完成结果可以触发一次锁外 Leader 补充判断，同时继续其他 Worker。原步骤、Action 和在途媒体任务保留；同一结果不按 tick 或重启反复触发。实现、一手调度资料与验证边界见[按已完成分工补充计划](incremental-worker-planning.md)。实际总耗时和额外语言用量仍需新空项目验证。

| 来源 | 采用的机制 | 在本产品中的处理 |
| --- | --- | --- |
| [Codex compact.rs](https://github.com/openai/codex/blob/main/codex-rs/core/src/compact.rs) 与 [Agents SDK CompactionSession 实现](https://github.com/openai/openai-agents-python/blob/main/src/agents/memory/openai_responses_compaction_session.py) | 压缩后恢复当前运行上下文，保留真实用户意图；窗口超限后先改变输入再重试；会话替换失败时恢复旧数据 | 保留完整原始要求、当前目标摘要和最新消息；通过已落盘数据构建工作集；先保存原始上下文再替换为可回读引用。没有照搬 SDK 默认按消息条数触发的策略 |
| [OpenAI Compaction](https://developers.openai.com/api/docs/guides/compaction) 与 [Agents SDK Memory/Compaction 示例](https://developers.openai.com/cookbook/examples/agents_sdk/building_reliable_agents_memory_compaction) | 有限上下文、持久化会话、阶段交接保留必要状态，事实留在工件里 | 每次决策检查完整输入；大结果保存并按需读取，Worker 保存当前工作笔记。当前代理尚未验证 `/responses/compact` 支持，不把它作为必需依赖 |
| [Anthropic Context Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) 与 [Claude Agent SDK Subagents](https://platform.claude.com/docs/en/agent-sdk/subagents) | 按需获取信息、独立子任务上下文、简洁交接 | 沿用 WorkOrder 的独立上下文；控制并发和任务所有权，父任务获取当前进展与结果 |
| [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | 持久化检查点与执行恢复，已完成结果可继续使用 | 沿用 SQLite 中的计划、Tool 请求和结果；修复消息确认及暂停边界，避免重放已完成的媒体操作 |

以上是对公开源码、文档和示例实现的机制借鉴，未复制私有产品代码，也不引入这些框架的运行依赖。用于研究的公开源文件位于 `output/harness-context/upstream/`，不作为产品运行依赖。258k 是本产品按用户要求设置的运行预算，不用于推断所有 Codex 型号或服务的官方窗口与定价。

## 当前代码检查发现

1. `ContextPack` 已控制自身大小，Worker 已按对象归并工具历史；但整份 `current_plan`、WorkOrder 的重复 manifest、多个不同的读取结果和 schema 不在统一请求预算内。
2. 进行中的补充消息在重规划成功之前即从待处理队列清除，失败时可能失去待处理标记；失败后的新消息也可能另建根任务。
3. 产品入口优先处理已创建的 WorkOrder，容易让独立分工依次创建，后台执行器的并行能力没有充分接到产品。
4. 暂停只在外层轮询检查；等待执行槽或写入锁的子任务，以及同一规划调用中的后续动作，需要共同的执行边界信号。
5. 子任务生命周期和上下文整理缺少面向当前任务的聚合展示；无可执行动作的等待态还可能空转。

## 本轮实施与验收

- [x] 全量模型请求预算：计入运行提示词、工具目录/schema、工作状态与计划；超大可恢复结果落盘，提供明确分页读取工具，不静默裁掉用户要求。
- [x] Worker 工作笔记与当前目标摘要持久化；完整记录继续可追溯，模型不重放所有过程。
- [x] 补充消息处理成功后才确认；失败、重启后保持同一目标与已完成结果。
- [x] 统一暂停/补充消息边界；对子任务限量调度，独立就绪分工实际并行，状态与结果正常回收。
- [x] 对纯语言连接中断做有限退避恢复；语义失败交回 Leader，媒体副作用不盲目重试；不可推进状态明确显示。
- [x] 右侧显示当前分工、执行/交接状态、上下文整理与实际模型用量，事件绑定到当前任务。
- [x] 验证长工具输出、不同实体累积、无新消息自动推进、仅剧本收尾、并行、暂停、新消息、连接中断和重启恢复；使用 Fake Provider 与现有数据，避免重复付费生图/视频。
- [x] 完成相关 Python/TypeScript 检查、构建与实际产品展示验证，记录结果与限制。

## 当前实现与核对结果

`agent_runtime/model_input.py` 在完整 Leader/Worker 输入上整理可恢复的大块内容，`token_budget.py` 使用随包携带的 o200k 编码进行本地计数。原始 payload 写入 SQLite，`agent.read_context` 支持 JSON pointer、offset 和 next_offset；Trace ZIP 同时收录这些快照。当前目标与 Worker 笔记独立持久化；结构化工作集仍是日常输入基础。单个小结果大量累积时也会整理集合，不依赖任意条数上限。

Leader 和 Worker 每次决策都从同一运行记录读取 `goal.original_request / current_goal / latest_user_instructions`。即使沿用旧WorkOrder或同一次schema读取周期，最近补充仍会更新；Worker将相关新要求用于本任务，及时修正旧笔记。2026-09-11实际续跑发现此前只向Leader传递最新要求，现已补齐Worker入口，并用复用旧工作视图的连续两次决策验证。

语言 Provider 本身还检查嵌套 Tool 的文本请求，避免绕过 258k 预算；这类 Tool 不能直接执行上下文回读，超限时返回明确错误，让 Agent 收缩本次任务。2026-09-12 已接通 Worker 的实际图片输入：成功图片操作将精确候选和必要对照登记到 Tool 回执，下一次已有决策附上图片及版本、用途、实际观察尺寸；`image.view` 可读取明确版本或原像素局部。图片编码只在请求边界生成，不作为 Base64 文本灌入历史；没有附件的旧记录不猜图，其他 Worker/Leader 不自动广播这些图片。视图不等于 QA 通过或采用。

文字仍用 o200k 计数；每张实际附图预留 8,192 tokens 的保守空间，明确标为 allowance 而非供应商用量或精确视觉公式，Provider usage 独立保留。普通 100k–110k 文本保持正常使用；本地回归确认 105k 文本加两图不触发整理。请求溢出后的单次重试保持图片顺序和观察映射。Provider 默认连接超时为 30 秒、连续无响应读取超时为 600 秒；流持续有数据时不会因总时长而中止。本批仅完成保存输入回放与 Fake 回归，按用户要求未重启加载或真实复跑，详见[第七批集中修复](research/2026-09-12-generation-quality/72-seventh-pre-video-consolidation.md)。

产品 Runner 先执行 Agent 计划中所有已经就绪的步骤，包括为并行分工准备输入的读取和编辑，再推进已经保留的 Worker；依赖关系仍由计划表达。独立分工默认最多 4 项同时执行。每项分工连续执行自身已经落盘的决策与工具调用，直到完成或交接；较慢的媒体观察不会在每次决策后拖住其他分工。当前一组准备步骤完成后，新结果交回 Leader，它可以安排独立工作；明确只需等待时，可以保留正在执行的分工并给出空步骤计划，机械轮询不因此反复调用模型。直接修改内容的工具串行保存；模型和媒体等外部调用不占用整段项目写锁。媒体服务在实际保存时使用共享锁，同一图片任务和同一H3请求各自防止重复推进。待执行请求、已完成结果、工作笔记和目标都落盘。暂停或新消息在下一次决策/Tool 前接管；已开始的调用允许结束并落盘。新消息只有在处理成功后确认，失败恢复使用同一任务；纯语言连接中断按 3、8、20 秒有限退避，暂停或新消息可提前结束等待。失败或没有可执行动作时给出具体状态，不空转。

2026-09-11 的真实制作进一步发现，旧 Runner 只优先创建就绪的委派，导致独立的 Leader 读取仍被保留的视频轮询占住。相关调度及空等待计划已修复，27 项 Harness/重规划检查和 Pyright 通过。当前项目副本中的两个真实读取工具成功执行，随后产生独立后期分工；副本只使用本地确定性规划响应，未生成媒体或替换正式项目。

右侧展示当前分工、执行与交接状态、258k 上限、请求估算、输出预留和整理前后大小；真实模型 usage 单列。事件按当前 job/run 归属，Tool 开始和完成按同次调用合并展示，完整原始事件继续留在 Trace。

本轮核对使用离线 Fake Provider 和真实路由、SQLite、SSE、React 页面。上下文/Team/Provider 64 项通过；ProductionManager/AgentRuntime/持久化/产品相关 227 项通过（其中一项原测试需按新增的可选 working_notes schema 更新，单独重跑通过）。随后新增阶段交接断网、不可推进状态两项检查，Harness/Team/Tool 40 项重跑通过，13 项重规划回归通过；合计覆盖 293 个不同的后端用例。证据在 `output/harness-context/`，不同检查对应各自范围，不等同于重新完成一次真实长片生成。

浏览器验收通过两项独立分工同时可见、超过 258k 的输入整理后调用、暂停阻止待写入场次、继续使用同一 job、正文自动刷新和全部分工完成。4 次实际整理的估算输入从 408,759–409,479 tokens 降至 9,103–9,820；这是本地压力数据的结果，不能推广为日常节省比例。详见 `ui-acceptance/result.json` 和同目录截图。

另对原校园成片的 36 次真实 Astra 文本请求只读核对：o200k 文本估算均比 Provider input_tokens 少 10 tokens；其中最大实际输入为 109,647。现有 1,024 tokens 封装预留覆盖了这些样本的差异，不能视为所有兼容代理的永久保证。核对数据为 `token-estimate-validation.json`；没有新发起付费模型调用。

TypeScript、Pyright、Vite 构建和 uv 锁文件离线核对通过。macOS 应用已重新打包并打开；随包编码、许可证及离线计数验证通过。网页 8018 与原生后端均返回 context_window_tokens=258000，原任务 id 和完成状态保留；21 个镜头、131 秒当前剪辑仍在，现有视频预览可播放。旧请求没有补写压缩事件；实际模型记录仍为 Astra/xhigh，输入 25,402 tokens、输出 1,085 tokens。见 native-acceptance.json、native-current.png 和 web-backend-acceptance.json。

## 验证入口

所有相对证据路径以 `output/harness-context/` 为根：

| 范围 | 证据 |
| --- | --- |
| 完整请求预算、分页恢复、Provider 拒绝和并发限制 | `budget-team-provider-tests.txt`（64 passed） |
| 生产管理、持久化、产品内容与旧案例兼容 | `integration-tests.txt`（226 passed；旧 schema 断言更新见下项） |
| 新增可选工作笔记与目标摘要协议 | `schema-regression.txt`（1 passed）；最后协议/上下文检查见 `final-prompt-context-check.txt`（9 passed） |
| 生命周期、阶段交接断网、暂停、新消息、重启与工具状态 | `lifecycle-final-tests.txt`（40 passed，含 2 项新增） |
| 自动重规划语义和故障交接 | `replanning-regression.txt`（13 passed） |
| 不同后端用例数量 | `test-collection.txt`（293 collected；重复验证不重复累计） |
| 真实 Astra 历史请求与本地计数比较 | `token-estimate-validation.json`（36 条只读样本） |
| 真正路由、SSE 与页面交互，使用本地 Fake Provider | `ui-acceptance/result.json`、`ui-acceptance/task-status.json` 与截图 |
| 类型、构建、锁文件和本机打包 | `pyright.txt`、`typescript.txt`、`frontend-build.txt`、`python-lock-check.txt`、`native-package.txt` |
| 更新后原生与网页案例 | `native-acceptance.json`、`web-backend-acceptance.json`、`native-current.png` |

测试均使用仓库已有的 Python 环境及本地 JavaScript 二进制；没有执行公共 npm 安装或重复付费生图、视频。长上下文生成质量与长期稳定性尚未作新的真实 Provider 复跑，本轮结论限于上述运行机制及可复现检查。

本轮补充覆盖素材任务切片与分页读取：普通读取以完整参数区分范围，不再只按工具名覆盖；文档读取继续按文档身份替换重复版本。同一素材任务所取的不同镜头、不同上下文分页、不同视频单元可以同时保留。一个单元的路线保存只移除该单元的临时上下文，已按实体折叠的引用预览不再保留重复正文。25项相关检查通过；用同一真实WorkOrder重建，18镜所需正文可以同时用于输入映射。

H3 路线及其准备项的引用表示持续更新的执行记录。Worker 每次读取它们的当前状态，委派时保留的状态号不冻结后续读取；这样，建立任务导致状态更新后仍能继续推进同一任务。工具修改仍使用当前状态处理并发，剧本和分镜等实际内容来源另行核对。原始委派引用和工具结果继续保留在Trace中。
