# 导演输入构成与精确引用消重

更新：2026-09-12。本轮分析读取第四次恢复后的实际语言请求；实施只改变最终模型输入中的重复引用表示，不改变公开 Tool 回执、分镜、任务、历史 Trace 或原始保存结果。未调用真实 Provider、未写运行项目数据库。

## 实际输入的构成

来源为 `output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/` 下的 `model.input.json`。文件保存的是最终请求的 `instructions` 和 `input_text`；`context` 来自 `model_context_dump()`，没有另一份名为 model_context_dump 的独立文件。本次按实际 compact JSON 序列化和本地 o200k 编码计数，未用重新缩进后的 JSON 代替真实输入。

以第二次局部编辑的 `model-333ee6768a52` 为例，原请求估计 81,531 tokens（正文 74,325 + instructions 7,206），Provider 报告 81,541，封装产生的 10-token 差异保留为两种计量口径，不改写实际用量。

| 顶层块 | 独立序列化的估计 tokens |
| --- | ---: |
| working_set | 42,642 |
| tool_schemas，14 个已发现工具 | 15,483 |
| available_tools，133 项短用途目录 | 7,869 |
| context | 5,212 |
| goal | 1,164 |
| work_order | 1,113 |
| response_schemas | 810 |

分块计数不含相邻键名和边界，因此不能简单相加当作完整请求的精确值。最大的工作集进一步分为：

| working_set 子块 | 估计 tokens | 实际内容 |
| --- | ---: | --- |
| latest_handoff | 21,405 | 刚执行的 get_storyboard；其中当前 16 镜分镜正文 20,078，只出现一次 |
| read_observations | 14,423 | 资产分页、项目快照、剧本场次和项目概览 |
| tool_checkpoints | 3,106 | 已执行 Tool 身份、来源轮次、状态和引用 |
| progress | 1,776 | 当前交接、进度及重复引用 |
| entities | 1,054 | 分镜文档/版本摘要和草稿进度，不是完整分镜 |
| artifact_refs | 834 | 当前已知引用列表 |

14,423 tokens 的保留读取中，资产 `agent.read_context` 为 5,834；这是六份资产设定的前 12,000 / 12,415 字符分页。`project.get` 为 5,693，其中同一 Script v3 为 3,097，资产索引及可回读标记为 1,476；场次读取为 1,821；项目概览为 1,074。资产设定对人物、空间和纸笔连续性有实际用途，不能仅因块大就删。

没有发现新旧完整分镜同时回灌：成功写入后旧分镜读取已移除；下一次 `get_storyboard` 才提供当前版本。`patch_storyboard` 后的真实输入也不含 18 镜正文：其 `latest_handoff.result` 只保留状态、引用和 receipt，没有 output；该时刻工作集总计 23,613，而 latest_handoff 仅 1,948。当前 `get_storyboard` 只有 project/version 选择，全文是所调用工具的实际合同，不是额外 current_assets、runtime_state 或 latest-success 副本。

## 研究判断与本轮范围

[此前的工具往返分析](10-tool-contract-and-latency.md) 已区分语言决策、按需 schema 发现和确定性 Tool 续跑；[Leader 正文恢复](11-leader-context-repair.md) 修的是读到的正文丢失。本轮不能为了缩小输入，再删除下一步确实需要的正文或强迫重复读取。

Anthropic 的上下文工程文章强调保留必要的高价值信息，结合明确引用按需取用；按需工具文章也说明了发现成本与预载成本的取舍。这支持先去掉可证明重复的表示，而不是按大小猜测内容不重要，也不支持把其内部性能比例套用于本产品。[Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)、[Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)。

分析还确认 14 个 schema 中有重复的相同类型定义，重复定义本体约 6,497 tokens；同一剧本也出现在 project 快照、场次读取和 context 摘要中。它们不是本轮改动：schema 合并需要保证所有引用的等价性，正文投影需要先证明同版本、完整覆盖和可回读关系。暂未按角色、阶段或历史轮数删 schema，也没有猜测哪些剧本文字可以省略。

