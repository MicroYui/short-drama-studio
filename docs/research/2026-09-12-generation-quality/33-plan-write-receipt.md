# AssetPlan 写后回执与未改读取范围

2026-09-12。本轮针对同一视频前 debug 的实际往返修复，于 06:55:53 随第九轮修复加载并恢复原 Job，见[实际制作记录](03-real-production-record.md)。修复与本地回放本身未调用 Provider、修改实验 DB 或改写原 Trace；后续实际运行单独记录，不是空项目稳定性验收。

## 实际问题与必要读取

项目 `project-5b8286dc63e6`，Worker `work-order-5aa6b485cec14f60acdf9803f9d69ee1`，Trace 目录为 `output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171`。

| 实际往返 | 证据与判断 |
| --- | --- |
| t19 写后 t20 读 index | `20260911T214732878016-2bd7bd62-model.input.json` / `model-c7e6e6049e83` 中，成功写的 `output` 被整体移除，`receipt.current_state` 只有 `project_id`；新版本 UUID `production-document-version-bfd73ce8f7f9` 不在输入中。只有 `asset_plan@17` 数字不能填 `base_version_id`，所以当时读取有必要。 |
| t24 写后 t25 读相同切片 | `20260911T220517893202-d671f495-model.input.json` / `model-351e33ef4d70` 同样缺新 v19 UUID `production-document-version-d3f81efa1738`。t24 的 44 条字段修改只改变 5 个 item，但计划换版使 t23 整份多对象 READ 失效。t23/t25 的请求逐值相同，会议室和五组目录这 6 个 item 也逐字段相同，却已从下一输入消失。 |
| t14、t18 读 index 获得合同问题 | 简短执行合同诊断原先仅出现在 index。实际 v15 的两条 canonical-root 诊断直到 t14 才可见；写回执原本不携带这个已能确定性计算的结果。 |
| t8、t16 的同值 index 刷新 | 与上面不同：这些读取时已有相同 index。Boyle 的独立逐次审查指出委派要求“保存时读取最新计划”会诱导额外刷新；不能把它们归因于 UUID 丢失，也不能把本修复描述为已修改该提示词。 |

t25 也新增了真实信息：`media_versions` 从 7 条变为 11 条，包含 t24 明确关联的四张字版。因此不能只按“相同 Tool 调用两次”判定整次读取无效。

实际 t25 决策请求估计输入 **106,767 tokens**，Provider 报告 **106,777 输入 / 591 输出 tokens、13.86547 秒**。t25 原始 Tool output 为 **47,460 个估计 tokens**，其中 6 个未改 item 为 **6,804**，重复的分镜正文为 **25,695**。这些是已发生的输入与返回规模，不是本修复已实测节省的 token 或时间。新增必要上下文留存会增加写后单次输入；能否减少后续调用需要加载后观察。

## 采纳依据

