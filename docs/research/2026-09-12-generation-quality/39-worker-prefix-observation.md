# Builder 实际请求的稳定前缀观察

状态：2026-09-12 只读完成。本项没有修改产品源码、运行数据库或任务，也没有发起模型/媒体请求。后续建议尚未实施，不为此暂停当前调试。

## 样本与计数口径

只选择同一 Builder「准确文字证据图片准备」的五份相邻 Worker 决策请求，未混入其 Tool 内部的视觉 QA 请求：

- 项目：`project-5b8286dc63e6`
- StudioJob：`studio-job-20f086971171`
- WorkOrder：`work-order-a318e1e27639488fab20ba34fc3a80d8`
- 时间：北京时间 2026-09-12 06:55:57 至 06:59:04，均为本次恢复后的已完成请求。
- 数据：只读 SQLite `mode=ro` 的 `studio_events`，以及以下已保存 `model.input.json`。Provider 用量取按 `call_id` 匹配的 `provider.language_generation` 完成事件，不用 model.started 估算完成次数。

Trace 目录为 `output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/`：

| 序号 | 请求开始（北京时间） | call_id | model.input 文件 |
| --- | --- | --- | --- |
| 1 | 06:55:57.824 | `model-c395ab9ce5aa` | `20260911T225557824233-74fed085-model.input.json` |
| 2 | 06:56:04.265 | `model-12fd3dfe8d54` | `20260911T225604264552-511c5917-model.input.json` |
| 3 | 06:56:15.498 | `model-9451527b8454` | `20260911T225615497879-563ebfa8-model.input.json` |
| 4 | 06:58:42.310 | `model-ad876eb41f69` | `20260911T225842310357-f6fb1db3-model.input.json` |
| 5 | 06:59:04.907 | `model-dee7712d8013` | `20260911T225904906711-12c165ec-model.input.json` |

五份实际模型均为 `gpt-6-astra / xhigh`，Worker `max_output_tokens=20000`，没有图片输入。角色 instructions 完全相同，均为 15,460 字符、**8,450 本地 o200k tokens**。本地采用仓库 `count_text_tokens` 和实际紧凑 JSON 序列化，不用字符折算，也不把 pretty-print 后的分块计数当成实际输入。

| 序号 | input_text 字符 | input_text o200k | instructions + input_text o200k | Provider input | Provider cached input | Provider output | Provider 调用耗时（秒） |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 73,035 | 26,003 | 34,453 | 34,463 | 0 | 206 | 4.230191 |
| 2 | 171,080 | 70,394 | 78,844 | 78,854 | 8,453 | 359 | 10.722801 |
| 3 | 185,568 | 74,497 | 82,947 | 82,957 | 8,453 | 647 | 14.595337 |
| 4 | 208,993 | 83,567 | 92,017 | 92,027 | 8,453 | 825 | 19.537124 |
| 5 | 209,824 | 83,448 | 91,898 | 91,908 | 8,453 | 613 | 13.317455 |

本地与 Provider 的差值如实保留，不把本地计数改写成计费值，也不据此猜测隐藏消息结构。此前根任务汇总的 341 次语言调用、18,059,568 input tokens、2,056,141 cached tokens 包含九次开发暂停和多次修复，是 debug 累计量；本表不将它们当速度基准，也不将五份样本推广到所有角色。

## 实际顺序与早变位置

`production_manager.py` 的 Worker 输入构造（约 4860 行）按下面顺序建立字典，`agent_runtime/model_input.py:json_text` 使用 `ensure_ascii=False, separators=(",", ":")` 保留键顺序；五份保存文本逐一重建均完全一致：

```text
work_order
context
working_set
available_tools
tool_schemas
response_schemas
tool_schema_feedback
response_protocol_feedback
goal
context_read（第4、5份出现）
```

`work_order` 内部又把 `working_notes` 放在 `task_inputs` 之后、`scope/constraints/acceptance_criteria` 之前；`status/next_owner/updated_at` 也在整个 context 和工具定义之前。`working_set` 开头是 `total_tool_turns/latest_handoff`，随后才是实体、读取结果和引用。

| 相邻请求 | 第一处不同的 JSON 路径 | 相同 input_text 前缀字符 | 相同 input_text 前缀 o200k | 实际变化 |
| --- | --- | ---: | ---: | --- |
| 1→2 | `/work_order/updated_at` | 2,932 | 1,159 | 首次工作集读取完成后时间戳改变，随后增加读取结果和实体。 |
| 2→3 | `/tool_schemas/qa.inspect_asset_version` 等新键 | 166,215 | 68,568 | 同一回合只发现并追加四个 Tool schemas；work_order/context/working_set 完全相同。 |
| 3→4 | `/work_order/working_notes` | 1,696 | 636 | QA 完成后保存了必要工作笔记，随后更新实际 Tool 回执和观察。 |
| 4→5 | `/work_order/working_notes` | 1,699 | 638 | 裁切完成后更新笔记与结果。 |

