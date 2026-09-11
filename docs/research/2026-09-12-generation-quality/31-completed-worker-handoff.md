# 完成工单结论跨规划与相关 Worker 的交接

日期：2026-09-12。范围是《一起签》视频前调试中的一次真实上下文丢失及其本地修复；并非新空项目验收，也不代表视频质量已验收。

## 实际现象与来源

只读来源为 `output/quality-e2e-2026-09-12/runtime/short-drama.sqlite3`，project `project-5b8286dc63e6`、job `studio-job-20f086971171`、run `agent-run-11e17815d7904736820b0821054adb3a`。使用本地 backend Python 的 SQLite `mode=ro`、`query_only=ON`，没有写运行数据库、追加消息、暂停任务或调用 Provider。

以下时间均为北京时间（UTC+8）：

| 时间 | 保存的事实 | 后续模型实际得到的内容 |
| --- | --- | --- |
| 04:42:37 | 纸笔 Worker `work-order-12f15f76ccb84cf1b6346a8ab5374c03` 为 COMPLETED，结果 `agent-result-ccef01f5b6124dc083772e9601b181ad` 完整保存 | 04:42:48 的 Leader 输入仍含完整完成结论与 `state_image_map` |
| 04:56:09、05:01:34 | 同一结果仍在数据库；没有变回待检查 | 对应 Leader 输入仍可见完成结论 |
| 05:08:44 | 数据库中原工单仍然完成 | `20260911T210844894662-2f6df7fd-model.input.json` 已不含该工单、完成摘要或状态图映射 |
| 05:35:19 | 同一已完成状态保持 | `20260911T213519301835-481cb8d3-model.input.json` 同样不含该结果；工件摘要只有当前 16 项，working_set 当时仅保留 READ 观察与引用 |
| 05:37:24 起 | 新规划 Worker `work-order-5aa6b485cec14f60acdf9803f9d69ee1` 开始 | `20260911T213724900488-d041c896-model.input.json` 的 task_inputs 带了十个具体图像版本 UUID，却没有原工单完成结论、状态映射或结果 ID |

这些 model input 文件均在项目 `studio-trace/studio-job-20f086971171/` 下，文件名使用 UTC。完整完成回执明确记录了“6 张摄影参考、4 张准确文字图版均已保存并检查”，情况说明的双空白、仅复核签字、双签三种摄影分别绑定 `asset-version-a9fb492dce6b`、`asset-version-1156f46e26af`、`asset-version-3adcb6b5365e`；报价摄影为 `asset-version-d99209b73470`，黑笔带帽与脱帽分别为 `asset-version-c10cf71df108`、`asset-version-f4936a914dc3`。每个状态都有具体用途与采用结论，DRAFT 并不使它们失效。

原纸笔 Worker 的实际 `qa.inspect_asset_version` 分布于 t7/t11/t25/t33/t42/t49；最终报价检查推荐使用，分数 4.6，指出红色文字的印刷感这个具体限制。新 Planner 却继续说原工单尚需检查，t6 调用 `qa.list_visual_input_reports` 试图找纸笔审图。该工具只列参考重排、关键帧比较和输入兼容性三类报告，不含前述语义图片检查；空列表不能证明图片没检查。新 Worker 一度还把已完成的分镜工单 ID 当作纸笔责任方。这属于完成事实丢失后的事实倒退，不是应该增设审批门的证据。

本轮另一个 `UNKNOWN_WORK_ITEM` 由两个实际制作项处于其他父 item 导致，属于归属说明问题，由另一个窄修复处理；不把它归因于本次完成回执丢失。

## 根因与成熟公开方法

`agent_runtime/context.py` 的默认 `recent_action_limit=8` 让较早的 Delegation Action 及其 AgentResultDigest 一起离开正常模型输入。原文始终存在数据库中；但是此前 `leader_read_working_set` 只保留成功的 READ Action，无法保留完成交付语义。剩下具体文件 UUID、索引和 DRAFT 状态，不能替代“哪次检查针对哪个状态、由哪个工单完成、有哪些限制”。因此只提高摘要字数或扩大最近轮数都没有处理这个生命周期接缝。

