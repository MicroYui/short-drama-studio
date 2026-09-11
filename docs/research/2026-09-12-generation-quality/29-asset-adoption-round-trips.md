# 资产采用后的重复读取与保存回执

日期：2026-09-12（北京时间）。范围是当前调试项目中许岚、赵衡近景资产的采用后交接。调查只读 SQLite `mode=ro` / `query_only`、已保存的真实模型输入与源码；没有操作运行任务、调用 Provider 或修改项目。此前真实图像质量判断不能由本调查替代，当前调试也不是新的空项目完整验收。

## 实际往返与耗时

两位 Worker 均执行了下面六步：

`artifact.get_asset_work_context → artifact.add_reference_entry → artifact.patch_asset_plan → artifact.read_current → shot.save_current → shot.preview_reference_plan`

| Worker / 实际 turn | 测量起止（北京时间） | 链路墙钟 | 其间语言调用 | Provider 语言耗时之和 | 六个 Tool 执行耗时之和 |
| --- | --- | ---: | ---: | ---: | ---: |
| 许岚，t14–19 | 05:05:30.989–05:07:41.199 | 130.210 秒 | 5 次 | 120.813 秒 | 0.777 秒 |
| 赵衡，t21–26 | 05:11:55.391–05:13:18.783 | 83.392 秒 | 5 次 | 75.074 秒 | 0.858 秒 |

墙钟从第一个 Tool 的 SpecialistTurn `requested_at` 到最后一个 Tool 的 `updated_at`；语言耗时来自对应 `provider.language_generation`，Tool 耗时来自该 Worker 的 `tool.finished.elapsed_seconds`。不把调度、保存和模型时间相加后冒充完整 Worker 时长，也不计入赵衡时间窗内另一调用方的 `project.get`。这段所有 Tool 均成功，不是失败重试。

原始数据：项目 `project-5b8286dc63e6`，任务 `studio-job-20f086971171`。Trace 根目录为 `output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/`。

| Worker | WorkOrder | 决定再次读取 Shot 的实际模型输入 |
| --- | --- | --- |
| 许岚 | `work-order-8724a4e7ae7741e9bd7530e4636335c5` | `20260911T210639530701-ea774443-model.input.json`，`model-f105d519a684` |
| 赵衡 | `work-order-dee3dc5119354dd5bf58c538226772ae` | `20260911T211235332132-1a4da2a9-model.input.json`，`model-bc6f42a85345` |

## 两处实际合同缺口

**工作切片已经提供完整指定 Shot，但中间写入使它退出下一次模型输入。** 许岚 t14 的 `result.output.storyboard.shots[0]` 与 t17 的 `result.output.content.shot` 逐字段相等，都是 `shot-yqq-08`、revision 1。赵衡 t21 与 t24 同样逐字段相等，都是 `shot-yqq-12-reaction`、revision 1。再次读取没有带回新的镜头事实。

然而，上表两份真实输入的 `data.input_text` 解析后，`working_set.read_observations` 已没有 `artifact.get_asset_work_context`，`working_set.entities` 也没有完整 Shot。此前 `_specialist_working_set` 在写入后调用 `_read_observation_is_superseded`：切片中任一工件的版本改变，就移除整个多对象读取。`add_reference_entry` 和 `patch_asset_plan` 改了 Pack / AssetPlan，连带移除了没有变化的镜头正文。不能把这次回读简单归因于 Agent 忘记或不遵守提示。

源码入口为 `backend/short_drama/production_manager.py` 的上述两个函数，以及 `service.py:get_asset_work_context`。后者按 AssetPlan 绑定的 Storyboard 版本返回任务切片，并非无条件读取最新 Shot。本例来源是 `production-document-version-0b577c62b1de`（数字版本 6），两镜的 revision 1 与随后当前读取一致。留存时必须保留源与精确镜头 revision；本镜真正改动仍需失效，不能把旧计划切片标成恒为当前。

**原保存回执没有实际引用解析结果。** 两次历史 `shot.save_current` 只返回 ID、修订号与通知，没有 `selected`、缺项或警告。随后 `shot.preview_reference_plan` 才提供这些确定性事实：许岚解析出人物、场景与近景三张实际选择，赵衡解析出一张近景。它们不是新的视觉质量检查。`StudioService.save_shot` 已校验本镜 revision、同步正文 cue 与绑定并保存；另一次语言决策只是为了获取保存时就能解析的结果。

