# 144 次真实语言调用：往返来源与 Worker JSON 兼容修复

更新：2026-09-12。对象为《一起签》`project-5b8286dc63e6`、`studio-job-20f086971171` 的同一次视频前调试。此次只读原运行 SQLite/Trace，修复源代码和新增测试；没有调用真实 Provider、修改原项目或覆盖原 Trace。同项目暂停后续跑属于调试，不是从空项目稳定验收。

## 1. 统计口径与主要时间来源

使用现有 [performance observer](../../../output/quality-e2e-2026-09-12/observations/project-5b8286dc63e6-performance.md) 的 `debug-pause-five` 快照，并以 `studio_events`、WorkOrder SpecialistTurn 和原始模型输入/输出逐项核对。快照 UTC `2026-09-11T19:57:50`，即北京时间 03:57:50。

- 144 次语言调用累计 4,760.78 秒（79.35 分钟），包括 9 次真实视觉检查。导演 25 次、纸笔 39 次、素材规划 23 次，共 87 次；这三者累计语言耗时 2,748.56 秒。
- 112 次已执行 Tool 中有 7 次 `image.enqueue` 和 14 次 `image.advance`。后者是已接通的程序确定性续跑，不对应 14 次额外语言决策。7 张生成图片、5 次本地字版 Tool 调用（3 成功、2 失败）、分镜编辑和实际检查不能按同一种“生成次数”理解。
- 包含暂停/恢复的墙钟为 7,694.27 秒。语言累计时间含并行，Tool 时间又包含媒体或嵌套语言耗时，不能相加，也不能将上述墙钟作为连续运行速度。
- 原始事件按角色统计 Writer 为 20 次语言调用；早期交接中的“21 次”不作为本报告的同口径数据。当前 21/33 条素材规划/纸笔 SpecialistTurn 分别包含 1 条 REQUESTED 待执行请求，已执行 Tool 为 20/32。

### 三组证据最明确的可避免往返

| 来源 | 实际证据与额外工作 | 当前状态及必要制作判断 |
| --- | --- | --- |
| 已确定内容逐项搬运、当前正文丢失 | Writer 连续 6 次 `artifact.create_asset` 保存文字资产；第 5 turn 又 `agent.get_tool_attempt`，原因明确写“当前摘要未保留具体外形和服装”。Leader 在 02:57–03:00 的 6 次 LM/219.46 秒中只执行 5 个 Tool：scene/doc 各读两遍及一次 H3 capability，两个既有 Worker 没有新 turn。 | 批量文字资产、当前 brief 保留、Leader 当前读取正文与精确引用、双向调度接缝已经修复。仍需新完整运行验证实际节省。角色外形、可拍动作与故事修正需要判断，单纯把同一信息再次搬运不需要。 |
| 局部编辑与制作合同缺口 | 导演只需拆第 10/12 镜，旧 Tool 却要求重输完整 14 镜；`model-9897a10799d2` 用时 310.65 秒、输出达到 20,000 tokens 后 incomplete。纸笔单签状态曾被母 Asset 固定文字合同拒绝；素材规划以稍后列表项为已完成母图依赖，被列表先后门拒绝，再回读失败参数并改写。 | `shot_splices`/`clip_group_splices`、版本级 `content_state`、AssetPlan DAG 和下游真实依赖已修。本轮两次真实局部拆镜保存成功，形成当前 18 镜；最后一条请求尚待执行。签名状态、对白与动作可演性本身需要判断；为两镜重输全片、为合法依赖改变表达不需要。 |
| Worker 工具请求外层 JSON 拼接错误 | 8 份完整工具内容因 `Extra data` 被拒绝，下一次输入均带 `response_protocol_feedback`。8 次修正请求累计 130.62 秒；原始被拒绝生成累计 185.04 秒，其中包含本来需要的创作/参数形成，不能全算纯浪费。 | 本次精确兼容修复。离线回放证明这 8 份原文现在可直接形成同一 Tool 请求；没有将这 130.62 秒宣称为已观测到的新运行提速。 |

### 素材规划 21 turn、纸笔 33 turn 的具体边界

素材规划 5 次 `artifact.read_current` 的明确理由是并行导演/纸笔更新：第 10/12 镜动作、纸向、最新版本与引用。这些不能一概认定为无意义轮询。其中第 10 次 `agent.get_tool_attempt` 和第 11 次重新 patch 直接由已修复的列表顺序门导致。轻目录刷新与局部参数读取仍是当前一致性读取，未发现足够证据称剩余工作“本可独立分工但被漏派发”；旧调度饥饿已有证据且已修。

