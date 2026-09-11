# 重启恢复不能用旧回执覆盖当前工单交接

日期：2026-09-12。范围：Worker 重启恢复、待执行 Tool、Leader/用户交接与 Action 完成登记。沿用当前 WorkOrder、AgentResult、Action 和进度记录；不增加调度状态模型。

## 真实问题

《一起签》开发调试任务 `studio-job-20f086971171` 的导演工单 `work-order-e523f7976b014a3aa367ec1ffb4185b1` 在 03:30:39 因 `Language generation incomplete: max_output_tokens` 交回 Leader。实际 `worker.updated` 保存的是 `NEEDS_ATTENTION / LEADER_REPLAN`；此前最后一次成功的 `shot.get_storyboard` 回执仍为 `PARTIAL / WORKER_DECISION`。

03:50:39 重启恢复后，没有新的 Leader 模型请求，该工单直接恢复成 `WORKER_DECISION`。首个模型请求 `model-f51722b8e67b` 是原导演。之后新局部工具使其两次拆镜成功，但这不能证明原来的 Leader 交接被正确处理。

证据位于 `output/quality-e2e-2026-09-12/runtime/short-drama.sqlite3` 的 `studio_events`、工单和进度，以及同目录输出项目的 `studio-trace/studio-job-20f086971171/`。调查使用 SQLite `mode=ro`；本修复没有修改该调试项目，也没有触发任何 Provider。

## 一手参考与采纳

[LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) 区分当前状态检查点与任务级持久输出：恢复可以复用已经完成的任务输出，而不重跑它们。[Temporal Events and Event History](https://docs.temporal.io/workflow-execution/event) 将完成、失败、取消等执行进展纳入持久历史，用于恢复执行状态。这些做法的共同启发是保留已完成工作的结果，同时恢复到已经到达的执行状态；一次较早的成功回执不能消除后来的失败或取消。

本项目已有更简单的适用机制：`AgentRuntimeStore.save_agent_result` 在同一 SQLite 事务中写入回执和对应 WorkOrder 状态，后续决策、失败、取消或中断会继续更新同一个 WorkOrder。因而恢复以当前 WorkOrder 为准，回执只在与当前状态相符时用于补齐 Action。无需引入 LangGraph、Temporal、事件重放引擎或新的全局时间戳竞争规则。

## 根因与改动

`ProductionManager._reconcile_delegation_action_after_restart` 原先只要找到 AgentResult，就按该回执决定完成、失败或 `next_owner`；PARTIAL 分支无条件重写 WorkOrder。实际失败处理 `_return_specialist_failure` 会保存较新的工单和错误进度，但不产生新的 AgentResult，因此被旧 Tool 成功回执覆盖。

现在：

- WorkOrder 的 `CANCELLED` 优先生效，不被旧 COMPLETED、FAILED 或 PARTIAL 回执恢复成其他状态。
- COMPLETED/FAILED 回执仅在当前 WorkOrder 处于相符终态时补齐尚在 RUNNING 的 Action；正常“已保存结果、尚未来得及完成 Action”的崩溃窗口仍可恢复。
- PARTIAL 回执不再重写当前状态或 owner。当前 `LEADER_REPLAN` / `USER_INPUT` 使 Action 正确交回对应控制方，并使用后续持久进度中的实际错误或问题。修复缺失 Action 链接时生成的恢复说明不会盖掉原问题。
- 已归还的历史 `stale_handoff` 不能决定重新处于有效范围的工单；仍属于旧范围的历史 Action 保持取消语义。
- `_recover_interrupted_operations` 只规范化无法继续运行的进程内状态。REQUESTED Tool 保留原 ID 和 `WORKER_TOOL`；真正 IN_PROGRESS 的 Tool 标记 INTERRUPTED 后交回 Worker 决策，不因旧的“Tool 已请求”回执再次获得执行权。明确的 Leader/用户交接保持。

同时核对了 Store 的旧格式迁移：只有历史记录缺少 `next_owner` 时，才允许从 PARTIAL 回执补全，已有显式 owner 不受影响。正常结果保存的原子事务、Team 按 owner 选择工单、Studio 对 paused 任务不自动启动的既有规则保持。

## 验证与边界

新增 `backend/tests/test_worker_recovery_ownership.py` 使用真实临时 SQLite、实际 Service 重建、实际 ToolRegistry 和本地 Fake 语言客户端。核心用例实际执行当前内容读取，保存成功回执，再抛出与真实案例相同的 incomplete 错误，保存暂停任务并重启；检查原 Tool 不重放、原回执保留、当前 owner 仍归 Leader、Action 带实际错误，并且暂停任务不发起模型请求。

其他用例覆盖最新用户交接、缺失链接时保留错误说明、旧回执之后新请求 Tool 的原 ID 续跑、实际中断 Tool 不自动重发、有效完成/失败/部分结果恢复、取消优先与历史 stale handoff。

18 项新增检查与原有 7 项重启检查联合通过（25 passed，14.41 秒）；既有暂停保留请求、已启动复合 Tool、派发窗口暂停及重启续跑检查 4 项通过（18.28 秒）。PM 与新增测试 Pyright 无错误或警告，相关文件 `git diff --check` 通过。测试使用本地 Fake 推理和真实本地持久化，无真实模型或媒体 Provider 调用。

这属于已观察到的调试缺陷修复。真实恢复效果与完整视频前准备仍待重新加载后观察，独立稳定性验收仍须从空项目重新运行；本地回归不等于完整影片或全链路质量已经通过。
