# 当前产品与真实制作诊断

核对日期：2026-09-11。只读当前源码、运行数据库、已有模型输入输出和检查快照；未调用 Provider、启动服务、改写运行数据或重新生成影片。本项只新增本报告、[聚合统计](01-current-metrics.json) 与[只读统计脚本](01-measure-trace.py)。

## 结论

最先应解决的是**机械工具协议带来的额外语言轮次、工作集反复携带大正文，以及外部等待对调度的占用**。最新案例并不能支持“主要时间花在视频生成”或“换更强模型就能消除返工”。许多反复暂停是本轮工程修复换包造成，另有明确要求的文本审阅；它们不能全部称为当前仍在发生的 Runner bug。

《午饭时间》完成了 18 镜、57.11 秒成片，但经过了多次工程修复、用户补参考、明确的修改审阅与恢复输入。影片仍保留餐盘起态、人物站位等生成连续性瑕疵。所有改善建议均是候选方案，不是已经测得的加速或质量收益。

## 数据与计数口径

主要项目：`project-974ca837e45e`。数据库：`output/studio-launch-2026-r2/runtime/short-drama.sqlite3`，本次以 SQLite `mode=ro` 打开。

任务：

| 任务 | Studio Job | 时间跨度（UTC） | 实际状态 |
| --- | --- | --- | --- |
| 剧本 | `studio-job-cf68cb9ca787` | 09-10 05:13:14 至 05:33:57，约 20 分 43 秒 | completed |
| 完整视频及结尾修改 | `studio-job-d87986235dff` | 09-10 05:40:29 至 21:52:44，约 16 小时 12 分 15 秒 | completed |

数据以本项目 `studio_events` 为主，并交叉检查 `studio-trace/<job>/*-model.output.json` 和持久化 WorkOrder。`agent.run_status`、`worker.updated`、SSE 重放以及重复项目快照均不算模型调用。

| 指标 | 实际计数及含义 |
| --- | --- |
| 模型开始 | 1,832 条 `model.started`，1,832 个唯一 `call_id` |
| Provider 语言完成记录 | 1,831 条 `provider.language_generation`，1,831 个唯一 `call_id`，全部能对应唯一开始记录；无重复完成 ID、无无源完成记录 |
| 语言结果 | 1,822 条 success；9 条 `LanguageProviderIncompleteError / sse_incomplete_max_output_tokens`。失败也可能已有 Provider usage，不应从成本统计删掉 |
| 唯一缺少完成记录 | `model-ae30e1c30414`，事件 12853，15:13:38，episode-editor；处于开发停止前的旧整集检查循环，不能将其用量补零 |
| Provider 已报告输入 | 92,995,579 tokens，其中缓存输入字段累计 14,130,744；后者包含在输入内，不能重复相加，也不能把总输入称为未缓存计费量 |
| Provider 已报告输出 | 1,927,855 tokens，其中 reasoning 字段累计 880,779；不能把 reasoning 再加到输出上 |
| usage 完整性 | 1,831 条完成记录均提供上述四个用量字段；不意味着唯一未完成调用没有消耗，也不据此推算价格 |
| 纯 schema 请求 | 原始模型输出可解析为 `requested_tool_schema_ids` 的 802 次，占完成记录 43.8%；累计已报告输入 42,277,511 tokens、输出 221,230 tokens |
| Worker Tool | 1,143 次 `tool.started`；1,142 次 `tool.finished`，成功 1,055、失败 87。这里只计这个事件系列，不混入另一层 Agent action 事件 |
| WorkOrder | 55 个唯一持久化工作项：33 completed，22 cancelled；取消含换计划/保留其他分工，不能直接作为失败率 |
| 图像任务 | 18 个，当前均 succeeded；不等于所有候选都被采用或图像内容全部满意 |
| H3 任务 | 9 个：7 succeeded、1 failed（确认未创建远端任务）、1 保留最后已知 running；最终用 7 份已返回媒体中的实际采用片段完成影片 |
| 用户文本消息 | 22 条 `chat.user`；含创作指令、恢复说明与工程提示。五次创作动作不能改写为全程只有五条消息 |
| 作业状态事件 | paused 33 次、waiting_user 4 次、failed 2 次；33 次暂停多数对应修复与换包，4 次等待对应用户要求的逐项文本选择 |