本轮只处理已经证明完全相同的 ArtifactRef 列表。例如 18 镜写入后，顶层 result 和 receipt 各有同一份 788-token 引用列表，最新 checkpoint、进度及其 Worker 交接又重复同样值。各项的来源、状态和错误仍有用，重复引用对象不必再次展开。

## 采用实现

[input_references.py](../../../backend/short_drama/agent_runtime/input_references.py) 是纯模型投影。只访问已知的 `working_set.latest_handoff.result` / receipt、tool_checkpoints、progress 及其四类交接记录；不递归扫描用户正文、Tool 输出内容或任意嵌入 JSON。

当一份列表里的每个完整原始 ArtifactRef 都存在于已经可见的引用表时，以 `artifact_refs_from: {pointer, indices}` 指向最终请求内的 JSON pointer 和零起始数组位置，保留原顺序和重复项。比较完整键值，不仅比较 ID；不同项目、类型、版本或不识别的来源字段不会被合并。若某个列表含引用表没有的历史版本，则保留原列表。其他字段，包括 WorkOrder/attempt 来源、错误、receipt current_state 和 changed_ids，全部保留。

调用位于 [ModelInputCompiler.prepare](../../../backend/short_drama/agent_runtime/model_input.py) 完成预算整理之后。只有最后仍为可见列表的 central artifact_refs 才能作为引用表；如果它已被外置，则跳过 central 共享。receipt 仍可指向同一最终请求中完整可见的 result 顶层引用列表。投影之后重新计算 token 数，实际发送文本和 `context.prepared.estimated_input_tokens` 一致。

公开 ToolResult、WorkOrder 的原始 SpecialistTurn、已有上下文快照和 Trace 保持完整。预算外置快照保存的是原始 payload，不会出现指向另一个最终输入的索引。此实现没有改动 PM 的重启恢复函数或其责任归属修复。

## 同源输入测量

只读已保存的五个真实请求，用同样的 instructions 和 payload 运行新的最终输入编译。没有调用语言或媒体 Provider，没有变更项目。每份生成的索引都可展开；展开后的整份 payload 与原请求逐值完全相等。

| 实际请求 ID | 原估计 tokens | 投影后估计 tokens | 减少 |
| --- | ---: | ---: | ---: |
| model-f51722b8e67b | 78,898 | 76,390 | 2,508 |
| model-81163717e1bd | 61,858 | 58,434 | 3,424 |
| model-333ee6768a52 | 81,531 | 78,107 | 3,424 |
| model-8cd80b31f439 | 62,528 | 58,808 | 3,720 |
| model-edb48bbbddcd | 83,037 | 79,317 | 3,720 |

全部测量未触发预算外置，变化仅来自已验证的引用表示消重。完整文件名、分块数值、替换路径和口径保存于 [同源测量 JSON](../../../output/quality-e2e-2026-09-12/director-input-reference-projection.json)。这证明输入体量减少，不证明响应速度、模型正确率或端到端墙钟改善，也不能改写历史 Provider usage。

## 回归

[test_worker_input_references.py](../../../backend/tests/test_worker_input_references.py) 覆盖：完整值匹配；不同版本/项目/类型/额外来源不混用；重复项及顺序；用户 JSON 即使与内部字段共用同一个 Python 对象也不变；central 被实际预算外置后无悬空引用；重启后快照保持原文；最终文本 token 统计一致；实际 LanguageSpecialistAgent 从重开的 Store 构造 Fake Provider 输入，索引可解析而持久 Tool 记录仍完整。

联合运行新用例、既有预算和按需工具上下文用例，命令在 `backend/`：

```sh
.venv/bin/python -m pytest tests/test_worker_input_references.py tests/test_model_context_budget.py tests/test_harness_tool_context.py -q
```

结果为 **27 passed / 3.71s**。两个源码文件及新专测的 Pyright 为 **0 errors / 0 warnings**，相关改动 `git diff --check` 通过。

本轮未复跑真实产品模型。恢复后的实际使用仍需继续观察；同项目调试不作为新空项目视频前端到端验收。