纸笔 33 turn 中包括 8 次程序 `image.advance`、4 张真实图片、5 次字版 Tool 调用（3 成功、2 失败），以及 6 次已执行 QA 和第 33 turn 的 1 次 REQUESTED 检查。最后一项尚未执行，不能算已检查。字版拥挤、签名状态和摄影用途属于必要检查。第 18/21/27 次 `agent.get_tool_attempt` 都为复用版式坐标；21/27 对应旧 `content_state` 合同修复，18 是一次当前摘要不含精确绘制坐标后的定向回读。当前保留这一精确回读，本波不另建写后缓存或通用批处理。

## 2. 八份原文与现有合同

原文件保存在 `output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/`。新增测试 fixture 逐份复制原 `model.output.json.data.text`，附原文件名；原文件没有改写。

| 原始输出 call_id | 后续协议修正 call_id | 修正请求秒数 |
| --- | --- | ---: |
| model-669f00e5c4ed | model-75c96a72dcd8 | 31.02 |
| model-00a844ed503a | model-632e4303acb7 | 14.03 |
| model-9406f27f3816 | model-76107614f11d | 17.48 |
| model-ffdce312ebbd | model-c6624e884510 | 12.91 |
| model-d67883d5a46d | model-a4538eb0e796 | 10.57 |
| model-6f2f3f8b4f37 | model-d949ece331ae | 19.76 |
| model-a3293a8bc791 | model-6be68f14aa47 | 9.08 |
| model-eb92815da80b | model-2176748f0ca3 | 15.77 |

7 份原文是完整的 `{tool_name, reason, arguments, working_notes}` 对象之后再多一个 `}`（可有空白）。1 份是在完整 `{tool_name, reason, arguments}` 之后继续 `,"working_notes":...}`。

`SpecialistToolRequest` 实际已有 `tool_name`、`reason`、`arguments`、`working_notes` 四字段。Worker 每次输入的 `response_schemas.tool_request` 也包含当前 schema。旧 normalizer 只认识增加工作笔记之前的字段组合，因此未匹配这 8 个已经有完整内容的请求。

现有 Provider 请求仍是 `/responses` 的文本输入/输出：携带 instructions、input、reasoning、max_output_tokens 和 stream，没有传原生 constrained JSON/tool strict 配置。不能把文本中给出的 schema 宣称为供应商已经保证结构正确。

## 3. 公开成熟规范及本地采纳

[Claude 官方 Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) 区分 JSON 输出格式约束与 strict Tool 参数约束，并说明停止于 token limit 等情况仍需处理。这支持在结构输出边界校验并保留实际参数合同，不能把任意不完整文本当作可执行工具。本项目没有验证当前兼容代理的原生 strict 能力，本波不擅自开启或更换 Provider。

