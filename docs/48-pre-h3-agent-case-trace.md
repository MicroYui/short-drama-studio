# 校园短剧：真实 Agent 的 H3 调用前准备实验

更新：2026-09-08。本轮已完成：沿用已有校园短剧项目，使用真实语言模型和产品 Agent，保存并校验了 12 组 H3 输入，覆盖 21 个镜头、131 秒。AgentRun 为 `COMPLETED`，最终 `assessment.json` 的 `complete_before_h3=true`；真实 H3 调用、视频 Task 和 H3 ProviderRun 均为 0。

## 案例和范围

- 项目：`project-11b4f7a637c3`，全世界数学能力下降100倍：食堂篇。
- 当前输入：Script v7、Bible v2、canonical Storyboard v2、ProductionSpec v4、AssetPlan v24；核心资产和逐镜引用已在前一阶段确认。
- 当前 AgentRun：`agent-run-8fc670a0966844bf87f405083819eea7`，与前一阶段共享原项目、数据库和素材目录；没有创建替代案例。
- 目标：覆盖 12 个 GenerationUnit、21 个编辑镜头、131 秒。由 Agent 读取单元来源、选择有序媒体、编译提示词并保存 H3RoutePlan。
- 本轮允许真实语言模型调用；不向 H3 上传、提交或轮询，不创建视频 Task/Take，不生成音频、时间线或导出。媒体 Provider 工厂在进入网络调用之前拒绝执行。

产品使用 `ProjectDocument + versioned Artifacts` 保存内容；本轮 trace 是运行记录，不是另一套项目状态。`GenerationPlan v3` 已通过修复后的编译器生成；既有 v1、v2 与失败记录仍保留。

## 最终准备产物

12 份 H3RoutePlan 均为 `READY / PLANNED`。保存后的完整 Prompt 与有序媒体逐项匹配对应的成功编译 ToolAttempt，没有以缩略文案替代；媒体版本均已批准且当前有效，本地文件存在。剧本、Bible、镜头、资产、分镜文档、制作规格与资产计划保持不变。

| 单元 | 镜头数 | 目标秒数 | 有序媒体数 | Prompt 字符数 |
| --- | ---: | ---: | ---: | ---: |
| 001 | 3 | 13 | 6 | 6,707 |
| 002 | 2 | 14 | 7 | 6,797 |
| 003 | 2 | 14 | 9 | 6,833 |
| 004 | 1 | 5 | 9 | 6,244 |
| 005 | 1 | 11 | 8 | 6,308 |
| 006 | 1 | 7 | 9 | 6,224 |
| 007 | 2 | 15 | 6 | 6,332 |
| 008 | 1 | 11 | 5 | 5,480 |
| 009 | 2 | 8 | 5 | 6,635 |
| 010 | 2 | 7 | 6 | 6,489 |
| 011 | 2 | 13 | 7 | 6,602 |
| 012 | 2 | 13 | 7 | 6,340 |

单元完整 ID 的前缀为 `clip-group-independent-v7-`；媒体数是每次请求的有序引用数量，不是去重后的资产数量。每组的 RoutePlan ID、完整版本、成功编译尝试 ID 和检查结果见 [最终覆盖检查](../output/agent-video-preparation/math-2min-pre-h3-20260908-001/assessment.json)，原始准备内容位于同目录的 `route_plans/`。

最后一次修复恢复后，Agent 自主安排了第 006–009 组和第 010–012 组两个工作单，随后执行覆盖核对，最终由 Leader 结束阶段。第一个工作单完成 4 次精确结果读取及 4 次保存，第二个完成 3 次读取及 3 次保存；该恢复过程没有再次调用提示词编译器。

## 实际参数与上下文用量

调用配置为 `gpt-5.6-sol`、`reasoning_effort=low`、本地代理 `http://127.0.0.1:4141/v1`、超时 180 秒。修复后的输出上限分别为 Leader 12,000、Worker 20,000、片段 Prompt 编译器 8,000 tokens。361 次记录中的图片输入数量均为 0；本阶段通过来源字段和媒体地址组织已确认的图片，没有重做图像生成。

整轮共记录 361 次语言调用，其中 354 次获得 Provider usage；另外 7 次保留为未知用量，未估算或当作 0。下表的调用数包含失败与中断，token 统计只使用实际返回的 usage。

