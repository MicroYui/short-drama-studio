# 就绪 Worker 在已落盘回合后公平轮换

2026-09-12。范围是现有单进程 TeamExecutor 的后台连续执行；并发上限保持 4，不改图片、视频或 QA 的执行协议。真实任务已于北京时间 07:36:56.620 暂停后修改源码，未写运行数据库、调用 Provider 或控制产品任务。

## 实际等待与原因

《一起签》调试任务 `studio-job-20f086971171` 于 07:23:05.145 恢复。事件 6651–6654 显示对峙、收尾、签复核交笔及开场四个 Builder 进入执行。准确文字证据工单 `work-order-a318e1e27639488fab20ba34fc3a80d8` 同属当前计划，状态为 `NEEDS_ATTENTION / WORKER_DECISION`，没有未来轮询时间；截至本轮只读检查的 07:33:55.724，仍没有新的 Worker、Tool 或语言调用，活跃等待至少 10 分 50 秒。暂停期间的时间不计入这个等待值。

这是四槽已占用与连续执行粒度共同造成的等待，未发现该工单丢失或恢复到错误 owner。现有 `collect_ready_work` 为每个工单提交的 Future 一直执行到整个工单交出 Worker 所有权。一次图片操作结束后，仍由同一个 Future 连续执行登记、语言判断、QA 和下一张图片，期间不重新参与就绪选择。

这段实际 `image.advance` 包含约 99–133 秒的同步调用，例如 `tool-6d014cc18a4f` 于 07:23:13.458 开始、07:25:03.493 结束，实际记录耗时 110.030116 秒；四次已完成 QA 分别约 117、255、165、188 秒。Tool 等待是真实慢操作，不是空转。当前图片请求在 HTTP 返回之前没有可供另外一个执行单元接管的完整结果，不能把它冒充已经受理的异步任务。已有 H3 远端轮询则有精确任务身份及 `continuation_not_before`，原先就能释放执行槽并到期恢复。

## 一手参考与本次采纳

Python 官方 [concurrent.futures](https://docs.python.org/3/library/concurrent.futures.html) 区分提交的 Future、已运行调用和完成结果；取消不会停止已经运行的调用，结果可按实际完成情况收取。本次因此不试图在线程等待媒体时抢占或取消它，而是在已经返回并保存的操作之间结束本次 Future。

Microsoft 官方 [Asynchronous Request-Reply pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/asynchronous-request-reply) 将受理、稳定状态地址、轮询节奏和结果读取分开。可借鉴之处是依据真实任务状态恢复和等待，而非盲目重发长操作。当前同步图片接口没有完整对应合同，本次没有为它新增远端轮询、第二个线程池或更多实际并发。已有受理后 H3 轮询继续沿用原实现。

采用现有持久工单和回执作为执行边界，与 [12 的独立派发与收取](12-carried-worker-scheduling.md)、[17 的当前 ownership 恢复](17-worker-recovery-ownership.md) 保持一致。这是本地工程选择，并非上述文档对本产品公平性、恰好执行一次或性能的保证。

## 最小改动

只有产品使用的非阻塞 `collect_ready_work` 开启公平检查。每次 `ProductionManager.execute_work_order` 返回、实际决策或 Tool 已落盘，且原有确定性媒体续接 REQUESTED 也已保存之后，检查是否存在另一个同 run、当前有效计划中真正可执行且未占槽的工单；有则结束这个 Future。当前工单的 status、owner、Action、Tool ID、参数、结果和尚未执行的 REQUESTED 均不因轮换而改写。

正常派发与 peer 检查复用同一个资格判断：排除已 claimed 或仍在 Future 中的工单、审阅阻塞范围、未来轮询时间、Leader 或用户接管、旧计划和终态。Runner 每次 collect 提供的最新审阅阻塞集合只作为执行器内的当前过滤条件，不新增持久调度状态。

下一次 collect 直接使用原 Store 的 `updated_at ASC, id ASC` 顺序。刚执行并保存的工单排在较早等待的就绪工单之后，最多选择现有 4 槽。没有真正 ready peer 时，同一 Future 继续原来的有效 drain，不为每个回合额外拆分。旧同步 `run_ready_work` 与 `drain_ready_work_until_handoff` API 不开启这一轮换。

轮换返回普通 `ADVANCED`，不伪装用户暂停，不追加完成事件、不创建新工单、不要求 Leader 重新规划。原 Runner/PM 仍以真实 Action、完成结果和失败状态决定规划。用户暂停或消息由原 `execution_boundary` 接管：已开始的操作完成并保存，禁止下一操作派发；原请求可在恢复后继续。

## 验证与限制

专项 `backend/tests/test_worker_fairness.py` 使用临时 SQLite、真实 StudioRunner / ProductionManager / Registry 以及本地 Fake 客户端。主要场景先保存带明确完成边界的五项计划，包含 4 个同步媒体 Tool 和第 5 个短工单，用事件确认四槽实际占满；放行媒体后验证短工单能获得执行机会，长工单继续完成各自剩余回合。完整场景只有 13 次 Worker Fake 请求、0 次 Leader 请求；每个媒体操作恰好执行一次，原始待执行请求 ID 保持，实际执行并发峰值为 4。

其他针对性场景检查：图片 enqueue 已成功且后续 advance 已保存时轮换，重开 Store 和 PM 后恢复同一个 pending ID，不重复 enqueue；没有 peer 或 peer 不可执行时保持一个有效 drain；执行期间审阅范围改变后使用最新资格；暂停和新消息先于下一派发，已保存 REQUESTED 及未启动 peer 保持原状。旧 Team、真实 Harness 控制和已接入的 carried 调度回归用于核对既有行为。

最终本地检查：14 项专项与 25 项旧 Team 回归联合 **39 passed / 15.17s**；实际 Harness 暂停、复合 Tool、派发暂停及 carried 调度 **8 passed / 24.00s**；实际决策期间用户消息接管 **1 passed / 3.71s**。共 48 项通过，两个源/测试文件 Pyright 为 0 errors / 0 warnings，相关文件空白检查通过。使用已安装依赖，未运行真实 Provider。

本次不宣称在慢 HTTP 中途释放槽，也没有提高同时进行的语言或媒体操作上限。若全部四槽都在慢调用中，第五项仍需等其中一次实际操作返回；若没有其他就绪工作，就不轮换。离线回归只证明所测边界正确，真实等待、端到端时长与内容质量的变化须由重新加载后的后续观察和新的空项目验收确认，不把 Fake 执行耗时当成真实收益。