这些是**用户 input_text 内**的最长字符共同前缀，再对该子串使用 o200k 计数，不包含顶层 instructions、HTTP 外壳或上游隐藏内容。不是实际 cache breakpoint 的测量。

时间戳、笔记及完成回执变化是正常运行状态，不应为了缓存将它们冻结或删除。可避免之处是它们位于后续稳定知识之前。最后两对请求中，以下块逐值相同，且位于早变字段之后：

| 稳定块 | 本地紧凑 JSON 值 o200k |
| --- | ---: |
| context | 1,994 |
| available_tools | 4,538 |
| tool_schemas | 8,183 |
| response_schemas | 810 |
| goal | 998 |

分块计数不含父键与相邻分隔符，不能直接相加替代整份请求计数。未在本项判断 65k 左右 working_set 的全部正文是否可删；只因块大或读取结果多，不能认定它们无关。

同时，本地 Client 实际先传顶层 `instructions`，随后传 `input`（`providers/openai_compatible_language_client.py` 约 221 行）。所以不是所有稳定内容都被放到了动态状态之后。Trace 外层的 call_id、created_at 也不是该用户 input_text 的前缀，不能误报成它们破坏缓存。

## 官方依据与适用边界

[OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)说明复用针对完整渲染后的匹配前缀，还受可用缓存边界、请求设置和实际缓存条目影响；保持会话或具有相同文本前缀并不保证命中。[OpenAI 提示词迁移指南的 What changes](https://developers.openai.com/api/docs/guides/prompting/migrate-from-prompt-object#what-changes)明确建议稳定内容在前、动态内容在后。

[Anthropic 的 context engineering 工程说明](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)强调管理推理时真正需要的上下文、按需读取及保留必要工作笔记。由此可借鉴的是“保留正确状态，同时管理内容的放置和载入”，不是为维持相同前缀删除新结果、改回旧项目事实，或把所有 Tools 一次装入。

本产品目前经 OpenAI-compatible endpoint 发送顶层 instructions 和一份 JSON 字符串；Tool schemas 是该字符串里的应用合同，不是此次 HTTP 请求的顶层 `tools` 参数。兼容代理如何将这些内容渲染给上游、选择缓存边界或路由到哪台机器，本次没有证据。官方直接 API 的参数与行为也不能直接当作这个账户已验证的能力。

最强反例是 **2→3 的 input_text 已有 68,568 tokens 相同前缀，但第3次 Provider 仍只报 cached input=8,453**。它与 instruction 的本地规模接近，但不能据数值推断“代理只缓存 system”或定位唯一根因。当前也没有调用官方缓存诊断接口或新增缓存参数。

## 最小建议与离线试排

后续可在最终模型投影中研究已知 envelope 的稳定键顺序，把稳定目标、工具目录、响应合同和项目事实放在逐轮状态前；保留所有值、版本、引用及完整持久 Trace。不要按内容猜测删除正文，不更改用户提供的数组顺序，也不为了稳定前缀抹掉 working_notes。

本次只在内存中试排顶层键：`goal/context/available_tools/response_schemas/tool_schemas/work_order/working_set/feedback/context_read`。逐份断言重排前后的解析字典完全相同，未写入产品或发送模型：

| 相邻请求 | 原共同 input_text 前缀 o200k | 试排后共同前缀 o200k |
| --- | ---: | ---: |
| 1→2 | 1,159 | 11,945 |
| 2→3 | 68,568 | 12,432 |
| 3→4 | 636 | 17,171 |
| 4→5 | 638 | 17,173 |

五份请求的**总 o200k tokens 均未变化**。schema-only 的 2→3 反而缩短共同前缀，说明不能不分更新形态机械地把所有 schemas 提到最前；这个试排是取舍展示，不是待部署实现。

值得下一次受控加载做小范围设计与回归，但不是当前 debug 恢复的阻断项。若将来实施，应验证真实最终输入等价、引用可解析、预算计数准确，再在实际运行中分别记录 cached usage 和耗时；不能从此次离线前缀数字预报提速百分比或成本下降。无需为此新增独立 benchmark、额外模型轮次或缓存状态系统。
