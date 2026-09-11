# 保留工单在 Leader 连续读取期间未获得执行机会

2026-09-12。本项由《一起签》的实际视频前调试触发，修复范围是 Runner 调度顺序。未调用 Provider、未修改运行数据库，当前案例的修复续跑不作为全新项目稳定性或性能收益证据。

## 实际观察

项目 `project-5b8286dc63e6`，制作任务 `studio-job-20f086971171`。北京时间 02:57:11 恢复至 03:00:56 暂停期间，两个既有工单始终保留在新计划中，但停在 `NEEDS_ATTENTION / WORKER_TOOL`：

- 分镜导演：`work-order-e523f7976b014a3aa367ec1ffb4185b1`。
- 纸笔道具制作：`work-order-12f15f76ccb84cf1b6346a8ab5374c03`。

r4 安排两次读取，r5 查询 H3 能力，r6 再安排两次读取。该区间只有 Leader 的 5 次 Tool 完成记录，没有 `worker.updated`。按 `provider.language_generation` 完成回执计数，共 6 次语言调用，Provider 报告输入 146,421 tokens、输出 9,805 tokens，语言调用累计耗时 219.45993 秒。`model.started` 包含开始或等待的请求，不用它替代完成回执统计。

r7 最终产出一个新的素材规划委派，暂停发生在执行该委派之前。因此实测证明的是这段时间旧 Worker 未恢复，不能称模型永远循环。连续就绪读取可以持续挤压 Worker，是下面可复现的代码路径；重复读取的上下文原因由另一项修复处理。

## 一手实现提供的调度经验

