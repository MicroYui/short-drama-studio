# 已保存结果的完成交接

2026-09-12。Fresh3 剧本《签完再走》（项目名“第三轮空项目”）已自然失败，本项只回放保存记录、修改独立工作树及运行本地 Fake 测试；没有恢复项目、新增 Provider 请求或修改运行数据库。

## 实际问题与边界

人物工单 `work-order-05f4de0c2cd548a6bc7c14005631034a`（林夏）与 `work-order-505b14867dd04aec9942d985e549e437`（周启）均已保存身份母版、本地脸部裁片、真实审核和参考用途。最后交接仍由模型逐条抄写版本、路径、尺寸、审核结果及用途。两次真实 final 可见输出分别为 2,504 / 2,596 个 o200k tokens，共 5,100；两次模型请求合计 85.2077 秒。该耗时包括实际模型生成过程，不能当作本修改可全部消除的时间。

实际 final 模型调用为 `model-afdfe0aaf2ff` 与 `model-2320b0ce3c3b`。主树 ignored 依据是 `output/quality-e2e-2026-09-12/observations/fresh3-early-latency-source.json`，可复现裁剪记录为 `backend/tests/fixtures/fresh3_completed_character_tools.json`：保留两个原 final、成功 Tool 序号、精确版本、现成 QA、ReferencePack/Entry 和原 image_task，移除无关项目聚合，路径归一到 fixture；本地测试生成的小图片只验证文件可读，不能替代原图视觉观察。

原 schema 要求结构化 AgentResult；工单 informal `expected_output_schema` 又要求 selected_media、visual_findings、continuity_anchors 等。最终输出将 Tool 已保存事实与模型应负责的新判断混在一起。最近同种 Tool 的 checkpoint 也不足以表示两次独立 `add_reference_entry` 的全部来源。修改针对该接口分工，不认定重复出现的任何 ID 都是错误，也不删除最终报告或完整 Trace。

## 最小实现

在既有 `AgentWorkOrderResultRequest` 添加可选 `saved_tool_result_sequences: list[int] = []`。Worker 可以引用本工单索引中的成功 `source_sequence`；状态、简要完成判断、额外判断、限制、尚未解决项及后续建议仍由模型给出。正式自定义输出 schema 和原 COMPLETED 校验继续执行。省略新字段时旧调用语义不变，分析任务不必生成媒体或选择保存序号。

runtime 对指定序号查同一 WorkOrder 的已持久成功回执，核对其 ArtifactRef，按实际不可变 AssetVersion UUID 补历史缺失数字版本，之后复用原完成登记检查验证记录和文件。不会取 current/latest 替代原版本，不将排队任务当成生成媒体，也不会把 Tool 成功当成语义完成或 QA 通过。

结果仍使用已有 `AgentResult.completion_receipts`，包含：

- `saved_tool_result`：显式选择的成功序号、Tool、时间、精确 ArtifactRefs 与原文快照地址。
- `saved_media_metadata`：精确版本的媒体信息、状态及已有制作参数。复用 `asset_construction_state`，保留裁切、字版、参考表、派生关系和本地合成参数，不复制 Provider 长提示词。
- `saved_media_observation`：本工单对这些实际所选版本已有的 QA/登记用途；不同 review scope 分别保留，不能以一个用途的通过代替另一用途。完整几何和回执可以回读。
- 每份交接一条 `saved_result_read`：复用现有 `context_read_contract`；各来源只保存 `context_ref` / JSON pointer，避免每条重复展开同一读取说明。

精确所选 refs 进入 Action、checkpoint、Leader 完成交接与任务页面“已登记结果”计数；不改写 `created_artifacts` / `updated_artifacts` 来冒充新建事实，也不把所有成功动作自动视为交付。原始模型报告、Tool 记录与快照保留。跨 Worker 只投影相关对象；选择母版不会通过子裁片的 lineage 反向选中所有衍生图，选择子裁片仍可看到其真实来源。无对象 ID 的共同限制保留。