| 角色 | 调用记录数 | 获得 usage | 输入 tokens 中位数 | 输入 tokens 最大值 | 整个文本输入最大字符数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Leader | 58 | 55 | 23,004 | 27,620 | 79,290 |
| Worker | 251 | 249 | 35,529 | 121,170 | 287,031 |
| Prompt 编译器 | 52 | 50 | 19,980 | 22,527 | 50,089 |

已获得 usage 的调用累计输入 12,654,678 tokens、输出 386,667 tokens。这是包含失败、开发修复和多次恢复的总记录，不是正常生产成本基线，也没有据此估算费用。原始 usage 另外保留缓存等 Provider 返回字段。

最后一次恢复从调用 `00334` 到 `00361`，共 2 次 Leader 与 26 次 Worker 调用，均获得 usage。该阶段 Worker 输入中位数为 22,760 tokens，最大值为 39,216 tokens；整个文本输入最大 103,547 字符。它只完成剩余 7 组的保存和最终核对，不能与修复前的整个编译阶段直接作为成本对照。

上下文曲线可直接用于后续案例素材：[PNG](../output/agent-video-preparation/math-2min-pre-h3-20260908-001/context-input-tokens.png)、[可缩放 SVG](../output/agent-video-preparation/math-2min-pre-h3-20260908-001/context-input-tokens.svg)。曲线按角色连接实际输入 token 数据，缺失 usage 的点被省略；关键修复对应调用编号有标注。

## 实际执行中发现的问题

| 观察 | 原因 | 修复与重跑方式 |
| --- | --- | --- |
| Leader 已创建 GenerationPlan，却反复读取同一计划；正确的 Storyboard/GenerationPlan 引用被拒绝 | 完成资产阶段的工作明细占据 ContextPack，最新动作与其他工件引用被预算裁掉 | 完成资产清单在 Leader 中保留条目身份和状态，详细工作行按需读取；优先保留当前动作；选中的精确工件身份不会随摘要丢失。沿原 Run 恢复 |
| 刚编译的 GenerationPlan 被判为 shot revision 过期 | 引用规划会递增当前 Shot revision，编译器却沿用 Storyboard 快照中的旧 revision | 新执行计划使用当前 Shot revision，叙事、分组和时间窗继续来自已确认 Storyboard。保留旧版本，让 Agent 重编译 v3 |
| Agent 把缺少媒体地址误判为缺素材；整份计划、分镜、资产计划的读结果使 Worker 输入达到 287,031 字符 | 视频准备缺少按单元读取的完整来源和可用媒体地址接口 | 新增只读 `route.get_generation_unit_context`，复用已有引用解析和 H3 媒体映射；返回当前组完整来源、真实地址及批准版本的候选。抽查 3 组，响应为 13,382–17,550 字符 |
| 语言编译器拒绝生成 Prompt，但 Tool 只报缺少画幅 | 编译输入没有明确 H3 model lock，旧制作规格中的阶段备注也被当作当前授权；格式检查又遮住模型的实际答复 | 从实际 H3 请求模型提供目标信息，明确当前操作只编译文本；保留原规格及创作约束；格式错误与实际模型答复一起进入 ToolAttempt |
| Worker 提了压缩意见，内部编译器仍收到相同输入；编译结果跨工作单交接不完整 | Tool reason 不进入内部语言模型；外部调用的结果没有精确 ToolAttemptRef；引用候选可能仍指向旧 GenerationUnit | 新增 `revision_instructions` 并传入编译模型；交接同时保留完整当前结果与精确 ToolAttemptRef；Leader 优先选择当前版本；返回可直接用于 RoutePlan 的 `candidate_id` |
| 英文表达被误判为缺少原说话人；超长草稿只能从头重写 | 校验只认中文姓名；错误只留下前 2000 字符，且待修订来源也被错误套用最终请求的 7000 字符限制 | 正式人物引用能通过 Bible entity 解析为说话人；没有此关联时明确保留原姓名。失败 ToolAttempt 保留完整草稿，`source_prompt` 可传入待修订长文，最终输出仍需满足原上限。保留官方字段标题，只压缩重复正文 |
| 单次 GenerationPlan 读取仍把 Worker 输入推到 153,875 字符 | `document.versions`、`version.payload` 和 `generation_plan` 重复带入历史及当前内容 | 模型投影只保留选定版本的完整计划一次、版本身份和输出槽。历史仍在数据库与原 Tool 结果；对已记录结果的只读复算从 75,491 字符降为 18,082 字符 |
| 文字图版的别名连续修订后仍不通过 | Worker 把别名绑定为上传图片编号；错误只报别名，未显示要求的主体编号 | 输入新增 `required_binding`；错误给出完整的 `alias = Subject` 及来源 Picture。多个参考属于同一主体时共享 Subject，仍逐项保留来源图片映射 |
| 首组已保存后，Worker 工作集仍为 97,688 字符，整个调用输入为 175,923 字符 | 外部编译调用没有消耗已读失败草稿；成功结果重复带入 `source_prompt`；RoutePlan 的外部调用分类掩盖了其持久化写入语义 | 编译重试消耗它使用的精确旧稿，成功交接保留新 Prompt 与媒体；保存 RoutePlan 后转为工件引用并退休该已完成单元的来源。对相同工具序列和相同进度记录复算，工作集为 9,529 字符；其他未保存单元的来源不受影响 |
| 续接规划误把 Action ID 填入 `carried_work_order_ids` | Leader 的 ActionDigest 只提供动作 ID，没有对应工作单 ID | ActionDigest 明确携带 `work_order_id`，经预算裁剪和模型序列化后仍保留 |
| 已续接 Worker 返回 `LEADER_REPLAN` 后，Run 一直等待 | 其 Action 属于旧计划，当前计划的失败动作检查看不到它；续接状态分支又把 NEEDS_ATTENTION 一律当作仍在工作 | 依据明确的 owner 转移触发自动重规划；测试覆盖旧计划工作单返回当前 Leader 的完整路径 |
| 第三组已有成功编译，却在交接后被判为不存在成功结果 | Leader 交接索引只收集失败尝试，并未以随后成功结果替换；Worker 被替换后成功文本更难寻址 | 未落盘的成功编译按单元提供 ToolAttemptRef，正文按需读取；后续成功清除旧失败指针，保存到 RoutePlan 后退休相应编译引用。已返回、完成或被替换的 Worker 仍可交接其已生成的有效结果 |
| 同一 Worker 读齐 9 份成功结果后，输入达到 201,165 字符；保存第一组后，其余已读结果一起消失 | 按引用读取成功稿没有应用直接编译结果的投影，重复带入旧草稿；写入会把所有已选尝试都视为已消费 | 成功稿的 RESULT 采用相同投影，保留 Prompt、媒体和身份，旧草稿仍在原始记录；保存或重编译只消费对应单元及版本，保留其他待保存组。修复后从第 6 组恢复；相同 9 次读取、相同进度记录的工作集复算从 145,944 降到 116,644 字符 |