[Dask Futures](https://docs.dask.org/en/stable/futures.html) 的 `as_completed` 按完成顺序交出结果，允许迭代期间添加新的 Future。任务提交、完成结果收取和追加后继是可分别推进的动作，不必等调用者重新描述整批任务。

[AutoGen Core 的 Concurrent Agents 示例](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/design-patterns/concurrent-agents.html) 展示多个处理器独立接收任务，并把响应交给结果队列；处理结果可以按实际到达顺序被收取。[消息通信文档](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/framework/message-and-communication.html) 则明确区分消息投递与等待请求响应。

Python 的 [Future.set_exception](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Future.set_exception) 说明执行器应把异常写入 Future，使等待者能取得终态；[asyncio.CancelledError](https://docs.python.org/3/library/asyncio-exceptions.html#asyncio.CancelledError) 也采用 `BaseException` 继承以区分取消控制与普通操作失败。本产品的 `ExecutionYielded` 是自己的控制信号，不能假定 `except Exception` 会捕获它；本次只显式传递该信号，继续由原 Runner 处理暂停或新消息。

这些公开机制支持本次局部选择：给既有可执行工作派发与结果收取的机会，不能让一个协调者不断安排自己的下一次判断而绕过它们。这不是上述框架对本产品公平性、恰好执行一次或性能的保证；本地仍复用现有工单、Tool 回执和线程执行器，没有引入新框架。

## 根因与最小修复

`StudioAgentRunner._advance_until_handoff` 发现就绪步骤后，原来调用 `advance_agent_run(advance_workers=False, allow_replanning=True)`，随后直接进入下一轮。`ProductionManager._advance_unlocked` 执行完最后一个 Tool 后，即使本次动作额度已经用完，仍可因本地计划完成而自动请求下一份 Leader 计划。若返回又有读取步骤，Runner 再次进入相同分支，始终无法到达后面的 `advance_agent_team`。

现将这一分支限定为执行已经写入的一个就绪动作，传入 `allow_replanning=False`；随后继续执行已有的非阻塞 Team 派发和收取。原 Worker 依照保存的 Tool 请求恢复，保留工单 ID、来源 Action、参数和结果，不重新委派，也不生成第二份媒体任务。

回归同时暴露相反方向的问题：仅禁止内部重规划，会让仍在运行的 Worker 挡住这次读取或保存之后的后继判断。现复用已有 `CompletionPlanningInput` 和后台 Future，区分两种触发：部分 Worker 真正完成时按原规则追加；非空本地计划全部执行完而还有相关工单在途时，后台进行本次普通后续规划。后者的模型请求也不持运行锁。

保存前重新核对计划和用户要求，只继承当前仍相关的在途工单；模型等待期间已经完成的工单及结果保留，完成 ID 不再冒充在途 ID。新增步骤可以依赖保留的已完成步骤。若 Agent 决定等待，则保存没有本地步骤的新计划并保留在途工单，因此相同读取结果不会按 tick 再次触发。暂停、用户消息或计划变化仍丢弃旧提案。本次必须继续的规划失败交给 Runner 原有有限连接重试或错误状态，不吞错后无界重问。

独立复核补齐两个实际竞态。第一，控制边界的 `ExecutionYielded` 继承 `BaseException`，后台线程必须显式将它交给 Future；否则线程已退出，但 Runner 会一直等仍标为运行中的 Future。现在暂停和新消息都能正常收取这一控制结果。第二，Leader 等待期间刚完成、尚未被本次输入看见的旧工单，与新计划在同一事务中交接归属，保留完成状态、原 Action 和结果，且不提前写入已消费集合。它在空等待计划保存后仍可触发一次真实完成事件，不会被另一个慢工单挡住。

这里的两个触发均要求真实保存的完成状态。Worker Tool 中间结果本身不触发规划；若中间结果恰好被收取时本地计划已经完成，走上述后台路径。若本地计划还未完成、已经明确完成目标或已保存空等待计划，PM 的实际计划和 carried-state 判断不会因这个中间结果单独发起完整规划。明确失败交接仍走原路径，本项没有把所有重规划入口迁移成异步，也没有修改并发上限、媒体查询时刻和审阅范围。

## 验证与边界

新增 `backend/tests/test_carried_worker_scheduling.py` 使用真实临时 SQLite、Runner、ProductionManager、ToolRegistry 和本地 Fake 语言客户端，保存一个已经决定但待恢复的 Worker Tool，再给 Leader 一串就绪读取计划。初始红测在旧代码中稳定观察到三次 Leader 规划都看见原工单尚未完成；只有 Fake 最后改成空等待计划，Worker 才恢复。最终回归进一步让 Worker 等到 Leader 请求开始，再要求该请求返回前 Worker 的原 Tool 和完成结果真正落盘，验证两个方向都不被锁或派发顺序阻断。原 Tool 只执行一次，工单来源与 ID 不变，没有创建视频任务。

另核对新后继可以依赖已完成读取、空等待跨服务重建不会重复触发、同一旧提案不能二次应用。既有 `test_ready_leader_work_prepares_parallel_post_while_retained_worker_waits` 保留实质断言：慢候选尚未返回时，保存独立准备并规划的后期分工已经执行。

第一轮完整定向回归：新 carried 调度用例 3 项，加上 `test_incremental_worker_planning.py`、`test_studio_harness.py`、`test_team_executor.py`，共 59 项通过，耗时 98.78 秒。独立复核后追加真实 `execution_boundary` 暂停/消息两项，以及 A 在后台 Leader 请求期间完成、空等待落盘、服务重建后仍能触发后继而 B 尚未完成的一项。最终增量组合包括 carried 4 项、incremental worker 9 项、incremental state 9 项和既有反向饥饿用例，共 23 项通过，耗时 32.23 秒。相关 PM、Runner 与测试 Pyright 无错误或警告，`git diff --check` 通过。代码已冻结，服务由主任务在记录后统一加载。

这只证明所测调度路径不再被连续读取阻断。实际任务是否少读、合理并行、缩短时间，以及最终资产和影片质量，需要新的空项目视频前端到端验收；当前调试暂停、修复和恢复时间全部保留，不能算作性能改善。
