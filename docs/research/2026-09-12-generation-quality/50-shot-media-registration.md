# 50：镜头保存中的可选具体媒体登记

本改动在基于 `f0e820fffee45888e15d761c695f2ad23a20bc8b` 的独立工作树 `/tmp/short-drama-shot-media-registration`、分支 `codex/shot-media-registration` 实现。没有修改当前调试项目、运行服务或 Provider，没有修改 Skills。是否整合到正在运行的产品由主任务在受控暂停时决定；本地回归不等于新空项目端到端通过。

## 实际问题与采用范围

[44：登记合同](44-registration-contracts.md) 已记录 s14 的实际链路：完成 QA 后，t20 判断选用，t21 登记 AssetPlan 完成字段，t22 登记参考包，t23 保存当前镜头的具体图像 cue。四次语言决策累计 68.698097 秒，四次 Tool 累计 3.410971 秒，从前一次 QA 完成至镜头保存的墙钟为 84.168112 秒。该数据来自调试中的一次实际链路，不是稳定性或性能基准。

t20 的选用判断有真实内容，继续独立保留。之后三次保存针对同一明确图片，分别写不同位置；没有必要为了提交已经确定的字段再要求三次模型决策。因此仅扩展已有 `shot.save_current` 的可选 `media_registration`，允许调用者一次表达已确定的镜头内容、完成登记与参考用途。不新增工具、固定流程、自动批准或自动采用策略，也不替 Agent 决定何时需要组合保存。

