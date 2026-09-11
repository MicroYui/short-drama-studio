# 已有素材候选、完成登记与工具作用域

记录日期：2026-09-12。本文对应同一《一起签》调试项目的实际往返；本轮只修合同说明、可定位错误和工具用途。没有调用 Provider、改写运行中的项目数据库或新增审批流程。

## 实际问题与自主恢复

数据来自 `output/quality-e2e-2026-09-12/runtime/short-drama.sqlite3`，以 SQLite `mode=ro` 读取。项目为 `project-5b8286dc63e6`，素材规划工单为 `work-order-5aa6b485cec14f60acdf9803f9d69ee1`。下列时间均为北京时间，事件原文保留 UTC。

| 时间 / 事件 | 实际动作与结果 | 能确认的问题 |
| --- | --- | --- |
| 05:39:13.014 / 5430，t6 | `qa.list_visual_input_reports` 成功，三个列表均为空。调用理由是读取已有纸笔图片的实际检查记录。 | 该工具只列参考图排序、关键帧比较和输入兼容报告；单图语义检查不在此处。工具名称和旧用途描述不足以解释这个差别。空列表不能证明纸笔图片未经检查。 |
| 05:41:31.859 / 5470 | `artifact.patch_asset_plan` 返回 `INVALID_ARGUMENTS`，本地校验耗时 0.006877 秒。五个新增 item 用 `REUSE_EXISTING` 搭配 work-item `candidate_*`，均缺 item 的精确完成版本。 | 已有文件/候选与已经选定的完成结果是两件事。旧 disposition 无字段说明，错误未给出已有候选的合法保存方式。 |
| 05:42:54.766 / 5500，t11 | Agent 自行改为 `ANCHOR` 并成功保存；理由明确写明保留候选、不重新制作纸笔。 | 原合同能够保存这批已有候选，不能把失败描述为产品禁止素材复用。该恢复发生于本次说明补丁加载之前。 |
| 05:44:31.173 / 5551 | `artifact.reconcile_asset_plan_execution_contract` 返回 `FAILED`，本地执行耗时 0.026725 秒。错误为 `UNKNOWN_WORK_ITEM`，列出 review/both 两个摄影状态 work ID。 | ID 实际存在于其他父 item。请求把它们当作当前主体的 supplemental work；错误没有说明真实归属，易诱发重复找 ID。 |
| 05:45:46.309 / 5577，t17 | Agent 使用 `artifact.patch_asset_plan` 将同一情况说明的三个现成摄影状态归到同一主体，保存成功。 | Agent 完成了一次有效的结构调整，不是再次生成图片；不能把此次成功归因于尚未加载的补丁。 |

五个 item 涉及情况说明的空白、复核签名、双方签名三种状态、报价纸和笔状态；笔 item 含有带帽/去帽两个候选工作项。候选记录本身不自动表示这些工作项全部完成。本文只据实际记录说明工具作用域和状态误用，不从空 QA 清单推断素材质量。

另对该工单 05:35–05:48 范围的 24 份 `model.input` 逐条核对：`response_protocol_feedback` 和 `tool_schema_feedback` 均为空。相对 21 个实际 Tool turn 多出的 3 次模型输出都是合法的首次 schema 请求：

| 模型调用 | 首次请求的 schema | Provider 输入 / 输出 tokens | Provider 耗时 |
| --- | --- | --- | --- |
| `model-b0d44d5ac9c6`，05:37:24–05:37:31 | 规划上下文、完整计划、计划索引和图片能力 | 24,600 / 164 | 6.326829 秒 |
| `model-fd9d827c488c`，05:38:32–05:38:44 | `image.list`、`qa.list_visual_input_reports` | 105,167 / 318 | 11.578385 秒 |
| `model-2e818539baf3`，05:43:58–05:44:10 | `artifact.reconcile_asset_plan_execution_contract` | 104,873 / 247 | 11.370121 秒 |

后续实际输入均保留新 schema，没有再次发现同一 schema。三次合计 234,640 输入 tokens、729 输出 tokens、29.275335 秒，是已发生的模型成本，不是修复后节省值，也不是三次 JSON 修正。第二次选错 QA 清单用途、第三次不清楚父项边界，分别由上述具体合同说明处理；正常按需发现本身不算错误。

## 公开实现能够提供的依据

以下是公开文档中的事实，不是对闭源产品内部 Agent、状态机或检查机制的推测。