`shot.update_reference_plan` 不能替代这次正文绑定。`service.py:_apply_reference_plan_update` 只更新 `requirements / action_beats / interactions / max_reference_images / planning_notes` 等语义参考计划字段；它不回写 ShotReferenceCue、正文字符范围或逐时段具体媒体。实际保存精确引用仍走 `shot.save_current` 与 `studio_references.py:sync_shot_references`。

## 最小修复与当前状态

采用 Anthropic 的 [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) 中按实际任务设计 Tool、返回下一步所需事实并以真实 Trace 评估的原则。本地采用点是修好现有工作集与回执，不新增通用批处理、审批步骤或固定 QA 流程；没有推断 MiniMax 的私有实现。

1. 已由 Arendt 按 Shot ID 留存已经读到的完整指定镜头，带来源和精确 revision。Pack / AssetPlan 写入不删掉无关 Shot；本镜写入仍使旧状态失效。同值正文继续复用既有上下文共享与分页引用，避免增加重复输入。成功写入观察到的 revision 继续阻止旧计划源恢复已失效正文；独立复核发现并补齐了另一顺序边界：即使本 Worker 没有执行写入，先读到 current revision 2、再刷新 pinned revision 1 时，也不能以旧正文覆盖已经知道的新正文。保存回执中的嵌套预览按 Shot ID 保留，后续相关写入按原规则使其失效。
2. 主任务已为原 `shot.save_current` 增加 `ShotWriteOutput.reference_plan_preview` 和可选 `reference_plan_preview_error`，输入不变，Tool version 2。从同次保存返回的 `document / saved / plan` 调用既有纯预览；不重新读取另一份项目快照，不调用模型，不再写入。预览失败时保留写成功及具体诊断，避免为取预览而盲目重试已保存的修改。Builder 方法同步说明可以直接核对回执，相关内容后续改变或没有可用预览时再按需读取。

第二项已独立只读复核：候选 DRAFT 可正常解析；REJECTED 选择留在正文而预览报告缺项；没有自动批准、切换资产 current 或修改其他镜头。`backend/tests/test_shot_write_reference_receipt.py` 两个集成用例覆盖与现有独立预览相等、精确 UUID、旧 revision 重试冲突、其他 Shot / Asset 不变、无图片任务。主任务运行新两例与既有 Registry 共 **5 passed / 3.90 秒**，所改两文件 Pyright 0。独立复核没有重复运行测试。

第一项最终验证为新 **11 项**专测与关联套共 **51 passed / 5.23 秒**，三个 Python 源/测试文件 Pyright 0；源码、测试及最后修订顺序保护已独立只读复核，未发现新的具体阻断。专测为 `backend/tests/test_asset_worker_shot_context.py`，使用 `backend/tests/fixtures/asset_worker_shot_reads.json` 中两份真实裁剪序列，保留完整 Shot、来源与实际引用，删除无关返回正文。覆盖两个写入节点后的实际 Worker 输入、重开 Store、只失效目标镜头、已知/未知写入 revision、current/pinned 两种读取顺序、同值正文指针，以及嵌套预览存在/缺失。

截至原 t16 / t23 的完整真实工作历史离线重建中，新 entity 的 Shot 与随后原 t17 / t24 返回逐字段相等。此前实际请求没有这两份镜头正文，现在分别恢复 **929 / 656 个 o200k 文本 tokens**。这是恢复下一步需要的事实，不宣称这一修复减少输入量，也不将离线回放当作真实模型已经省去回读。

## 判断边界

ReferencePack 条目、制作项完成登记、正文中的具体媒体及用途绑定，是三种不同的保存事实。首次刷新工作切片可以合理获取长时间 QA 后或并行修改后的计划与图集；不应删除所有读取。裁切、观察真实裁切结果，以及赵衡修正实际背景问题的生成与检查，也不是本次识别出的机械往返。DRAFT 可用，不据此新增必须批准的步骤。

原案例中“决定回读当前 Shot”和“决定单独预览”两次语言调用合计分别为许岚 14.581 秒、赵衡 16.560 秒。这是已发生的调用耗时，不是修复后净收益承诺；新的输入、模型判断与并发环境会影响最终时延。后续需在正常产品运行中确认工作集可见性、回执使用及真实调用次数，不能据本地合同修复声称已经缩短整片制作时间或完成新项目端到端验收。