上述摘要与 [trace-summary.json](../../../output/studio-launch-2026-r2/snapshots/trace-summary.json) 一致；本报告额外核对了 call_id 唯一性及原始输出类型。统计 SQL 的核心为按 `project_id` 过滤 `studio_events`，再以 `kind`、JSON `call_id` 分组。失败分类另取 `tool.finished.status`，不把旧状态快照重复计数。

可复算命令：在仓库根目录运行 `python3 docs/research/2026-09-11-mature-video-production/01-measure-trace.py`。脚本仅查询指定项目 SQLite；schema 分类只直接读取两个指定 job 目录下的小型 `*-model.output.json` 文件，不递归扫描 output、模型输入、ZIP 或应用包。聚合 JSON 不含完整用户输入、Provider 响应 ID、提示词或大型 payload。

### 耗时能分到什么程度

| 指标 | 剧本任务 | 视频任务 | 使用限制 |
| --- | ---: | ---: | --- |
| 语言完成记录数 | 33 | 1,798 | 含 Worker、Leader、工具内嵌语言请求 |
| 累计已报告输入 | 625,184 | 92,370,395 | 含缓存输入；含修复与重复工作 |
| 各语言请求 elapsed 之和 | 898.3 秒 | 40,853.4 秒 | 有并行，不是墙钟总耗时 |
| 语言请求时间区间并集 | 898.4 秒 | 32,780.3 秒，约 9 小时 6 分 | 以唯一 started 时间到完成事件时间合并；表示至少一个语言请求在途，不代表全是有效创作 |
| 单次语言请求中位耗时 | 11.8 秒 | 9.1 秒 | 一次工具可要多次语言请求；不等于一次创作动作耗时 |
| 单次输入中位数 | 18,309 | 40,174 tokens | 不含唯一未完成调用 |

输入用量最多的角色如下。角色名来自实际 Trace，允许多个阶段使用同一角色名；它不是工作项个数。

| 角色 | 完成记录数 | 已报告输入 tokens | 单次输入中位数 |
| --- | ---: | ---: | ---: |
| EDITORIAL_FINISH | 270 | 19,596,133 | 67,276 |
| ENDING_VIDEO | 121 | 10,442,059 | 81,495 |
| editorial_repair_worker | 90 | 7,662,234 | 98,919 |
| asset_planning_recovery | 90 | 5,180,036 | 48,223 |
| ending_asset_planner | 42 | 5,115,611 | 132,536 |
| episode-editor | 75 | 4,526,271 | 61,477 |
| ending-video-compiler | 65 | 3,770,593 | 61,219 |
| leader | 100 | 2,427,720 | 24,137 |

仅 EDITORIAL_FINISH 与 ENDING_VIDEO 就累计约 30.04M 输入 tokens；增加更多角色之前，应先检查单个工作项的机械往返和输入范围。全部角色与工具分布保存在聚合 JSON，未被调用的工具不会显示在调用表内。

按 `job.updated` 相邻状态时间积分，视频任务包括：running 42,467.1 秒，pausing 1,869.3 秒，paused 11,836.1 秒，failed 620.0 秒，waiting_user 1,509.5 秒，queued 32.8 秒。`running` 仍混合模型、工具、本地处理与等待；`paused` 与修复等待对应，但不能精确归因全部为编程时间。`waiting_user` 约 25 分 9 秒是明确的审阅等待，不能叫自动化故障。

7 个成功视频从本地 `submitted_at` 到 `finished_at` 为 353.2–615.7 秒，中位 431.9 秒；区间并集 2,425.1 秒，约 40 分 25 秒。它包含远端生成、轮询及本地回收延迟，不是供应商纯渲染速度。未返回追加候选不在这组成功样本里。

19:42:49 至 20:04:41 的已有 [runner-h3-wait.json](../../../output/studio-launch-2026-r2/snapshots/runner-h3-wait.json) 实际记录 44 次状态查询、零新语言决策。299 次 `task.advance` 因此不能等同于 299 次模型轮次。语言、视频与状态区间有交叠，**上述耗时不应直接相加或相减来构造净制作速度**。

## 按影响排序的问题

### 1. P0：每次工具调用重新发现 schema，机械协议消耗了大量语言轮次

**事实与当前状态：仍活跃，源码与真实输出均确认。** Worker 只把 schema ID 保存在一次决策函数的局部变量；工具执行完成后的下一次决策重新从待修复工具、协议反馈等构造 schema 集合。普通成功调用用过的 schema 没有在 WorkOrder 内连续保留。运行提示又明确要求复杂参数先请求 schema，并且一次只能请求一个工具。

