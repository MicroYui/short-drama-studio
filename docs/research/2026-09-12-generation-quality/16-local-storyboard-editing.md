# 局部分镜结构编辑：修复一次真实无法完成的工具调用

更新：2026-09-12。当前实现已完成本地集成与 Fake 回归；实际项目仍保留原分镜，未调用 Provider、未写运行数据库。后续由产品 Agent 在恢复后的任务中选择如何修改，本文没有预写本片替代分镜。

## 真实问题与根因

《一起签》调试任务中，导演读取 14 镜分镜后判断第 10、12 镜需要拆开动作与结果画面。当时 `shot.patch_storyboard` 只支持同 ID 镜头替换，不能插入或删除镜头；其 schema 指示结构改动使用完整 `shot.replace_storyboard`。轻量工具目录只有 ID 等元数据，没有用途说明。

以下是实际完成回执，时间为本地 UI 时间；输入、输出均来自 Provider 报告，不是估算：

| 实际调用 | 完成时间 | 输入 / 输出 tokens | 耗时 | 结果 |
| --- | --- | --- | --- | --- |
| `model-0de7e695429e` | 03:25:04 | 68,828 / 2,354 | 58.550198 秒 | 请求 `shot.patch_storyboard` schema |
| `model-83d2900d4238` | 03:25:28 | 72,103 / 1,000 | 23.542980 秒 | 发现 patch 无法拆镜，再请求 replace schema |
| `model-9897a10799d2` | 03:30:39 | 75,183 / 20,000 | 310.652894 秒 | 重写全片时 `sse_incomplete_max_output_tokens`，未形成可执行的工具请求 |

前两次共 82.093178 秒。对应输出及下一轮输入中 `response_protocol_feedback`、`tool_schema_feedback` 都为空，因此这是两次正常但可避免的能力发现，不是参数错误修复或重复执行 Tool。第三次并未保存正式分镜；`worker.error` 事件 2426 后，根任务暂停本次调试，H3 提交数仍为 0。

实际记录位于 `output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/`。本次修复对象是“小范围结构改动被迫输出全片，导致调用无法完成”，并非单纯扩大输出额度。

## 成熟实现的可借鉴部分