另对最终汇总读取做了源码核对：原 `route.list_h3_plans` 会把每份完整 Prompt 和媒体地址一起重放。模型投影现在保留单元覆盖、版本、状态、Prompt 字符数及有序资产版本；新增只读 `route.get_h3_plan` 按 ID 返回完整内容。数据库和 HTTP 列表原始内容保持完整。这项改动属于汇总阶段的上下文整理，不计作已经观察到的额外生成失败。

同时把片段编译的输出 token 上限从 2,200 调整为 8,000，以适应既有 7,000 字符的最终 Prompt 合同。此前观察到的是拒绝编译，不能把这项参数调整写成已测得的 token 截断修复。

部分模型答复包含两个连续 JSON 对象。已比对原始 Responses 流：这些案例的 Provider 确实返回了两条 message，并非本地流解析器重复拼接；产品协议检查拒绝该轮并反馈，随后由模型自行纠正。模型的参数/格式纠正与上表需要改代码的问题分开统计。部分进程在 SIGINT 后仍有线程执行已派发工作，因此后续修复使用进程终止后从持久化 Run 恢复；中断未收到 usage 的调用单独保留，不伪造完整用量。

## Trace 与复现入口

本轮目录：`output/agent-video-preparation/math-2min-pre-h3-20260908-001/`。

