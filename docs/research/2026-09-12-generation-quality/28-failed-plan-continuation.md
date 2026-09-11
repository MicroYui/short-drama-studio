# 失败计划的后台续接与精确素材版本引用

日期：2026-09-12。本次根据《一起签》视频前真实调试发现的问题修复。只读原 Trace / SQLite，新增回归使用临时项目和 Fake 语言客户端；没有调用 Provider、重写调试数据库、恢复任务或提交媒体。

## 1. 实际错误与循环机制

当前调试项目 project-5b8286dc63e6、任务 studio-job-20f086971171。原始输入位于 output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/。

时间采用 UTC；本地北京时间加 8 小时：

- 20:42:37，纸笔 Worker 保存完成结果 agent-result-ccef01f5b6124dc083772e9601b181ad。
- 21:01:34，Leader model-bbc055b9eb57 的实际输入 20260911T210134544375-f73091f5-model.input.json 中，context.artifact_index 及 recent_actions 的完成回执已经包含十条 asset_version UUID，但 version 全为 null。
- 21:03:18，保存 r11 plan-193ef5f0365f43ab84beaafda6b9cf1e。Leader 沿用这些实际可见引用。
- 21:03:19，complete_remaining_asset_preparation_slices 的 Action action-f6db2a5ea40046b7b438a51274f868f6 在约 11ms 内失败，error_kind=InvalidContextManifest。事件 4378 的首项错误是：

      1 validation error for ContextManifest
      Value error, Context manifest requires an explicit version for asset_version:asset-version-a9fb492dce6b

十个 UUID 均真实存在，包括情况说明的 3/4/5/6/8/9 版、报价的 2/3 版和笔的 2/3 版，不是 Leader 编造 ID 或文件缺失。AssetPlanIndex 同时带有明确版本 7。

同一 r11 还存在独立就绪的 read_h3_preparation_capability。Runner 先看到 ready_steps，调用 allow_replanning=False 的 advance；PM 先设置 RUNNING，再看到失败 Action，直接退出。Worker 尚在活动，下面的同步规划条件又未满足。于是独立 READ 没有执行，失败也没有送到 Leader，约每 0.5 秒重复一次相同状态写入。

事件 4720–4727，21:05:43.285965 至 21:05:47.029398，均为 agent.run_status，status 和 previous_status 都是 RUNNING。这时没有新的后台 Leader 请求，不能把这些循环描述成模型正在思考。最后一次已完成的后台规划是 21:03:18 的 r11。

## 2. 引用为何丢失数字

Epicurus 独立只读核对完整传播链：纸笔 turn17 的 compose 返回 UUID@3，turn19 的 QA 返回相同 UUID/null；turn24 的 image.advance 返回摄影 UUID@4，turn25 的 QA 又返回相同 UUID/null。现有工作集按 project/type/id 保留当前引用，后来的 QA 覆盖了写回执的数字。完成校验能用固定 UUID 解析到实际文件，但回传引用原样保留，Leader 因而接收到 null。

当前 resolver 已允许以固定 AssetVersion UUID 精确找到该记录；Manifest 却要求 asset_version 明确提供数字。因此这是 Tool、工作集与分工合同之间的口径不一致，不是应该放宽所有上下文版本校验。

修复分为两处，缺一不可：

- 源头：主任务新增 service.asset_version_artifact_refs，按同项目固定 UUID 一次查出实际数字；QA inspect、候选分析和同项目 evidence 回执使用它。不会查 current，不发模型请求，不新增写操作；跨项目观察证据仍不进入本项目工件引用。相关 32 项 inspection/crop 回归通过。
- 历史兼容：PM 的 _canonical_work_order_refs 在创建 WorkOrder 前，仅为同项目 asset_version、UUID 确实存在、version 缺失的引用补该记录的数字。scope 与 adjacent_boundaries 均使用同一规则，再通过现有 resolver 校验完整身份。显式错误数字或未知 UUID 仍失败；shot 等原来要求版本的对象仍保留该要求。asset 对象引用原合同允许不带版本，继续原样，不自动变成其 current AssetVersion。

不修改已保存的 PlanRevision、Action、旧 AgentResult 或 Trace，不改变 summarize_artifact 返回引用的 identity。工单保存的是规范后的精确 Manifest，历史计划继续能读到原始 null 形态。

## 3. 采用的成熟实现原则

