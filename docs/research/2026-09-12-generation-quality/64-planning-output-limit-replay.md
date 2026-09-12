# Fresh3 规划输出耗尽：实际请求、有限纠偏与离线回放

本项基于 `fab5b93` 的独立工作树。Fresh3 已自然失败，不恢复；没有调用真实 Provider、修改运行项目或启动新案例。它修复规划响应的资源边界，不代表整轮视频前制作通过。

## 已保存的实际故障

项目 `project-d2fa0fa72d76`，Job `studio-job-f199582089d7`，Run `agent-run-fcfcb2862e8d4f818b6c6a85e37f29ba`。五个图片任务成功，视频为零。导演完成后 Leader 及时继续，故障发生在后续语言规划响应。

原 Trace 目录：`output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-d2fa0fa72d76/studio-trace/studio-job-f199582089d7/`。以下时间为 +08：

|实际调用|输入/输出证据|实际行为与用量|
|---|---|---|
|14:01:40 `model-db6ca782df3d`|`20260912T060140138710-6ecc08b6-model.input.json` → `20260912T060216185871-18e96687-model.output.json`|合法发现 `route.h3_capability_profile` schema。输入 43,603，输出 1,655，其中 reasoning 1,552；36.045174 秒。不是解析纠正或失败重试。|
|14:02:16 `model-9907687267ca`|`20260912T060216342809-c83352b1-model.input.json` → `20260912T060547187792-68a218c9-provider.language_generation.json`|Astra/xhigh，上限 **12,000**，输入 43,664，输出用满 12,000，其中 reasoning 5,140；可见输出约占余下 6,860 tokens。210.841194 秒后 `sse_incomplete_max_output_tokens`。|

第二次实际输入 105,758 字符，完整文本估算 43,654 tokens；不是 258k 输入溢出，也不是 Worker 的 20,000 输出上限。Provider 记录收到 17,126 输出字符。历史 adapter 在 incomplete 时只记录字符数、原因、usage，没有保存该段规划正文；因此**无法证明它具体写了几项、哪一字段重复了多少次**。不把推测补成原 Trace。

输入已有剧本、正式分镜与五项素材完成信息。当前协议虽说“计划简洁”，另一条却要求逐项明确所有 delegation 扩展字段，包括默认空集合及 `expected_output_schema`，容易鼓励展开重复的交接模板。这是可确认的合同表达接缝；没有历史完整正文，不能断言它是全部超限原因。原实现对第一次 max-output 异常直接退出自动重规划，没有让 Agent 依据这个具体错误改写一份更小且完整的计划。

## 方法依据与本地选择

- [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses/create) 将 `max_output_tokens` 定义为可见输出和 reasoning 共同使用的上限；incomplete 是未得到完整响应的状态。本地保持该语义，不解析或执行截断 JSON。
- [Anthropic：Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) 的 orchestrator-workers 由中心 Agent 根据任务动态分解，并强调先用简单、可组合的方法。这里不引入固定阶段、镜头数量或新执行器。
- [Anthropic：Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) 建议使用明确但不过度固化的指令，并以轻量引用按需取资料。本地据此要求交接本次意图、所选对象和准确来源，不在每个分工重抄上游全文。

上述是公开方法；以下一次纠偏、具体提示和测试均为本产品的局部工程选择，不宣称官方推荐了这个确切次数或模型配置。

## 实现范围

1. `agent_runtime/planning_output.py` 提供明确的 12,000 共享输出额度、精确引用交接和当前就绪责任说明。`leader_protocol.md` 与已有 typed 默认值对齐：没有补充信息的集合和自定义结果 schema 可以省略；本次必要输入、判断、限制与验收仍要表达。独立工作仍可并行，没有固定 step 配额。已保存事实可用 Worker 的可选 `saved_tool_result_sequences` 交回，确实指定自定义 JSON Schema 时仍严格满足它。
2. `LanguageProductionPlanner.propose_plan` 仅对 `LanguageProviderIncompleteError(reason=max_output_tokens)` 提供**一次**带原因的重新规划。它要求重新生成完整对象、缩小当前可执行责任、复用精确引用；不续接被截断字符串，不执行部分步骤。纠偏额度在同一次 propose_plan 的 schema/验证往返之间共享，不能通过发现 schema 重置。再次耗尽或其他 incomplete 原因保持明确失败。
3. 所有请求仍是 Astra/xhigh、12,000；不降低推理、不增大上限。每次请求仍经过 `execution_boundary`，暂停和新消息先于下一次请求；既有 role/ref/依赖/计划验证照常执行。没有新的媒体调用或运行状态体系。Trace 的 `agent.plan_output_recovery` 仅记录纠偏反馈已准备，实际模型请求仍由正常 `model.started` 标识。
4. Provider 常规诊断补齐：新的 incomplete 异常携带实际收到的文本，单独写 `model.incomplete` 文件，标明 `saved=false`；不会产生成功 `model.output`，不会返回给计划解析器，也不把整段正文重复 emit 到页面事件。SSE delta/done/terminal 和普通 JSON output 都可保留收到的文本；正常完成的原文本处理规则保持。旧历史不存在的正文仍不可恢复。

没有修改已失败 Job、其原 Trace、图像版本、当前选用状态、H3 编译/生成规则。

## 回放与验证边界

`tests/fixtures/fresh3_planning_output_limit.json` 保留第二次实际请求的完整 instructions/input_text、第一次真实 schema 发现输出和终态 usage；只从终态元数据去掉不参与验证的 opaque response_id，并显式说明未保留 partial 原文。不是从该缺失原文补写一个“修好”的答案。

离线使用真实 `ModelInputCompiler` 重渲染实际输入：原有每个顶层字段逐值不变；估算从 43,654 变为加说明后的 43,794，纠偏反馈出现时为 43,937。这里增加了 140/143 tokens 的针对性说明，**不宣称减少输入**。两个按现有精确 Shot ref 委派的人工测试响应仅 310 tokens、1,384 字符，可以通过原 typed 合同；这证明紧凑表达被当前接口接受，不证明真实 Agent 必然选择这两项或能净节省某个时长。

专测覆盖：实际输入事实保持；正常 schema 发现后一次纠偏；发现 schema 不能重置额度；其他 incomplete 不重试；暂停/新消息接管；原引用校验保留；真实 Manager 在成功/再次失败时只保存完整合法计划并保留既有成功 Action；SSE/JSON 未完成原文仅保存在 Trace；完成响应文本格式兼容。

相邻回归首次 64 项为 62 通过、2 个旧断言失败：`test_language_provider.py` 两项仍假设单镜编译 `validation_notes` 没有 doc49 已上线的单镜/整组边界说明。依当前产品合同补全准确断言，保留原对白/时长验证；本批没有改变 H3 行为。

最终 `test_planning_output_recovery.py`（16 项）与完整 `test_language_provider.py` 共 **50 passed / 6.55s**；首次联合中另外 15 项 Leader 读取、上下文预算与 Planner 发现回归也通过（64 项原组在新增空白检查前有 62 pass，只有上述两项旧 H3 断言失败）。5 个源/专测文件 Pyright strict **0 errors / 0 warnings**，`git diff --check` 通过。离线 Fake 能验证合同、控制流和保存边界，不能验证真实模型收敛或声称这轮制作成功；后续验收须在统一修复后的新空项目进行，旧案例不续跑。