- 源码：[production_manager.py:4510](../../../backend/short_drama/production_manager.py#L4510)、[4640](../../../backend/short_drama/production_manager.py#L4640)、[4660](../../../backend/short_drama/production_manager.py#L4660)、[4771](../../../backend/short_drama/production_manager.py#L4771)。新 schema 在 4781 行只扩展本次局部列表。
- 802 次模型原答只请求 schema；这些请求的 elapsed 合计 7,106.88 秒。有并行且改变协议会影响后续上下文，不能直接承诺节省同样墙钟时间或 42.28M tokens。
- 被请求的 schema ID 次数包括 `artifact.read_current` 184、`artifact.patch_asset_plan` 124、`timeline.read_editorial` 115、`timeline.read_export` 110；一次请求可含多个 ID，所以不能把这些数相加当调用数。

按真实响应顺序，以 `(work_order_id, tool_id)` 记录是否以前发现过，得到更保守的拆分：

| schema 响应类别 | 次数 | 含义 |
| --- | ---: | --- |
| 请求的全部 ID 均曾在同一 WorkOrder 发现 | 554 | 重复发现同一 Tool ID 的明确数量；需区分换包后的 schema 更新 |
| 同时含新 ID 与已发现 ID | 135 | 仍有新增发现价值，不能整条算可消除 |
| 全部 ID 是本 WorkOrder 首次发现 | 76 | 不应默认视为浪费 |
| 没有 WorkOrder 的 Leader 请求 | 37 | 单列，不把全部 Leader 调用强行并成同一工作项 |

有 WorkOrder 的响应一共请求 2,869 个 Tool ID occurrences，其中唯一 WorkOrder+Tool ID 为 477，重复请求为 2,392。554 条纯重复发现响应的输入累计 32,234,652 tokens（内含已报告缓存输入 4,309,278）、elapsed 合计 4,717.652 秒。这是待优化负担的可定位集合，不是已经实现的节省量；**不能声称 802 次全部消失**。

由于本轮中途多次修复换包，同一 Tool ID 的 schema 可能发生过变化。本报告还使用更保守的连续运行口径：每逢 `queued / paused / failed / waiting_user` 状态都清空发现集合，只认定同一 WorkOrder、同一连续运行段内的重复。仍有 **458 条全部请求已发现 ID 的响应**，输入累计 25,252,726 tokens（内含缓存 3,600,498）、elapsed 合计 3,718.231 秒；其余为 128 条全新、179 条混合、37 条无 WorkOrder。这里没有读取大输入中的 schema 内容摘要，因此它仍是按 ID 与运行边界定位的优化机会，不是可直接扣除的费用或墙钟保证。实现时必须按实际 schema 变更刷新。

**候选改善：** 在同一 WorkOrder 中保留已选用 schema 的集合并按实际版本刷新；给当前职责提供小组常用工具的精确 schema，其他仍按需发现。把纯 schema 发现从创意判断轮次中移除。先用已有 Trace 回放确认相同操作可以直接形成合法参数，再观察模型请求数与输入 token 的变化。

**预期影响：** 减少大量不产生内容的语言往返，改善延迟、成本与可见进度。无需先更换模型，也不必一次注入全部工具 schema。

可按当前具体任务预装的现有 schema 小组如下；这只是减少发现成本，不要求每个任务把表内工具全部执行。正式实现应按本次职责/操作选择，保持 `gpt-6-astra / xhigh`。

| 工作范围 | 可从已有工具选择的小组 |
| --- | --- |
| 镜头引用编辑 | `artifact.read_current`、`shot.save_current`、`shot.patch_storyboard`、`artifact.get_asset_plan_index`；有实际观察疑问时再加载 `qa.inspect_asset_version` |
| 当前生成组编译 | `route.get_generation_unit_context`、`route.h3_capability_profile`、`route.compile_h3_prompt`、`route.plan_h3`、`route.get_h3_plan`；需要修改生成计划时再加载对应保存工具 |
| 后期剪辑 | `timeline.read_editorial`、`timeline.edit_clips`、`timeline.save_editorial`、`timeline.add_asset_overlay`、`timeline.export_editorial`、`timeline.read_export`；涉及原声时补 `audio.inspect_manifest` |
| 素材制作项 | `artifact.get_asset_work_context`、`artifact.patch_asset_plan`、`artifact.create_asset`、`image.capability`、`image.enqueue`、`image.advance` |

### 2. P0：工作集保留的是读过的大正文，尚未充分收敛为当前操作所需内容

**事实与当前状态：仍活跃；旧全 Trace 回灌已修复，但当前读取缓存仍重。** `_specialist_working_set` 按工具与选定对象保留成功读取，解决了过去“只保留最近读取，来回重读”的问题；然而不同读取参数/范围形成多个长期保留正文，之后的 schema 请求也携带它们。

- 源码：[production_manager.py:1947](../../../backend/short_drama/production_manager.py#L1947)、[2033](../../../backend/short_drama/production_manager.py#L2033)、[2392](../../../backend/short_drama/production_manager.py#L2392)、[4678](../../../backend/short_drama/production_manager.py#L4678)。普通读取键包含原始完整参数，尚不规范化默认值；精确结果继续写入 read_observations。
- 最大输入调用 `model-6c2b3c2ec10b`，事件 16107，ENDING_VIDEO，Provider 输入 179,555 tokens。原输入文件 `20260910T174718130578-826fa622-model.input.json` 中 working_set 为 374,192 个 JSON 字符，其中 read_observations 331,196。单个 `shot.get_storyboard` 143,212 字符，局部素材上下文 52,159，资产索引 35,693；这里的字符数只用于定位载荷，不能当 token 数。
- 最终后期调用 `model-36ec912784f7`，事件 19794，输入仍为 96,040 tokens。read_observations 198,978 字符；同一 WorkOrder `work-order-8ecfeff371264744b2063ab638509ef2` 同时保留三份整集 `timeline.read_editorial`：第 19 轮 `include_project=true`、第 27 轮 `include_project=false`、第 38 轮省略默认参数，正文分别约 70,833 / 17,721 / 19,992 字符。
- 此时 instructions 仅 8,980 字符，工作集 215,011 字符。因此不能把主要问题简单归给 Skill 文本。

**候选改善：** 规范化读取默认参数；用“对象 + 视图 + 范围”替换字符串参数身份；同对象当前内容失效时统一替换；下一步模型只带本次输入切片、短状态和已存正文引用。保留对不同镜头/页范围的独立读取能力，不能退回粗暴 last-N。把长期完整输入卸载提前到对象/操作范围，不只在 200k 阈值附近触发。

**预期影响：** 降低每一轮重复输入与注意力噪声；局部镜头更新不再长期带着全剧本/全分镜/旧全时间线。

### 3. P0：调度仍存在批次等待屏障，外部轮询占住一个 Worker 及批次返回

**事实与当前状态：源码静态风险仍活跃，需小型 Fake 场景验证影响边界。** 已修复“只执行委派步骤”和“所有 Worker 每轮一起前进”的问题，但 `LocalTeamExecutor` 仍在一次 ThreadPool 上等待所有选中 WorkOrder 的 Future 返回，然后才返回 StudioRunner。一个 Worker 连续轮询远端时，会持续占用槽位并推迟整个 batch 返回；同批先完成的结果不能立即回到 Leader 产生后继新分工。

- 源码：[team_executor.py:140](../../../backend/short_drama/team_executor.py#L140)、[154](../../../backend/short_drama/team_executor.py#L154)、[229](../../../backend/short_drama/team_executor.py#L229)、[389](../../../backend/short_drama/team_executor.py#L389)；H3 使用 15 秒 `time.sleep`，然后继续持久化下一次确定性查询。
- Studio 调用仍是 [studio_runtime.py:357](../../../backend/short_drama/studio_runtime.py#L357) 的同步整批 `advance_agent_team(...continue_until_handoff=True)`。
- 已完成修复：所有 ready step 优先执行（350–354 行），外部工具不持有项目写锁、Worker 可以各自连续执行、H3等待不再需要模型决策。这些修复不应再列为当前缺失。
- 真正发生过的独立后期被远端轮询挡住，见 [04-production-progress.md:166](../../presentation/studio-launch-2026/04-production-progress.md#L166)；新版 20:43 已让两个独立读取和 EDITORIAL_FINISH 启动，见 [independent-ready-work-native.json](../../../output/studio-launch-2026-r2/snapshots/independent-ready-work-native.json)。本报告不声称最新版本仍复现同一已修复路径。

**候选改善：** 把 Provider 在途等待变成 `next_poll_at` 的可调度状态，让完成/交接结果逐个通知 Leader；长期等待释放 Worker 槽位。暂停/补充消息通过现有唤醒信号结束等待。保留已有确定性轮询与提交去重，不引入分布式队列。

**预期影响：** 减少“已有结果却等另一组慢任务”的停顿，改善末尾收敛和补充要求响应。

### 4. P1：局部修改需多层数据合同同步，旧版本校验曾反复把无关工作拦住

**事实与当前状态：多处已修复，结构性回归面仍在。** 87 次 Worker Tool 失败中，`artifact.patch_asset_plan` 25 次、`route.get_generation_unit_context` 5 次、`shot.patch_storyboard` 3 次。历史失败包括全局 AssetPlan/Storyboard/ProductionSpec 版本不一致、H3路线运行状态更新却仍要求旧状态版本。

- 当前可变执行记录已统一为当前状态引用：[agent_runtime/context.py:47](../../../backend/short_drama/agent_runtime/context.py#L47)。Worker 最新要求也已接通：[production_manager.py:4703](../../../backend/short_drama/production_manager.py#L4703)。这些不是待实现项。
- 活跃生成入口仍依次读取生成组、解析媒体、逐镜核对引用并进行 Provider 校验：[service.py:10884](../../../backend/short_drama/service.py#L10884)。真正内容变更必须检查；无关内容版本变化不能继续作为全局冻结。
- 修复实际证据见 [parallel-material-saves-real-results.json](../../../output/studio-launch-2026-r2/snapshots/parallel-material-saves-real-results.json)、[parallel-video-unit-check.json](../../../output/studio-launch-2026-r2/snapshots/parallel-video-unit-check.json)、[live-route-replan-fixed.json](../../../output/studio-launch-2026-r2/snapshots/live-route-replan-fixed.json) 和 [04-production-progress.md:124](../../presentation/studio-launch-2026/04-production-progress.md#L124)。

**候选改善：** 围绕“这次选中的生成组/镜头/制作项”统一依赖读取与变更回执；保存工具返回准确的已影响对象、当前引用与可继续动作。先用本轮真实失败请求组成有限的跨入口回放集，避免每个入口单独放宽校验后另一入口再次拒绝。保留真实内容冲突处理，不建立新版本管理产品。

**预期影响：** 减少修复后重新读取、重新登记、重新提案与用户反复点继续。

### 5. P1：工具粒度把确定性登记、读取、编译和放入时间线拆成很多创意决策

**事实与当前状态：仍活跃。** 单工具请求协议位于 [production_manager.py:4510](../../../backend/short_drama/production_manager.py#L4510)。本轮发生 `artifact.read_current` 157 次、`timeline.read_editorial` 59 次、`artifact.patch_asset_plan` 52 次、`shot.save_current` 30 次、`route.get_generation_unit_context` 27 次。它们不是全部冗余，但一次小修改通常需要多次“读—决定调用—登记—再次读”。

**候选改善：** 为可独立完整表达的操作提供有边界的组合能力，例如读取一个生成组的当前执行输入、把已指定图版批量放入当前时间线、批量保存互不冲突素材字段。返回足够的更新回执，避免用再读确认刚刚保存的确定性事实。允许一轮提交多个独立只读操作，写入仍按对象校验与落盘。保持创意判断在 Agent，不把完整制片固化成硬路由。

**预期影响：** 让模型轮次主要用于故事、视觉、选择与修订判断；减少查询与登记链条，和问题 1 联合处理收益更大。

### 6. P1：H3 编译存在重复改写回路，Provider 约束与创作内容没有足够早收敛

**事实与当前状态：仍有可改进机制；本轮输出额度问题已修复。** 本轮 `route.compile_h3_prompt` 29 次，其中 21 次工具失败，混合 ProviderOperationError、过期输入与引用限制。完整结尾稿出现 7,047 字符超出 7,000 限制，后续缩为 6,649 字符才提交；另有 xhigh 输出空间耗尽。

- 当前调用已将输出上限改为 32,000 tokens：[service.py:10917](../../../backend/short_drama/service.py#L10917)。最终 7,000 字符仍在 [h3_prompt_skill.py:160](../../../backend/short_drama/providers/h3_prompt_skill.py#L160) 等边界校验，不等于允许最终超长。
- 当前编译输入同时含 locked_generation_unit、locked_shots、参考映射、可见资产与全组规则：[h3_language_prompt.py:507](../../../backend/short_drama/providers/h3_language_prompt.py#L507)。方法提示已强调独有事实只写一次，不能把“提示词再写短一点”当完整解决方案。

**候选改善：** 对稳定字段、槽位、时间标记和共享声明做确定性组装；模型只写不能机械生成的镜头表演与承接。第一次组装就精确计字符并把超长位置返回，复用原稿做局部压缩；不要反复重新生成整份正文。保留用户对白、实际 Provider 限制与原意检查，不引入额外创作预算。

**预期影响：** 减少编译失败与草稿循环，降低资产/镜头已经就绪后迟迟不能提交的时间。

### 7. P1：连续性主要依赖设计描述，生成结果中的实际状态未自然进入下一段输入

**事实与当前状态：当前质量缺口仍存在。** 食堂四视图补角有空间矛盾，最后采用用户既有确认参考；结尾加肉候选实际出现餐盘起态、校长站位和重复添菜偏差。最终用已返回候选配合剪辑完成，而非所有问题重新生成到完美。

- [TRACE_README.md:27](../../../output/studio-launch-2026-r2/TRACE_README.md#L27)、[31](../../../output/studio-launch-2026-r2/TRACE_README.md#L31) 保留具体结果边界。
- `_previous_shot_terminal_state` 从上一镜 `ShotPlan` 的 visual、action_out、prop_state_out、character_positions 读取设计事实，见 [h3_language_prompt.py:262](../../../backend/short_drama/providers/h3_language_prompt.py#L262)。这不是上一段实际采用视频的终态观察。
- 实际视频参考在发现偏差后才额外加入，见 [04-production-progress.md:154](../../presentation/studio-launch-2026/04-production-progress.md#L154)。该改进候选后来一直未返回，不能声称其提高质量已经验证。

**候选改善：** 对确实要求连续的相邻生成组，维护小型“已采用画面的当前状态”记录与实际参考；只包含人物位置、手中物、食物初终态、关键动作承接。明确设计状态和观察状态来源。先用已有影片验证裁切、插入反应镜头、声音桥等本地修复；必须重生成时再使用实际参考与局部目标。视频抽帧观察和音频听审继续分开。

**预期影响：** 减少多次整段重生成，提升关键动作和情节兑现的一致性；不承诺消除生成模型全部偏差。

### 8. P1：用户指定审阅与全任务暂停仍耦合，收尾也容易被建议性检查拖住

**事实与当前状态：部分已修复，局部审阅造成全任务等待的策略仍活跃。** 本轮 4 次 waiting_user 都来自真实要求的文字对照，应保留；但 Runner 当前只要同一 run 有任意 pending change，就整体返回 waiting_user，尚未按依赖隔离可继续的其他工作。[studio_runtime.py:318](../../../backend/short_drama/studio_runtime.py#L318)。

- 用户后来要求第 16–18 镜 21 处文字集中成一批，否则按镜头分别等一轮，见 [04-production-progress.md:120](../../presentation/studio-launch-2026/04-production-progress.md#L120)。批次已实际处理，不应再次列为未完成。
- 目标完成时，最终一次消息明确已有文件可交付、建议性审校不要变成新门槛；此前透明黑尾的建议检查仍报旧 Take 来源错误。该转换现已修复：[studio_episode_review.py:30](../../../backend/short_drama/studio_episode_review.py#L30)。
- Worker 新补充未入模型、保留 WorkOrder 的空计划校验问题均已修复，代码分别为 [production_manager.py:4703](../../../backend/short_drama/production_manager.py#L4703)、[5332](../../../backend/short_drama/production_manager.py#L5332)，并有本轮恢复证据。

**候选改善：** 审阅按一次语义修改收集完整关联项；仅暂停依赖这些待决内容的分工。计划明确区分“本次必须完成”“可用但有建议”“可选改进候选”；已有交付满足原目标时，由 Agent 总结剩余建议并结束。用户确认仍只出现在用户要求的位置，不用正则或固定审核次数替代判断。

**预期影响：** 减少来回交互与末尾无限打磨，同时保护用户明确要求的逐项选择。

### 9. P1：桌面运行包、编辑器、检查与导出之间的能力合同仍需小范围实际组合验收

**事实与当前状态：本轮具体缺陷大多已修复；发布与回归缺口需保留。** 本轮因缺 OpenCC、图版透明与文字布局缺口、导出只取顶层、透明度旧限制、LTX 素材元数据必填、当前剪辑仍被旧 Manifest 检查等问题反复修复换包。它们发生在用户已经拥有可用素材之后，与 H3 创作质量无关。

- 当前音频输入已经来自保存的 Studio 时间线并保留速度/音量：[studio_audio.py:51](../../../backend/short_drama/studio_audio.py#L51)。当前整集检查同样读 Studio 剪辑：[studio_episode_review.py:15](../../../backend/short_drama/studio_episode_review.py#L15)。不能继续把“读取旧 Manifest”称为当前活跃 bug。
- 本轮真实修复与最终导出证据：[04-production-progress.md:108](../../presentation/studio-launch-2026/04-production-progress.md#L108)、[130](../../presentation/studio-launch-2026/04-production-progress.md#L130)、[174](../../presentation/studio-launch-2026/04-production-progress.md#L174)、[178](../../presentation/studio-launch-2026/04-production-progress.md#L178)、[184](../../presentation/studio-launch-2026/04-production-progress.md#L184)。

**候选改善：** 用已有短案例做一次打包后有限验证：启动、读当前剪辑、局部裁切、PNG叠图、原声字幕、透明黑尾、导出并重开。工具能力声明与实际导出转换使用同一个适配事实来源；不支持的效果在创建参数时明确反馈。只做本产品需要的回归，不扩建独立 QA 平台或付费稳定性实验。

**预期影响：** 将“成片已有但最后一步失败”的低价值工程中断前移到交付前发现。

### 10. P1：字幕自动对齐仍写旧 Manifest，尚未直接更新用户正在编辑的时间线

**事实与当前状态：源码确认仍活跃；不是缺少语音分析或对齐工具。** 当前 `audio.inspect_manifest` 已能检查保存的 Studio 剪辑，而 `audio.align_subtitles` 仍调用 `align_current_native_subtitles`，读取 `_current_manifest(document)`，生成一个新的 TimelineManifest 并更新 `current_manifest_id`；该路径没有将字幕时码合入 `document.editorial_timeline`。

- 默认工具注册：[tools/audio/__init__.py:125](../../../backend/short_drama/tools/audio/__init__.py#L125)；描述也明确返回新 Manifest 与对齐报告，供其他 Worker 再读。
- 实现：[service.py:13704](../../../backend/short_drama/service.py#L13704)、[13725](../../../backend/short_drama/service.py#L13725)、[13766](../../../backend/short_drama/service.py#L13766)。对照已改好的检查路径：[service.py:13677](../../../backend/short_drama/service.py#L13677)。
- 本轮 `audio.align_subtitles` 两次调用，第一次受到桌面 OpenCC 缺依赖影响；之后后期仍围绕当前时间线读取原声、调整字幕并多次 `timeline.save_editorial`。不能把旧 Manifest 对齐成功等同于当前编辑字幕已经自动同步。

**候选改善：** 复用既有 `align_native_subtitles` 与 `editorial_audio_input`，增加当前剪辑范围的对齐应用能力：输入当前剪辑修订与选中字幕/镜头，输出并保存相关字幕时码；保留字幕文字、画面、人工裁切、音量和其他轨道。提供精确变更回执与近似匹配告警，避免 Agent 重抄整份时间线来搬运确定性时码。先用现有影片验证实际对白末尾和第 5 镜手动裁切，不重新生成声音。

**预期影响：** 缩短后期工具链，减少字幕与当前剪辑脱节及反复读取报告、手工回填时码。

## 三条真实调用链：为什么一次小操作需要很多语言轮

下列时间均为 UTC。事件 ID 对应本项目 SQLite `studio_events.id`；模型调用以 call_id 去重。schema 请求没有创作新内容；工具内的视觉模型则是不同的必要观察成本。

| 实际链 | 可复核事件与行为 | 诊断 |
| --- | --- | --- |
| 开场视频输入准备，10:05:55–10:07:25 | 6212 schema → 6220 read_current 请求 → 6232读取成功；6252 schema → 6259 capability 请求 → 6267成功；6284 schema → 6292 compile_generation_plan 请求 → 6305成功；6325 schema → 6333 get_generation_unit_context 请求 → 6347成功 | 4 次确定性操作之前用了 8 次语言决策。来源判断必要，但每次 schema 发现不是创意判断；随后兼容观察又发生一次输出耗尽并恢复 |
| 后期读取，17:04:17–17:07:08 | 15098 schema → 15104 read_editorial → 15111成功；15128 schema → 15134 schema → 15147 read_current → 15155成功；15162 schema → 15172 get_production_document → 15178成功；15194 schema → 15204 schema → 15215 get_take_inspection → 15219成功；15228 schema → 15241 schema → 15260 read_editorial → 15269成功 | 5 次读取用了 13 次语言决策；部分一次读之前先加载两批 schema。输入由15.7k增至51.2k，尚未真正写入新剪辑 |
| 镜头参考绑定，09:26:12–09:30:36 | 5794 schema → 5798 read_current → 5804成功；5813 schema → 5817 get_storyboard → 5823成功；5832 schema → 5836 inspect_asset请求，5845工具内视觉模型，5846成功；5855 schema → 5859 save_current请求 → 5865失败 | 首次绑定写入前8次Worker决策 + 1次视觉观察。输入从20.8k升至68.4k；失败源自正文所有图片被套入H3数量限制，后经修复才保存成功。不要把这一历史失败重复描述为当前规则 |

## Skills、旧案例与解释边界

已有能力不应重复建设。当前默认注册入口 [tools/defaults.py:42](../../../backend/short_drama/tools/defaults.py#L42) 已加载 shot、video、audio、qa：

| 现有能力 | 活跃代码位置 | 本轮使用与缺口判断 |
| --- | --- | --- |
| 关键帧 brief、导演建议/修复 | `tools/shot/__init__.py:873,901,929` | 不是缺少同名工具；应在相关问题出现时复用，不能给每镜新增必经步骤 |
| 参考视觉重排、关键帧比较、输入兼容观察 | `tools/qa/__init__.py:326,360,394` | 本轮输入兼容检查 3 次；未调用 rerank/compare 不直接证明应该调用，需结合具体镜头问题判断 |
| 已有 Take 观察读取与新观察 | `tools/video/__init__.py:104,197` | 本轮 get 13 次、inspect 14 次；描述已要求复用已有报告，并明确抽帧不听声音 |
| Take 比较与再制作入口 | `tools/video/__init__.py:236,299` | `compare_takes` 已存在但本轮未调用；`retake/extend/gap_fill` 是 Mock，真实 H3 需走受影响组新候选。比较范围与修复写回缺陷见 [07](07-implementation-quality-audit.md) |
| 原声检查与字幕对齐 | `tools/audio/__init__.py:134,279` | 已有 ASR/时间戳能力；真正缺口是问题 10 的当前剪辑应用路径 |

既有 [37 低重试研究](../../37-phase26-platform-paper-open-source-low-retry-research.md) 与 [41 Skills 研究](../../41-production-skill-research-and-enrichment.md) 已讨论参考、关键帧、相邻边界、局部重算及经验复用。后续应检查这些能力如何进入当前工作分工和持久化回执，而不是把已注册工具再次写成“待新增”。同样，旧研究的某些待办不能自动当作当前缺陷。

当前加载器已经按角色/阶段组合，去重 shared prefix/suffix：[production_profiles.py:230](../../../backend/short_drama/production_profiles.py#L230)、[302](../../../backend/short_drama/production_profiles.py#L302)、[340](../../../backend/short_drama/production_profiles.py#L340)。`drama_series/manifest.json` 使用精确角色章节，并有阶段覆盖。不能因为来源原文仍有审批、固定配额，就推断这些原文正在每个 Worker 中整篇生效。

现有方法要求连续性、明确素材用途和真实 Provider 限制，均应保留。旧全流程文档包含历史兼容措辞，清理时应以实际 `skills.loaded` 和 model.input 的 instructions 为准。最新输入抽样显示最大载荷来自 working_set，先继续大删 Skill 不能取代问题 1–2。

校园 131 秒案例记录的 432MiB H3 列表、反复选 Take、导出重建覆盖字幕等均有明确修复，不列为当前新缺陷。该阶段 133 次语言调用使用 gpt-5.6-sol/low；与本轮 Astra/xhigh、不同工具与任务范围不可直接做模型性能对比。来源：[50-complete-film-agent-case-trace.md:37](../../50-complete-film-agent-case-trace.md#L37)、[55](../../50-complete-film-agent-case-trace.md#L55)、[65](../../50-complete-film-agent-case-trace.md#L65)。

## 建议的最低验证顺序

1. 先用现有 Trace 及 Fake 工具验证 schema 留存、读取默认参数归一化和新模型输入大小；不生成新视频。
2. 用一个立即完成 Worker、一个远端等待 Worker 和一条后继依赖验证逐项唤醒；证明无需用户补消息就能启动后继。
3. 用本轮明确失败请求验证局部资产/分镜/时间线更新，以及已有短片的桌面打包后导出组合。
4. 再选择少量已有生成组做参考连续性与本地剪辑改善对照；需要新付费生成时另按用户当前授权范围执行。比较实际返工、模型轮次、输入 token、等待分类和观感，不把演示录像长度当制作耗时。

以上优先序针对已经观察到的产品问题，不需要引入密码学证明、额外审批链、分布式基础设施或独立安全审计。