Anthropic 的[官方工具设计文章](https://www.anthropic.com/engineering/writing-tools-for-agents)建议围绕有实际意义的任务设计工具、返回相关上下文，而不是逐一包装底层 API。这里借鉴的是对已确定保存意图的合并；公开资料不证明本产品的运行速度，也不证明闭源制片产品内部使用相同实现。

## 具体合同

`shot.save_current` 原 `project_id`、`shot_id`、`request.expected_revision` 和完整当前 `request.shot` 不变。省略 `media_registration` 时仍走原保存方法，原来的修订冲突行为不变。

可选块包含：

| 字段 | 含义 |
| --- | --- |
| `asset_version_id` | 明确选择的同一张当前可用图片，必须出现在本次 Shot 或 beat 的实际 cue 中；接受 CURRENT DRAFT/APPROVED，不改变审核或资产 current/canonical 指针。 |
| `plan_completion` | 可省略。提供已读的 `base_version_id`，以及本次明确要完成的 `item_ids` / `work_item_ids`；只写这些目标的 `completed_asset_id` 与 `completed_asset_version_id`，资产 ID 由精确版本解析。 |
| `reference_entries` | 可省略。复用现有参考条目的主体、用途、角度、景别、来源根、说明等字段；所有条目必须使用上述精确版本。 |

至少有一项计划完成或参考包登记，否则直接省略可选块。没有 AssetPlan 的草稿项目可以仅保存参考包和镜头，不要求先创建计划。当前范围是一个 Shot 和一张已选择图片；不隐式绑定它所服务的所有镜头，也不把镜头 cue 当成已经剪入时间线。

AssetPlan 继续复用已有字段合并保护：不同 item、不同明确 work-item 的并行结果可以合并；目标内容或制作来源已改变时拒绝。若显式选择整项完成，仍保护整项当前内容，不把它改成忽略子工作变化的无条件写入。纯工作项完成可只给 `work_item_ids`，不需要同时写父项或复制 candidates、共享 notes。

参考包沿用现有自然键：主体包内的精确版本、role、angle、framing。相同键且全部规范化元数据相同直接复用；已有元数据不同则返回具体条目冲突，保留用户说明。确实需要修改已有参考用途时仍使用原独立参考包工具，不通过此组合入口猜测覆盖。

## 保存与重试

原有 AssetPlan patch、参考包添加、正式分镜 patch 和草稿单组替换抽取为对传入 ProjectDocument 的内部验证/修改函数，原公开方法继续使用原锁和持久化路径。组合入口在同一服务锁内读取、完整验证并准备三处变化，没有模型请求或媒体生成。

最后使用一条专用仓储方法，在一个 SQLite `BEGIN IMMEDIATE` 中保存 ProjectDocument 和对应既有 Take 失效状态。ProjectDocument 中的正式分镜/草稿、参考计划、输出槽、AssetPlan、参考包及内容事件一起提交；不同集共享资产的父项目保存沿用原仓储逻辑。提交前再利用已有快照合并检查，若另一服务实际改了冲突字段，整次保存拒绝。Take 更新失败也回滚内容和事件，不留下已完成计划但镜头未保存的半次结果。

独立复核补出了写冲突以外的读取前提：若本次只登记而不改 Shot，另一服务可能撤掉 cue；若本次不改资产，另一服务可能拒绝所选版本或更换其文件。仅做写合并不会发现这些情况。专用保存因此在事务内取得包含父项目共享素材的当前内容，完成合并后、第一次内容写入前，重新验证所选版本的可用状态、原归属及文件/URL 来源，确认实际 Shot 仍有该版本的合法 cue，并复用原参考包与 AssetPlan 字段校验。相同参考自然键必须只有一条，避免并发追加生成两个不同 ID 的条目；冲突后重新读取再提交可复用实际已存条目。

这些检查不比较整份 Shot 或 AssetVersion。仅改机位而保留 cue、补充审核备注、新增其他版本并切换资产 current，以及同一参考包追加不同用途，都保留其实际修改。此入口自动产生的项目/参考包更新时间不充当用户内容冲突；真正写入的镜头正文、计划字段或参考元数据仍受原保护。相同内容重试也在事务内核对当前读取前提，但不重写内容或产生事件；回执使用实际当前计划/参考条目 ID，不把未由本次执行的并行更新记成自己的差异。

已有视频文件、Task、审核记录、资产当前选择、无关镜头/制作项不被改写。只在实际 Shot 内容变化时沿用原语义使相关 Take 失效，不删除媒体。相同 Shot 内容、完成指针及参考条目重试不产生新 Shot/AssetPlan 版本、参考条目或内容事件；服务重启后也根据实际保存内容识别，无需新增幂等记录表。

回执复用已有 `saved_shot`，继续说明 GENERATION/EDITORIAL cue 的精确版本、正文位置和真实时段。另返回所选版本、参考条目 ID、实际完成目标和 AssetPlan document/version ID。AssetPlan 部分直接复用实际父版本差异回执，进入现有 Worker 上下文折叠：已改变项不继续宣称是旧状态；未改变的已读项保留原来源版本，并标明通过此次局部保存未改变。没有回灌整份 AssetPlan，持久 Trace 不改写。

## 验证与边界

新集成测试使用实际 ToolRegistry、Service、SQLite 和本地 PNG，无 Provider 调用：覆盖 DRAFT 的三处一起保存、明确工作项与父项的区别、跨重启相同请求重用、其他计划项/同父项其他 work-item/其他 Shot 的人工修改保留、旧目标/参考元数据冲突、非法 cue 和缺失/拒绝/失效媒体不写入、正式分镜与草稿同步，以及实际 Tool 回执进入重启 Worker 模型输入。

提交边界使用真实 SQLite 触发器使 Take 更新失败，确认 ProjectDocument、计划版本、参考包和内容事件全部回滚；另用两个服务和明确的本地提交暂停点复现验证后用户写入。新增并发用例覆盖所选版本被拒绝、文件来源改变、撤掉 cue、已有参考包的同自然键追加、无关机位/审核备注/current 指针/参考用途保留，以及纯重试期间另一个计划项更新后的实际回执版本。红灯先确认了“已拒绝素材仍成功”和“仅机位改变却被更新时间误拒”；修复后分别拒绝前者并保留后者。没有通过 mock 跳过仓储或工具行为。

最终相关回归 **170 passed / 38.42 秒**；5 个修改源文件 Pyright **0 errors / 0 warnings**，`git diff --check` 通过。回归包含新 23 项，以及原镜头保存回执、AssetPlan 运行与回执、参考包、局部拆镜/跨镜对白、StudioWorkspace 和 no-mock 检查。命令使用主项目已安装的 backend/.venv，不安装依赖。独立只读复核已覆盖最终事务、读取前提和回执；多集共享保存链经过代码复核，本波未单独执行新的多集组合登记 fixture，不将其称为实测。

```sh
.venv/bin/python -m pytest tests/test_shot_media_registration.py tests/test_shot_saved_content_receipt.py tests/test_asset_plan_write_receipts.py tests/test_asset_plan_runtime.py tests/test_reference_planner.py tests/test_storyboard_local_edits.py tests/test_storyboard_dialogue_continuation.py tests/test_studio_workspace.py tests/test_no_mock_usage.py -q --tb=short
.venv/bin/pyright short_drama/shot_media_registration.py short_drama/tools/studio/__init__.py short_drama/service.py short_drama/repository.py short_drama/asset_plan_receipts.py
```

当前只证明一次调用能够表达并原子保存这三处已确定内容；没有真实模型重新运行，因此不声称已减少实际总 tokens、语言次数或耗时。用户选用判断、图像质量判断、不同素材或尚未确定用途仍应由 Agent 根据任务分别处理。

## 整合后的计划状态投影接缝

整合提交 `71db175` 的集中只读检查发现：`fold_asset_plan_items` 原先把组合回执的 `current_state` 全部复制进 `asset_plan_state`。其中的 `saved_shot`、引用预览和媒体登记是 Shot 的事实。后续普通镜头保存只更新独立 Shot 状态，计划实体却仍携带上一份镜头摘要。纯内存交错重放确认了计划版本 19 正确、两份 QA 范围仍在，但计划实体内的镜头 revision 1 与最新回执的 revision 2 同时出现。该问题来自整合检查，不是宣称真实调试任务又触发了同样错误。

修正仅在独立分支 `codex/shot-media-plan-context` 的计划模型投影中进行。已核对五个直接 AssetPlan 写 handler 均使用 `asset_plan_write_receipt`，retry 原样透传内层回执；当前计划状态包含 `project_id`、`asset_plan_identity`、`base_asset_plan_identity`、`sources_changed` 和 `execution_contract`。历史兼容适配还支持 `operation`、`base_version_id`、`item_count`、`work_item_count` 和 `notes_updated`。投影保留这十个字段的原值，同时作用于新回执和相同版本的先前状态，避免旧混入字段继续残留。

公开 Tool 回执、持久 SpecialistTurn/Trace、计划 changed_ids 和原项级保留判断都未修改。完整 `saved_shot` 与 `media_registration` 仍在原保存回执；当前镜头由其独立实体/最新回执负责。QA 继续按精确图像、范围和来源分别保留，本次不改其分页或回读机制。

新增九项回归先红后绿：实际 Registry 组合登记、随后普通镜头保存、持久 Worker 重启后的实际模型输入；全部七个计划写入口的当前/历史字段逐值保留；四份实际 QA 回执与组合登记交错后的精确范围、建议坐标、attempt 引用和原记录保持不变。新测试与既有计划回执、QA 范围、Shot 保存回执及组合登记的 Worker 输入回归合计 **54 passed / 25.38 秒**；修改源文件 Pyright **0 errors / 0 warnings**。最终局部独立只读复核通过。没有调用真实模型/Provider，没有修改运行数据，不据此声称端到端性能提升。