- **MiniMax 官方 H3 CLI Skill**直接接受已有本地图片、视频和音频作为参考输入，并要求输入顺序与提示词中的引用顺序一致。其指南要求说明参考内容的实际用途，区分连续性约束与审美偏好；已有结果下载失败应复用原结果，不重新生成。这支持复用真实素材与准确交接，而不要求把本地制作计划改成某个审批状态。[官方 Skill](https://raw.githubusercontent.com/MiniMax-AI/cli/main/skill/h3-video/SKILL.md)、[官方 H3 参考指南](https://raw.githubusercontent.com/MiniMax-AI/cli/main/skill/h3-video/references/h3-video.md)
- **Kling 官方元素库指南**支持从已有图片或人物视频建立可重复使用的角色、物件、场景元素，并通过 `@` 选择。补充视角的 AI 生成是另一个明确能力。由此能确认“选择已有来源”与“需要补充素材”是可区分的操作，不能推导其内部存在与本项目相同的候选/完成字段。[Kling 元素库 3.0 用户指南](https://kling.ai/quickstart/klingai-element-library-3-user-guide)
- **火山引擎官方 Seedance 2.0 介绍**说明图片可以提供主体、场景构图和角色细节，并可以组合图像、视频与音频参考。这说明应围绕本次生成所需信息选择素材；文档没有定义本项目的 AssetPlan、完成登记或 QA 清单。本条引用用于参考用途，不把当时的 API 发布时间说明当作当前账户能力验证。[火山引擎官方介绍](https://developer.volcengine.com/articles/7606009619928449070)
- **Anthropic 的工具工程实践**建议从真实任务中的调用失败和往返改进工具，以清楚的用途、参数和相关输出减少歧义。本次据此把候选与完成、单 item 与跨 item、三类镜头报告与单图检查的范围直接写进对应合同和错误；不新增一层要求 Agent 反复检查的流程。[官方工具工程文章](https://www.anthropic.com/engineering/writing-tools-for-agents)

本地采用的判断是：**候选存在、明确选作完成结果、检查结论及其覆盖范围应分别表达**。已有候选可以继续选用，不因制作项尚未完成而重新生成；当前明确选择且可用的 DRAFT 可以登记完成，不额外要求批准；反过来，文件存在或某次检查通过，也不替 Agent 自动选择所有工作的完成结果。这是结合公开参考方式与本项目实际合同作出的工程决定，不是照搬厂商内部实现。

## 本轮改动

### 1. 解释已有候选怎样合法复用

在 [AssetPlan 合同](../../../backend/short_drama/production_contracts.py) 的 `AssetPlanItem` / `AssetPlanWorkItem` 字段说明和 [素材规划角色](../../../backend/short_drama/skills/shared/roles/asset_planner.md) 原有一条规则中直接说明：

| disposition | 实际含义 |
| --- | --- |
| `ANCHOR` | 尚需准备或选择完成媒体；可承接已有 work-item `candidate_asset_id` / `candidate_asset_version_id`，不意味着重新生成。 |
| `REUSE_EXISTING` | 已明确选定 item 的完成媒体；必须填写精确 `completed_asset_id` / `completed_asset_version_id`。候选不自动等同完成。 |
| `TEXT_ONLY` | 仅文字，不包含制作 work items 或完成媒体。 |

`completed_*` 原说明中的“已批准 canonical”改为“已明确选用、CURRENT 且可用的精确版本，可为 DRAFT”。这与 [service._validate_asset_plan_media_references](../../../backend/short_drama/service.py) 原有执行规则对齐，没有放宽拒绝、失效、引用错误或媒体类型检查，也没有添加 QA 必经门。该校验块以保存的媒体位置及当前状态检查可用性；本文不把它说成对所有路径进行了磁盘实测。

原 `REUSE_EXISTING` 校验条件保持不变；错误增加具体 item ID、缺失的 completed 字段和 `ANCHOR + candidate_*` 的合法替代说明。字段形状不变，OpenAPI 仅描述文字改变。一个 canonical work 与 item 可以一起完成登记，但这不表示其余 work 已完成。

### 2. 跨父项错误返回真实归属

[reconcile_asset_plan_execution_contract](../../../backend/short_drama/asset_plan_reconciler.py) 在当前计划中建立 work ID 到父 item 的映射：

- 选中的 work 实际属于另一 item：返回 `WORK_ITEM_OUTSIDE_SUBJECT`，带请求父项、work ID、实际父项，以及该操作不会移动工作项的说明。
- work 确实不存在：保留 `UNKNOWN_WORK_ITEM`，补充正在协调的 item ID。

`AssetPlanSubjectReconcileIntent` 同步说明 canonical / supplemental 只能选择本父项的工作。需要改变跨 item 引用关系时可明确使用 `artifact.patch_asset_plan_fields`；reconcile 不暗中合并父项。请求中的多项仍先在深拷贝上构造，失败不修改传入计划。

### 3. QA 清单写明报告种类

[qa.list_visual_input_reports](../../../backend/short_drama/tools/qa/__init__.py) 保留原 ID、响应结构和 READ 行为，只把说明改为服务实际返回的三类：`reference_reranks`、`keyframe_comparisons`、`compatibility_checks`，并列出对应检查工具。单图 `qa.inspect_asset_version` 的语义判断可在原 Tool 结果与工单回执中读取，不属于这个清单；清单为空不代表所有图片未经检查。

这次没有新增报告索引，也没有要求所有候选补做检查。后续真实运行是否据此减少误读，需要观察加载新说明后的调用。

## 验证与交付边界

- 候选/完成合同复用 [test_asset_plan_runtime.py](../../../backend/tests/test_asset_plan_runtime.py) 的 5 项有效行为测试：合同依赖合法性、候选重启后仍与完成分开、明确 DRAFT 完成后解除派生依赖、完成媒体可用性，以及实际 Tool schema 的引用职责说明。结果 **5 passed / 1.01 秒**；`production_contracts.py` Pyright **0 errors / 0 warnings**。没有用新文案的逐字镜像断言代替行为验证。
- 根代理运行 [test_asset_plan_reconciler.py](../../../backend/tests/test_asset_plan_reconciler.py) 的 2 个新反例与 6 个既有回归，结果 **8 passed / 0.61 秒**；相关源、QA 和测试 Pyright **0 errors**。两种新反例分别检查“存在于另一父项”和“确实不存在”，均验证原计划未变。首次测试夹具遗漏必要 `origin` 已补齐，未因此改变产品行为。
- 独立只读复核确认父项映射、错误分类与 `AssetPlanSubjectReconcileIntent` 一致，QA 清单描述与 service 的三个实际返回字段一致，未发现新的具体缺陷。没有重复运行根代理已通过的测试。

2026-09-12 **05:48:40.995**，主任务请求本次调试的第 **8 次暂停**，等待已开始的模型结果落盘后统一加载修改。本轮说明修复当时尚未用于上述真实往返。新空项目的视频前端到端验收尚未开始；该调试项目也没有提交 H3 视频。本文不宣称已经提速、消除返工或通过无人干预的稳定性验收。