成功序号索引按同一精确产物集合复用最新来源地址，不同版本与不同保存对象不按固定 last-N 丢弃。项目级宽引用不单独进入索引；较长索引沿用 ModelInputCompiler 的持久分页，600 个独立镜头来源的本地反例可在预算内回读全部内容。

## 公平回放与体量取舍

下表两路都使用同一裁剪 fixture、同一 submit 校验与 `completed_work_handoffs` 投影。原 final 原样提交；新 final 是人工写的协议示例，通过 Fake LanguageClient 进入实际 Worker 和 runtime，并非重新调用模型得到的输出。o200k 统计序列化文本，临时路径与运行 ID 可能带来少量差异。

| 角色 | 原真实模型可见输出 | 人工最小协议示例 | 原完整 AgentResult | 新完整 AgentResult | 原 Leader 交接及 refs | 新 Leader 交接及 refs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 林夏 | 2,504 | 132 | 2,792 | 4,303 | 2,788 | 4,430 |
| 周启 | 2,596 | 132 | 2,892 | 4,319 | 2,853 | 4,447 |

新输入确实更大：runtime 带入了旧手写报告没有完整保留的审核用途 scope、实际登记描述与制作参数。已合并重复读取说明，但保留相关质量限制；没有把旧完整长报告和同份全量 Tool 结果再双份注入。Leader 沿用完成交接 16k 预算和原文外置，接收 Worker 按实际 scope 过滤。此回放证明协议可不手抄元数据，不能证明真实模型必定输出 132 tokens、总 token 减少或节省 85 秒。真实完成表达、速度和模型是否正确使用新字段仍待新任务验证。

## 公开方法与本地采用

[Anthropic 的工具设计文章](https://www.anthropic.com/engineering/writing-tools-for-agents)强调让工具返回与任务相关、清晰且节省 token 的信息，并用真实任务和调用记录评估。本项借鉴其职责划分：保存系统已经知道的事实由 runtime 提供，模型保留判断责任；它没有规定本产品的序号字段。

[Anthropic 的 context engineering 文章](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)讨论按需获取信息、持久笔记与长期任务上下文管理。本项直接复用已有快照与分页，不新建历史状态库，也不要求每次交接强制回读。公开资料不证明 MiniMax Design 或其他闭源产品采用同样的内部实现。

## 本地验证与整合

新专项覆盖两名真实角色经实际 Worker 输入/submit、原 final 兼容、精确媒体/QA/用途、任务面板计数、12 个后续 Action 后的实际 Leader 输入、重启/原文分页、PARTIAL 和正式自定义 schema、无效序号/失败回执、批量参考条目、600 项索引预算，以及母版/子裁片的正确方向过滤。原始 source 和用户的视频前边界不变；没有真实媒体或 Provider 调用。

独立树 `backend` 中执行：

```sh
.venv/bin/python -m pytest tests/test_saved_completion_results.py tests/test_completion_receipts.py tests/test_completed_worker_handoff.py tests/test_partial_media_handoff.py tests/test_harness_tool_context.py tests/test_studio_method_activity_drafts.py -q
.venv/bin/pyright short_drama/agent_runtime/completion_saved_results.py short_drama/agent_runtime/leader_context.py short_drama/agent_runtime/model_input.py short_drama/production_manager.py short_drama/studio_activity.py tests/test_saved_completion_results.py
```

最终 15 项新专项与相邻 5 套合计 **54 passed / 10.65 秒**；上述 6 文件 strict Pyright **0 errors / 0 warnings**，限定 diff 检查通过。独立复核关闭了母版经嵌套 lineage 误命中子裁片的接缝，并核对原文回读和公共读取说明，没有新增运行调用。

依赖 root 的 `asset_version_context.py`（`f1aec07`）和批量参考条目的公共 `media_turn_observations`（`3baa169`、`a4871b2`），不重复提交这两个依赖文件。规划侧 expected_output 职责说明由 `a3d9d56` 提供；此项不另改角色 Skill 或扩大任务边界。
