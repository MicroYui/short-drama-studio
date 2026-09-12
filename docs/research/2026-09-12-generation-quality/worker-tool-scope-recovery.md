# Fresh4：Tool 请求拒绝与终态执行边界

## 已发生的问题

Fresh4 项目 `project-06116be431fa` 的 `prop_asset_builder` 工单
`work-order-8d895cc08c39460aa8a8f9cb0acd9eb8` 在第 23 个 Tool 请求中把
`image.crop.project_id` 写成 `project-061be431fa`。原模型输入中正确项目 ID
出现 90 次，错误 ID 未出现；这次是输出参数错误，不是旧项目上下文污染。

输入/输出分别为 `20260912T070751059880-70173e42-model.input.json`、
`20260912T070807146195-890ca79d-model.output.json`，call 为
`model-2117e73ba0fe`。裁切的资产、源版本和区域本身已有明确来源。

北京时间 15:08:07.482668，原 attempt
`specialist-turn-d3bb5a7402a84241a5ea10ae72465a7d` 已保存
`FAILED / Agent Tool calls must target the current run project`；Registry 及裁切
handler 未执行。该异常被作为执行故障重新抛出。随后出现了第二个独立问题：

- 15:08:07.730705，Team 返回该错误。
- 15:08:07.731827，同一批收集又 claim 了原 Worker。
- 15:08:07.743897，Job 进入 `FAILED`。
- 15:08:08.259217，已重新 claim 的 Worker 开始 `model-1e11f8412ada`。
- 第 24 个请求自行改正项目编号，15:08:19.268288 裁切成功；之后 QA 和采用操作仍继续。

当时 Job 为 FAILED，而 Run 仍为 RUNNING。这个自然失败的案例没有恢复，也没有
用新实现续跑。完整源保存在 ignored observations 的
`fresh4-project-scope-failure-fixture.json` 与原 Trace；本次不回写它们。

## 最小修复

1. `_validate_tool_scope` 的显式参数/归属拒绝使用专门的
   `AgentToolScopeError`。原校验条件未放宽，ID 不被自动改正。Worker 调用边界把
   这类拒绝保存为原 turn 的 `INVALID_ARGUMENTS`，含 `VALIDATION` 阶段、原字段
   指针、期望/收到的值以及未执行说明，交给原 Worker 作下一次判断。未知项目存储、
   Registry 或内部异常仍抛出，不能假装为可修正参数错误。
2. 复用现有 `tool.started/tool.finished` 验证事件，标记
   `execution_started=false`、原 `attempt_id` 和失败状态。它们表示请求校验尝试，
   不表示业务操作已执行。完整原参数仍在 turn/原输出，界面的 latest_error 和
   失败统计可见；没有伪造 `operation_completed` 回执。摘要改为“请求已处理”。
3. 异步 Team 收集到执行错误的这一批先返回调用方，不再同时 claim 原 Worker
   或其他就绪项。Runner 分类并处理错误后，正常后续调度仍可继续。同步 API 不改。
4. 真正 fatal 时关闭该次 Runner 的协作执行回调，Job 和 Run 都进入 FAILED。
   原回调会随 `copy_context` 保留到已启动的 Worker/Leader。即使用户随后明确
   resume 或发新消息使 Job 变为 queued，旧执行回调仍停止；先收集在途操作，再由
   新 Runner 处理这次明确继续。没有新增持久调度状态或额外 Leader 规划。
5. 暂停/等待用户/失败/完成的 Job 不启动下一次决策或 Tool。PM 在开始 Worker
   决策和开始已保存 Tool 前也读取当前 Run，阻止终态调用。已开始的操作仍保存
   实际结果；提交结果在同一 run lock 内重新读当前 Run，终态不被迟到回执改回
   RUNNING/WAITING。普通成功、可恢复错误、暂停和用户交接仍沿用原合同。
6. 相邻 FAILED_PLAN 消息回归暴露了检查与消费之间的竞态：用户消息可能在循环
   的 yield 检查之后到达，`_apply_messages` 若立即提高共享的 processing 计数，
   旧后台回调会误以为它也在处理这条消息。现在只在旧 Worker/Leader future 全部
   收集后才标记消息处理中；否则保留 pending 并回到既有 drain。确定性测试把消息
   放到该间隙，证明旧决策先 yield、新 Leader 后接收消息。这个边界来自离线回归，
   不宣称 Fresh4 已发生同一消息竞态。

## 方法依据与边界

Python 官方 [`concurrent.futures` 文档](https://docs.python.org/3/library/concurrent.futures.html)
说明，取消待执行 future 不会取消正在执行的 future。
[`asyncio` 取消说明](https://docs.python.org/3/library/asyncio-task.html#task-cancellation)
说明取消在协作边界生效，清理后应继续传播取消。这是本地设计借鉴：关闭下一操作入口，
允许正在执行的同步操作正常落盘并收集结果；没有引入 asyncio、强杀线程或回滚媒体。

终态并非全局不可恢复：明确的用户 resume/new message 仍可启动新执行。普通网络
错误继续使用现有有限重试；本次不调整模型、重试次数或业务完成判断。对已进入外部
调用、但因进程崩溃而未确认结果的情况，仍使用原任务恢复合同，不能重发作补偿。

## 离线验证

`tests/fixtures/fresh4_tool_scope.json` 保留实际 t23/t24 的完整 arguments、reason、
ID 和时间。测试只保留一个源 AssetVersion，把本机图片位置归一为 fixture 路径，
并在临时目录生成同尺寸 PNG；不是生产图片质量验证，也不调用 Provider。

- 真实 Registry 本地裁切：t23 拒绝且零写入 → Store/Service 重开 → 下一份实际
  Worker 模型输入含原 attempt、错误和两种项目 ID → t24 由 Worker 修正 → 裁切仅一次。
  相同 run/Worker 保留，原 Trace/参数不改，不增加 Leader 调用。
- 其他项目/其他 run/未交接 attempt/非法 schema 继续拒绝，不能放开隔离。
- 同步阻塞并发：fatal 与慢操作同时执行，第三项等待。收集 fatal 不重新派发；慢操作
  落盘后不发下一请求，Job/Run 均终态；进程重开不自动启动。
- fatal drain 期间分别排队明确 resume 和新消息：旧 Worker 不继续，新执行在原操作
  落盘后开始，消息成功交接后才确认，未新增媒体执行。
- COMPLETED/FAILED/CANCELLED 的 Run 阻止新 Worker 决策和已保存请求；在途模型
  仍可保存最终结果而不恢复 Run。paused/failed/completed/waiting_user 的 Job 不重开。
- 消息恰在循环检查之后到达：旧后台 Leader 不被新消息的 processing 状态放行，
  收集完成后新 Leader 收到完整消息并成功确认；暂停确认也等后台 Leader 收尾。

最终专项 + Studio Harness + 公平调度 + FAILED_PLAN 回归联合 **75 passed / 124.83s**；
终态专项文件最终独立 **12 passed / 18.29s**。另一次 Team、恢复归属、局部失败计划、
Tool attempt 与 feedback 相邻检查的 60 项通过；其中同批 FAILED_PLAN 消息分支暴露的
竞态已按上面第 6 项修复，并包含在最终 75 项全绿结果中。5 个源/测试文件 strict
Pyright 为 0 errors、0 warnings，`git diff --check` 通过。

这些是离线 Fake 与本地真实 Tool 的结果，不能据此宣称新的完整 E2E 已通过或已提速。