[RFC 8259](https://www.rfc-editor.org/rfc/rfc8259) 定义一个 JSON text 为一个序列化值，对象成员名应唯一；重复字段的解释不可依赖。本地只接受可以完整解析的唯一工具对象和两个已观察到的标点错误，不从多对象/后续正文中任意挑一份，也不合并冲突字段。

最小实现位于 `agent_runtime/response_normalization.py`。PM 原 `_normalize_specialist_response_payload` 只保留薄代理，并传入当前 `SpecialistToolRequest.model_fields`，避免独立字段清单再次落后于实际 envelope。

- 完整 Tool 对象后仅剩一个多余 `}`：移除对象之外那个括号。
- 完整 Tool 对象后为合法字段续接：只允许尚未出现的 `reason` / `working_notes`，移除提前关闭顶层对象的那个括号。
- 使用解析器定位结构边界，返回原字符串切片；不重新序列化参数，不改变数值写法、字符串转义、文字、顺序或嵌套括号。
- 不续接 `tool_name` / `arguments`，不吞未知字段、字段冲突、第二个 JSON、后续正文、多余多个括号或截断值。需要归一但含重复字段的非法 JSON 也保持原样交协议反馈。已经合法的 JSON 不改动，本波没有新增全局重复字段检查。
- 正常化后继续走现有 Pydantic、Registry 与实际业务合同。工作笔记仍由既有 PM 路径落盘；没有增加调用、调度状态或协议字段。
- Provider 在返回文本前写原始 `model.output` Trace；兼容发生在 Worker 随后的解析处。测试覆盖实际语言适配器的 Fake HTTP transport 到 Worker 接口，验证原文 Trace 未被替换。

## 4. 验证与尚待观察

新增 `tests/test_specialist_response_normalization.py` 与 `tests/fixtures/specialist_response_serialization.json`。8 份真实原文全部通过当前 envelope 校验，所有字段与 fixture 的原始参数/笔记逐值相等；另覆盖一轮 Worker 请求落盘、重开 Store 后同一 Tool 与笔记保留、当前 Registry 拒绝缺参，以及原始 Trace 保持错误原文。

验证还包括对象内字面括号/转义/高精度数值保持原字符串、合法 JSON 幂等和必要的协议反馈边界。最终运行新 27 项和现有 PM 相关 41 项：`pytest tests/test_specialist_response_normalization.py tests/test_production_manager.py -k specialist -q`，68 passed / 43.50 秒。helper、PM、专测三文件 Pyright 为 0 错误、0 警告；`git diff --check` 通过。独立复核将 8 份 fixture 逐字对照原 Trace，并确认只删除所定位的一个结构括号、保留全部字段。

没有 OpenAPI 或 Tool schema 变化；没有调用真实 Provider、修改原项目或补写历史 Trace。真实续跑和新空项目完整视频前验收仍待进行。

## 5. 后续调试观察：04:28:41 恢复后的最初约五分钟

恢复使用后台 PID 33521。只读核对当前 SQLite 和原始 Trace；观察者未发产品消息、Provider 请求、暂停或写运行数据。本段更新文档，不改写前述 144 次快照统计。

三条此前 REQUESTED 的 Tool 沿用原 SpecialistTurn ID 执行，`requested_at` 均仍是此前 03:55–03:56 的时间：

| 待执行请求 | 原 SpecialistTurn ID | 本轮实际结果 |
| --- | --- | --- |
| 素材规划第 21 turn `artifact.patch_asset_plan` | specialist-turn-cffb26a515c54d3e819f0bd43f967e6b | 04:28:42 SUCCEEDED |
| 纸笔第 33 turn `qa.inspect_asset_version` | specialist-turn-0ac3231afb1640e3b5e18302ad677d74 | 04:29:09 SUCCEEDED，已实际检查 |
| 导演第 19 turn `shot.patch_storyboard` | specialist-turn-1b537c2eeeab422390f66648262e6e51 | 04:28:42 FAILED，业务校验拒绝跨镜重复 dialogue line ID；并非恢复重新创建请求或错误 owner |

### 首轮 Worker 输入与引用投影

三份输入的 `goal.original_request/current_goal/latest_user_instructions` 与当前 AgentRun 逐值一致。当前目标明确只完成视频前准备，`enqueue=false`，不创建/提交真实视频、不继续视频后期，检查完直接结束。

| Worker / call_id | 实际 instructions + input_text 的 o200k 估算 | Provider input_tokens | 已出现的引用投影 |
| --- | ---: | ---: | --- |
| 素材规划 model-713858df15a5 | 59,583 | 59,593 | 6 处 artifact_refs_from |
| 导演 model-c40ed40f940d | 68,620 | 68,630 | 3 处 artifact_refs_from |
| 纸笔 model-85ffb438b79e | 53,208 | 53,218 | 7 处 artifact_refs_from |

本地重新计数与各自 `context.prepared.estimated_input_tokens` 相同；这三个输入的全部 pointer 和 indices 都能在同一实际请求内解析。表中是 Worker 文本决策，未混入纸笔恢复 QA 的视觉请求。Provider 比文本估算各多 10 tokens 是本次观测值，不推广为固定计算公式；两种数据仍分别记录。

### 两次已经发生的真实自动兼容

- `model-85ffb438b79e` 的原始输出仍是 `Extra data` 非法 JSON；同一请求随后成为纸笔第 34 turn `image.compose_text_plate`，04:29:54 SUCCEEDED。
- `model-362832142a3f` 的原始输出同样为 `Extra data`；同一请求随后成为素材规划第 24 turn `artifact.get_asset_work_context`，04:29:43 SUCCEEDED。

两份原文通过当前 helper 和 typed request 解析后，其 `tool_name/arguments/reason` 与实际持久化请求逐值一致。原 `model.output` 仍保留多余括号，各自直接执行 Tool，未追加 `response_protocol_feedback` 修正调用。这是本次新恢复已经观察到的两次兼容成功；仍不宣称整个运行已节省历史 130.62 秒或已经通过新项目稳定验收。

### 保存成功与跨镜发声时窗仍是两回事

导演的原失败请求让同一句 `line-yqq-02` 跨 `shot-yqq-03` 的 2.5 秒和 `shot-yqq-03-evidence` 的 1.5 秒连续说完，正文明确只说一次。但当前 `StoryboardContent` 要求一个 line ID 全片只归属一个镜头，拒绝该表达。

04:30:07 原 Worker 自行改写并保存成功：typed line ID 只保留在首镜 0–2.1 秒，随后 0.4 秒和证据镜 1.5 秒改为空 line ID；`sound` 正文仍写跨镜连续尾句。因此保存成功尚未表达完整的 4 秒连续发声窗口，不能当作 H3 跨镜对白链路已解决。该具体缺口已交给 H3/分镜切面的后续贯通修复；本观察没有将 Agent 的 prose 绕过视作能力验收。