| 文件 | 内容 |
| --- | --- |
| `session.json` | 原数据库、项目、AgentRun、原始阶段目标与停止边界 |
| `events.jsonl`、`process-*.log` | 连续事件、每次进程恢复、操作员修复说明和运行进度 |
| `model_calls/NNNNN/instructions.md`、`input.txt` | 实际发送的指令和完整文本输入，包括 ContextPack、工作集、Schema 和反馈 |
| `request.json`、`result.json`、`usage.json` | 模型、输出上限、超时、字符数、图片数量、调用耗时、成功/错误、Provider 返回的实际 token 用量 |
| `provider-stream.txt`、`response.txt` | 原始响应流与产品解析出的原答；中断调用也保留已收到的流 |
| `plans/`、`actions/`、`work_orders/`、`tool_turns/` | Agent 的计划修订、动作、委派、Tool 参数、结果、错误和归属 |
| `route_plans/` | 保存的最终 Prompt、有序媒体和版本绑定；并不表示已经提交视频 |
| `database-before.sqlite3`、`project-before.json` | 本轮开始前的普通恢复备份和对照输入 |
| `context-fix-001.patch` 等修复文件 | 本轮各次源码修复记录 |
| `inspect_preparation.py`、`assessment.json`、`model-call-metrics.json` | 不调用 Provider 的版本/覆盖检查，以及从真实调用记录计算的指标 |
| `final-recovery-metrics.json`、`context-chart-data.json`、`plot_context_usage.py` | 最后一次恢复的单独用量，以及上下文曲线的数据和生成脚本 |

执行入口是 [validate_agent_video_preparation_run.py](../backend/validate_agent_video_preparation_run.py)。同一个输出目录会从 `session.json` 恢复同一个 Run；`--replan-reason` 用于把已观察并修复的问题反馈给产品 Leader。运行器不手工调用内容编译 Tool 来冒充 Agent 完成工作。

在仓库根目录复查落盘状态：

```sh
backend/.venv/bin/python output/agent-video-preparation/math-2min-pre-h3-20260908-001/inspect_preparation.py
```

字符数包含调用指令与输入文本，不是 token 数。token 来自 Provider 的 usage；中断而未收到 usage 的调用不估算用量。完整原答与工作历史保留在 trace 中，模型输入通过当前状态和精确引用重新组装。

这轮也观察到了 Agent 一次读取多个待保存结果的选择。即使删除重复旧稿，多份仍待使用的完整 Prompt 和媒体依然会占用工作集。因此上表的两次同记录复算只说明对应投影问题得到修复，不能解释为整轮所有调用都处于 9,529 字符，也不是从干净起点进行的成本或延迟对照实验。

`route.plan_h3` 会调用既有 `refresh_lightweight_reference_plans`，依据已声明镜头需求补全派生 `ReferencePlan.bindings`。本案例开始时这些绑定为空，首次保存路线后出现补全；对照检查分别记录这项派生变化，以及保持不变的原需求、镜头设计、剧本、Bible、资产和制作文档。不能把整份 ReferencePlan 描述为逐字未变。

## 软件验证

- 修复 001–012 后的较广回归：299 项通过，覆盖 ProductionManager、Agent Runtime、H3、语言 Provider、ToolAttempt 交接、Tool Registry 及运行器。
- 修复 013 后再次运行受影响链路：204 项通过，覆盖 `test_production_manager.py`、`test_agent_runtime.py`、`test_tool_attempt_handoff.py`、`test_h3_generation_unit_language.py`。两个数字有重叠，不相加作为独立测试数量。
- 在 `backend/` 运行 `.venv/bin/pyright`：0 errors。一次从仓库根目录直接指定文件的检查没有加载后端配置，产生依赖解析错误；改用正确工作目录后完整检查通过，两个日志均保留。
- 在仓库根目录运行 `./node_modules/.bin/tsc --noEmit` 和 `./node_modules/.bin/vite build`：均通过；没有安装新依赖。
- 最终实际状态检查：12/12 单元就绪，全部 Prompt/媒体与成功尝试一致，来源与版本检查通过，AgentRun 已完成，视频 Task/H3 ProviderRun/媒体 Provider 边界尝试均为 0。模型调用编号没有重复。

测试日志和各次修复前文件、补丁都在本轮输出目录内。验证实际媒体播放、音画同步和成片效果需要后续真正的视频生成，不属于本轮已通过的软件检查。

## 与前一阶段和案例展示的关系

[核心资产闭环记录](46-phase30-core-asset-plan-and-reference-pipeline.md)对应原资产 Run `agent-run-52da5fdb84fb4677bbeaf15d8d4cea5a`；详细文件仍在 `output/agent-asset-validation/math-2min-asset-media-context-v17-20260907-034/run/`，包括 `trace.json`、`trace.md`、`CONTINUATION.md`、用户供图接入与最终资产检查。上一轮的[成熟实现参考](47-video-generation-reuse-and-execution.md)属于无 Provider 调用的调研和离线验证，本轮才是后续真实语言 Agent 实验。

本案例包含用户供图、确认和开发者修复参与，不能描述成从零一次无人干预成功。当前阶段也不能证明真实视频的画面、声音、连贯性或整集播放效果。