- [Anthropic Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) 建议用真实调用分析改进工具，返回能够支持下一动作的相关信息，并兼顾 token 效率。本次返回精确版本身份、实际变更 ID 和已有诊断，避免要求模型为补齐回执另读 index。
- 本仓保存的 [MiniMax Design Stage Execution Plan](../../../backend/short_drama/skills/sources/minimax_design/3.0.11/workflows/_shared/stage-execution-plan.md) 把稳定 `work_items`、真实 `runtime_refs`、局部变更与当前执行上下文分开。这里只借鉴“保留未改稳定对象、用真实输出交接”的方法，不复制其私有 Tool 名、固定阶段或确认门。该来源是已保存的 3.0.11 方法文档，不宣称是公开运行时源码或当前最新版；许可边界见 [研究 06](../2026-09-11-mature-video-production/06-minimax-design.md)。
- [MiniMax 官方 Agent Team 文章](https://www.minimax.io/blog/minimax-agent-team-long-running-1779893953) 公开说明长期任务的角色分工、独立上下文与进展交接；不能据此推断 Design 私有运行时内部采用本项目的回执格式。

## 修复一：成功写回执携带可继续使用的事实

新增 `asset_plan_receipts.py`。创建、完整替换、局部 patch、字段 patch、执行合同 reconcile 均通过现有 `ToolWriteReceipt` 返回：

- `current_state.asset_plan_identity`：实际保存的 document ID、version UUID 和数字版本。
- `current_state.base_asset_plan_identity`：实际保存版本的父版本，包含并发字段合并后真实使用的父版本，不把请求中的旧 base 冒充父版本。
- `changed_ids`：实际保存 plan 与实际父 payload 的差异，含变更/删除的 item、work item；字段 patch 不再返回“全部空变更”。不存在变化的 upsert 不被标成实际变更。
- `current_state.sources_changed`：计划制作来源是否实际变化。
- `current_state.execution_contract`：直接调用已有 `assess_asset_plan_execution_contract`，保留具体 code、item/work ID 和原因；这是诊断，不是新增审批、自动迁移或生成完成声明。

模型输入继续压缩成功写的完整正文。`_specialist_turn_handoff` 对旧成功结果仅补回原 `output` 已有的精确 identity 和已确认的 ID 摘要。旧结果没有保存的父版本、字段差异或诊断不猜造；原始记录保持不变。最近已知计划身份和诊断也作为简短 `asset_plan_state` 留存，后续其他 Tool 不会再次擦掉它们。

`retry_asset_plan_attempt` 原样转交实际 create/replace 的内层写回执，避免成功重试再次丢失同一事实。它修复的是完整候选，不按局部 patch 证明未改 item。

无需新增 API 或领域模型字段；沿用 `ToolWriteReceipt` 的现有结构，不回传整份 AssetPlan。

## 修复二：局部写后保留已证明未改的 item 范围

新增 `agent_runtime/asset_plan_context.py`，在现有工作集重建中保留 `get_asset_work_context` 实际读出的 item 切片与原读取参数。不同 ITEM/WORK_ITEM 范围独立；没有读取的字段不补造。

只有以下条件同时满足才保留未改 item：局部写成功；写回执带实际完整变更集合；制作来源未变；该读取的原版本或上一次已证明未改的版本，等于本次实际父版本。保留的正文仍标原 `asset_plan_identity`，另用 `unchanged_through_asset_plan_identity` 说明已经验证未改到哪个版本，不能把旧正文直接重标成新快照。

目标 item、真实来源变更、完整 replace、跨越未观察并发版本时仍失效。index 读取一旦发现此前未观察的新版本，也会清除无法证明未改的旧 item 切片；后续较旧读取不会重新写入当前 item 实体。成功回执缺少比较依据的旧历史只能恢复 UUID，不能获得“未改”证明。

原 READ 仍在正常输入时，不再加一份同值 item 正文；只有多对象 READ 因局部换版移除时，才显露已经保留的未改 item。超出完整请求预算时仍走现有 context snapshot 和 JSON pointer，原正文可精确回读。原始大 READ 和旧 Trace 不删改。

这不代表所有后续读取都能消失：本次受影响 5 个 item 若继续需要完整修改，应按需读取；新媒体和新依赖事实仍需实际来源。当前只补 AssetPlan 的已读 item 范围，没有扩建全项目缓存或自动重建整个 index。

## 验证

`tests/fixtures/asset_plan_write_scope.json` 保存本次 t23 的 11 个实际 item、精确范围与原 t24 写结果；裁掉不参与本问题的分镜、媒体和规格正文。新回执依据实际 v18→v19 保存值计算，明确与原历史 receipt 分开。另保留 t11 写 v15 的真实摘要与按现有 assessor 算出的两条诊断，覆盖非空报告不再晚到。

针对性回归覆盖实际 Registry 字段/批量 upsert 写 → 重启后 Worker 模型输入 → 直接沿用新 UUID 再次保存；t24 的 5 个目标清除、6 个未改 item 原文/来源/范围保留；两个读取范围不合并；旧成功回执兼容；来源变化、完整替换、未知并发父版本、较新 index 之后旧读取不复活；实际失败候选重试保留内层回执；预算外置后的精确回读与最终真实渲染 token 计数。

联合回归：新测试、Harness 工具上下文、Shot 工作集、当前分镜工作集、AssetPlan Runtime、AssetPlan Reconciler 共 **100 passed / 21.01s**。校正非空诊断 fixture 为真正 t11 写记录并补齐最后版本/retry 边界后，**15 项新专测 + AssetPlan Runtime 共 67 passed / 16.98s**；五个改动源文件/测试的 Pyright **0 errors / 0 warnings**。`git diff --check` 通过。

代码已冻结。未发真实 Provider 请求；运行任务尚未加载本改动，不能作为新一轮真实性能或稳定性验收。

## 独立审查补充：只有新 index、没有本 Worker 写操作

冻结前独立只读审查用同一 fixture 去掉 t24 本 Worker 写操作，构造“已读 work-context v18 → 观察其他 Writer 的 index v19”。这是离线重放发现的接缝，不声称运行案例实际发生了这次顺序。原实现删除了 `asset_plan_item` 实体，却仍保留 `read_observations` 中的完整 v18 多对象正文；原新版 index 用例含本 Worker 写操作，旧 READ 已先被写路径清除，因此没有覆盖它。

新增 `asset_work_read_is_stale`，在最终工作集投影之前，仅对 `artifact.get_asset_work_context` 检查其真实 `asset_plan_identity`：同一 document 已成功观察到更高数字版本时，移除旧普通 observation。已有局部写证明保留的 item 实体继续按原规则处理；不同文档、其他 Tool、相同版本或失败 index 不被此次规则清理。显式读取旧版本仍作为当次请求的实际返回交接，但不能在后续决策中重新进入普通保留 observations。

没有删除私有快照或历史引用：`project_retained_read_results` 只为当前 observations 生成投影，不会主动重放旧 snapshot；测试预先保存旧 `context-read-*` 后重开 Store，确认真实 Worker 输入没有旧 item 正文，而原 snapshot 和 SpecialistTurn 逐值不变。旧来源不改标新版本，也不据未知并发推断哪个 item 未改。

新增五个回归节点涵盖直接新 index、后来显式旧读再继续、跨 Store 的实际 Worker 输入、其他文档及相同/失败 index；另断言无关场次正文仍原样保留。最终上下文相关四套 **49 passed / 9.25s**，三个相关源/测试文件 Pyright **0 errors / 0 warnings**。Epicurus 独立只读复核通过，未新增 API/schema、Provider 调用或运行数据修改。