ProseMirror 官方 `ReplaceStep` 将编辑表达为一个范围及其替换内容，再以步骤应用、失败或映射处理；其文档说明了如何在现有文档上应用和重新定位编辑。这里采用范围编辑和整次验证的思路，但使用项目已有的稳定镜头 ID 与插入锚点，不引入另一个编辑状态、OT 系统或节点画布。[官方 ReplaceStep 源码](https://github.com/ProseMirror/prosemirror-transform/blob/master/src/replace_step.ts)、[官方变换指南源码](https://github.com/ProseMirror/website/blob/master/markdown/guide/transform.md)。

Anthropic 的官方工具设计文章强调围绕真实工作构造工具、减少不必要的串联调用、让输入输出范围清楚，并用实际轨迹、错误和用量评估。其按需工具加载说明也指出工具用途必须可发现，而不需要预载所有完整 schema。这里保留已有 registry 与按需 schema，在同一个局部编辑工具上补结构能力，并在目录中提供短用途。[Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)、[Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)。

这些公开实现支持本次工程取舍；它们不证明 MiniMax Design、即梦或 Kling 内部采用相同机制。闭源产品的私有调度、分镜存储及编辑协议仍未知。

## 当前实现与使用范围

沿用 `shot.patch_storyboard`，不新增一套工作流。`StoryboardPatchRequest` 新增两个可选列表，旧请求保持兼容：

- `shot_splices`：用原分镜中连续且有序的 `shot_ids` 指定替换范围，`shots` 只包含该范围的新内容。一个镜头替换成两个就是拆镜；多个替换成一个就是合并。`shots=[]` 删除所选范围。`shot_ids=[]` 配合 `after_shot_id` 插入；锚点为空表示开头。替换中的每个镜头仍需满足完整 `ShotPlan` 契约。
- `clip_group_splices`：仅需重新分组时，给出原来的连续 `clip_group_ids` 和新的 `clip_groups`。普通组内拆镜或插入自动更新该组成员；跨组改动必须明确受影响组的替换，不能猜测新组边界。新增组不要求重输其他组。

例如，同时拆第 10、12 镜，只需要提交这两个原镜头 ID 及各自两个完整的新镜头；其余 12 镜从当前文档保留。序号、实际内容修订号、分组时长和场次索引由代码维护。对白归属、逐时段窗口、参考图用途和动作设计仍由创作者或 Agent 提供，代码不会替他们编写创作内容。

完整重写仍用 `shot.replace_storyboard`。同 ID 的普通编辑继续兼容 `replacements` / `shot.save_current`。导演实际角色片段已说明局部拆、插、删和重分组优先使用局部工具，避免发现新字段后仍默认重写全片。

主要入口：

- [结构请求与兼容合同](../../../backend/short_drama/production_contracts.py)：`StoryboardShotSplice`、`StoryboardClipGroupSplice`、`StoryboardPatchRequest`。
- [局部应用与读取基线检查](../../../backend/short_drama/storyboard_patch.py)：`apply_local_storyboard_patch`、`validate_storyboard_patch_base`。
- [现有项目保存入口](../../../backend/short_drama/service.py)：`patch_storyboard`。
- [工具用途](../../../backend/short_drama/tools/shot/__init__.py)、[导演职责提示](../../../backend/short_drama/skills/shared/roles/director.md)。

## 并发、时窗与实际媒体保持

所有 splices 都寻址同一份原文的稳定 ID，不使用先前插入后已经偏移的数组位置。范围交叠、借用范围外的已有 ID、删除另一个插入锚点、重复输出 ID 会在保存前拒绝。最终仍以 `StoryboardContent` 验证每条对白的顺序与唯一归属、组对镜头的完整覆盖、场次归属及镜头和组时长。

当 `base_version_id` 落后时，只检查本次实际改动的镜头、插入位置、受影响组和指定元信息，再在最新文档中合并。无关镜头的人工修改得以保留；同一镜头或插入间隙已经变化时返回明确冲突。读取、校验和项目保存沿用现有短锁，不在锁内调用语言模型。

仅镜号顺移不增加镜头 `revision`。未改镜头的正文、具体参考图、规划记录和已选 Take 保留；受影响组的时窗由现有生成计划编译重新计算，其他生成单元保持原输入。H3 组合操作的缓存比较也同步排除两个纯展示字段：`locked_shots[].locked_shot.sequence` 和 `previous_clip_planned_terminal_state.sequence`。真实镜头 ID、修订、时窗、设计终态、实际前序媒体、参考与文件状态继续参与比较；原始输入仍完整保存，未从 Trace 删除序号。

Fake 验证实际执行了“准备末组 → 前面拆镜 → 按原 operation ID 恢复”：语言编译总共一次，route ID、generation plan version 和 operation ID 不变，没有创建视频任务。这只证明无关组能复用，不代表修改组的视频可以继续当成当前结果。

## 输出中断及恢复边界

当前 Worker 请求的 `max_output_tokens` 仍为 20,000，本次没有提高上限。Provider 的不完整响应在客户端抛出 `LanguageProviderIncompleteError`，没有将半段 JSON 当作 Tool 执行；草稿和实际失败仍保留在 Trace。

完整返回但协议不合法的响应已有结构化反馈，Worker 可根据错误修复。Provider 截断发生在完整解析之前，现有路径保存工单进展并将责任交回 `LEADER_REPLAN`；它不能自动拼接缺失的 JSON。恢复时已发现的 Tool ID 从当前 registry 重新加载 schema，因此旧导演工单会拿到新增局部字段，无需手改数据库。是否实际改用局部能力及能否一次完成，仍需恢复后的真实轨迹确认。

## 本地体量对比与验证

从暂停项目只读提取同一份 14 镜分镜，在内存中做两处等结果拆镜得到 16 镜；分别序列化局部请求与同结果完整替换请求。临时拆镜内容仅用于合同和体量比较，没有保存为本片正式内容。

| 请求 | 字符数 | o200k 估计 tokens |
| --- | ---: | ---: |
| 两处局部拆镜 | 6,188 | 4,046 |
| 同结果整份替换 | 32,036 | 19,983 |

该例结构请求的估计 tokens 减少 79.75%。这是确定性文本体量，不包含模型推理，不是 Provider 实际 usage，也不能换算成已验证的速度或成片质量收益。

139 项工具的完整轻目录增加用途首句，最多 64 字符（含截断符），不包含完整输入输出 schema。以相同序列化方式计数，目录从 5,140 增为 8,162 个估计 tokens，增加 3,022；完整细节仍按需加载。patch 首句已能区分拆、合、插、删与全片重写。这个开销需要在后续真实使用中与减少的发现往返一起评估，不隐藏为无成本改进。

数据与口径：[本地体量记录](../../../output/quality-e2e-2026-09-12/local-storyboard-editing-volume.json)。

本轮联合回归 **56 passed**，覆盖局部拆镜、开头/中间/结尾插入及删除、显式重分组、跨组约束、非法请求原子失败、两个并发局部编辑、人工修改保留、相同插入位置冲突、场次索引同步、参考/Take 保持、H3 原操作复用及按需 schema。命令在 `backend/` 执行：

```sh
.venv/bin/python -m pytest tests/test_storyboard_local_edits.py tests/test_storyboard_contracts.py tests/test_storyboard_agent_interfaces.py tests/test_storyboard_clip_group_invalidation.py tests/test_h3_group_operation.py tests/test_production_manager.py::test_language_planner_preloads_small_hot_set_and_discovers_long_tail tests/test_production_manager.py::test_language_specialist_receives_append_only_work_order_progress -q
```

相关 7 个源文件/专用测试文件 Pyright 为 0 errors、0 warnings；本次修改的 diff whitespace 检查通过，文档本地链接均可解析。本轮未调用真实 Provider。新字段需要随正常 OpenAPI/前端类型生成和服务重启加载。修复后继续同一调试任务不算全新无人干预稳定性验收；完整视频前链路仍须按用户要求从新空项目运行。