采用的公开依据是 Anthropic 的 [有效上下文工程](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) 与 [面向 Agent 的工具设计](https://www.anthropic.com/engineering/writing-tools-for-agents)：正常输入保留当前任务所需事实，完整内容以轻量引用按需访问；返回内容应直接支持后续任务判断。本地采用点是“完成交付工作集 + 精确原文回读”，不复制所有 Worker 对话，不推断 MiniMax Design 的私有实现。

## 最小实现

`agent_runtime/leader_context.py` 新增共享的 `completed_work_handoffs`：

- 仅从同一 Run 中当前状态为 COMPLETED 的 WorkOrder 读取其实际 COMPLETED、非 stale AgentResult；PARTIAL、CANCELLED、其他 Run 与失效交接不升级为完成事实。
- Leader 获得这些完成交付的工单、角色、完成时间、摘要、具体 deliverable/检查结论/限制与准确 ArtifactRef。它们不随最近 8 个 Action 滚动消失。
- 原 AgentResult 保存为既有 context snapshot，给出 `agent.read_context` 的 `/result` 精确入口。正文总预算 16k tokens，优先最近结果；放不下时保存引用，不切坏 JSON。正常整体输入继续经过既有 ModelInputCompiler。
- `LanguageSpecialistAgent.execute` 每次准备模型输入时复用同一函数，以当前 scope、task_inputs 及已读具体对象/媒体 ID 精确匹配相关完成结果。仅共同持有 AssetPlan、整份脚本或项目引用，不会引入所有完成工单。
- 若交付数组及具体文件回执中的行明确属于其他对象或媒体则不内联；没有对象 ID 的共同限制仍保留。例如“静态检查不能证明动态书写”不会因为缺少 ID 被当作不相关信息删除。完整未投影结果仍可回读。
- 共享计划实际返回的 `asset_plan`、`production_document`、`production_document_version` 等别名和全局 Bible 引用，使用同一排除集合；它们出现在 scope、task_inputs 或 deliverable 的标准 ArtifactRef 中，都不能单独触发整份媒体完成结果交接。
- 不修改原工单 task_inputs、当前目标、素材、采用或审批状态。完成检查只是当时的判断，后续文件缺失、来源变更、新的制作需求仍需按实际事实处理。

Arendt 同步把 `completion_handoffs` 加入既有 ModelInputCompiler 的单项正文及集合外置范围，没有新增读取工具。当前已运行 Worker 不需要人工追加消息：在加载新代码后的下一次正常 `execute` 决策中即可获得相关完成事实；已经发出的模型请求不会被事后改写。

## 回归与边界

专测 `backend/tests/test_completed_worker_handoff.py` 覆盖实际 Leader `propose_plan` 请求、实际 Worker `execute` 请求、12 个后续 Action 与 replan/restart、相关与不相关媒体过滤、无 ID 共同限制、分页 snapshot 重启读取，以及取消/失效/部分完成/跨 Run 排除。`backend/tests/fixtures/completed_paper_handoff.json` 是此次真实完成回执与接收 task_inputs 的 18 KB 裁剪片段；六行状态映射、13 份实际完成回执和接收方精确媒体 UUID 保持原值，本地回放验证六行逐字段进入 Worker 输入，不是只检查 helper 返回字符串。

测试使用已有 Fake Client、本地临时数据库；没有真实语言或媒体调用。执行命令（在 backend 目录）：

```sh
.venv/bin/python -m pytest tests/test_completed_worker_handoff.py tests/test_leader_read_context.py tests/test_harness_tool_context.py -q
.venv/bin/pyright short_drama/agent_runtime/leader_context.py short_drama/production_manager.py tests/test_completed_worker_handoff.py
```

验证：完成回执过滤版与既有 Leader/Worker 上下文联合 **24 passed / 14.90s**；最后共享计划别名差异补入后，最终专测 **9 passed / 1.12s**。三个所改 Python 文件 Pyright **0 errors**，限定 diff check 通过。Arendt 独立只读复核已完成，特别核对了具体回执不回流未选中媒体、计划别名不扩大范围、无 ID 限制和完整 snapshot；未发现剩余具体阻断。根代理另运行 PM/Studio 联合，不在此重复。

源数据的真实视觉检查与采用来自原 Worker 保存的完成回执；此修复不重新做 QA、不把本地图版当摄影、不将静态检查升级成动态书写或听审证明。下一次真实恢复是否减少回读、避免错误等待，仍需从实际 Trace 验证，不将 Fake 回放写成产品速度收益。