[Dask Futures](https://docs.dask.org/en/stable/futures.html) 将任务结果及异常保存在 Future 中，依赖与独立任务分别调度；其可复用部分是“慢决策与独立执行并行，错误是明确结果”，无需引入 Dask 执行器。

[LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) 记录运行状态与已完成节点写入，恢复时可保留已经完成的工作。本项目已具备 Action、WorkOrder 和事务计划保存，所以沿用这些当前持久记录，不能让一个失败分工导致已成功媒体重发。本次与 [12](12-carried-worker-scheduling.md)、[17](17-worker-recovery-ownership.md) 的执行/恢复原则一致。

## 4. 最小调度改动

复用现有 CompletionPlanningInput / Future，而非新增执行器或固定阶段：

1. Runner 发现当前计划已有失败 Action 时，不再调用明知无法推进的无重规划 advance。每轮仍派发、收取独立 Worker。
2. prepare_completion_planning 可显式选择失败计划触发，将当前失败 Action 的 ID、step、错误与返回控制的工单状态保存为这一请求的内存快照。没有中间 Tool 进展或普通轮询触发新规划。
3. Leader 请求在 run lock 外执行，收到具体失败及原目标。已成功和正在执行的步骤、活动工单由系统保留；失败和未执行步骤交给 Agent 修订，允许修复后继续使用原 step ID。不要求重新读取项目或重新创建已有媒体任务。
4. 返回时在锁内重读计划、用户最新要求和失败集合。任一变化则丢弃旧响应；Worker 转交用户也不能被旧规划覆盖。暂停和补充消息沿用真实 execution_boundary 和 Future drain。
5. 应用失败修订只继承精确成功/运行 Action，不继承失败 Action。仍在途工单和规划期间晚完成、尚未消费的工单与新计划同事务迁移；原 Action 和结果不改。返回空等待计划也保存新 revision，避免因原失败反复开语言请求。
6. 必要失败规划的异常进入既有错误/有限传输重试路径，不按每个调度 tick 静默重试。没有语言客户端时明确报错，不伪造一个已修复计划。

同时保留 [25](25-asset-worker-context-efficiency.md) 的状态无变化短路：相同状态及一致的完成时间不再重复保存、刷新时间和写事件。真实迁移、异常完成时间修复及独立原因/阻塞更新仍生效。这只是减少重复事件，不能代替上述调度根修。

## 5. 验证与边界

新增 backend/tests/test_failed_plan_continuation.py 使用原 r11 的失败/就绪组合和原错误形态，实际重开服务并运行 Studio Runner、Registry、本地数据库和 Fake 模型，验证：

- REQUESTED Tool 使用原 attempt ID 执行一次；Leader 慢请求期间 Worker 能完成和保存；旧成功 READ 不重跑，独立 READ 与修复后的分工都能继续。
- 错误 Action 保留为历史 FAILED；失败步骤修复时沿用同一 step ID，不把原失败继承进新计划。
- 用户消息、计划替换、新失败和 Worker 转交用户之后，旧响应不应用。
- 空等待在重启后不因同一失败再次规划；真实 pause/message 边界使后台 Future 正常结束。
- 非当前选用的精确 UUID 在 scope/adjacent 中补出其原数字，原计划不改；错误数字、未知 UUID、缺版本的 shot 继续拒绝，asset 对象不被暗中替换成媒体版本。

最终联合命令在 backend 使用现有 .venv/bin/python -m pytest，覆盖 test_failed_plan_continuation、test_carried_worker_scheduling、test_incremental_worker_planning、test_agent_run_status_idempotence、test_asset_worker_context_efficiency、test_worker_recovery_ownership 六套：**66 passed / 53.98s**。其中本文件对应新失败/引用合同专测为 14 项。PM、Runner、AgentRuntime 及三个本轮专测文件 Pyright：**0 errors / 0 warnings**；git diff --check 通过。

Epicurus 已独立只读复核最终失败计划、精确 UUID 规范化、状态短路及跨 Tool 裁切建议保留，未发现具体阻断；没有重复调用 Provider 或改运行数据。新增 failed_plan_state 仅属于内部规划请求快照，未增加公开 API 或持久模型字段。本批代码冻结，等待主任务统一加载。

本轮没有新的真实 Provider 时延或端到端结果可报告。主任务在 05:15:21 本地时间已暂停，两个图片 Worker 完成、12 个 image_tasks 终态、H3 为 0；重新加载后的表现属于继续调试，新空项目完整视频前复跑仍是独立验收边界。
