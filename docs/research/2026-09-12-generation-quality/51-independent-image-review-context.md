# 同图独立观察的上下文留存

## 失败样本与具体原因

旧项目 `project-5b8286dc63e6`、工单 `work-order-cde9c3be49f54ba7b2d9baf262f40952` 用同一张 `asset-version-6c3de1f63617` 准备第 11 镜。第 44 回合已有一次整体检查与手笔编辑建议；第 52 回合起发生了下面的实际往返。时间均为 2026-09-12 北京时间，来自持久 Tool turn。

| 回合 | 实际选定范围 | 完成时间 | 后续决定输入 |
| --- | --- | --- | --- |
| t52 | 北侧隔断，参考会议室版本 d42fba8ad6bb | 09:08:22.820917 | model-d2a0eef6cdb1，仅保留 t52 同图观察 |
| t53 | 手笔几何，参考笔版本 f4936a914dc3 | 09:12:14.568066 | model-249105133923，仅保留 t53 同图观察 |
| t54 | 再取北侧隔断几何 | 09:16:10.776785 | model-35006289ded8，仅保留 t54 同图观察 |
| t55 | 再取手笔几何 | 09:20:14.915914 | 暂停边界收尾；本修复不恢复旧案例 |

t53、t54、t55 的实际 reason/request 明确写到所需坐标没有保留，因而只补取该部分几何。对保存的模型输入作完整 JSON 值检查：t53 后的输入没有 t52 的完整 `edit_guide_suggestion`，t54 后的输入也没有 t53 的完整建议。它们并非 Provider 没返回，也不是暂停恢复遗漏执行。

原因在 `production_manager._fold_specialist_entities`：`asset_review` 只按 `version_id` 替换。它保住了不同图片版本，却把同图不同 `review_focus`、镜头用途或参考源的观察当作同一个结果。`qa.inspect_asset_version` 为 EXTERNAL，不进入 READ observations；检查实体又没有原 Tool attempt 地址。后一次检查覆盖前一次，正常输入只能看到最新一个建议，Worker 最后选择了重复视觉 QA。

这份旧长案例保留为失败样本。用户已要求先集中修复并离线回放，再以新空项目做完整 E2E；本次没有恢复旧 Job、改写其记录或增加 Provider 调用。

## 公开方法与本地采纳

Anthropic 的 [Context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) 强调有限上下文中保留当前决策需要的高价值信息，并让完整内容按需精确读取；[Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) 强调工具结果应提供相关内容和明确语义，并用真实任务记录验证工具设计。本次重新读取这两份官方来源，将原则用于这个已经发生的覆盖缺陷，没有引入新的记忆系统、主题分类器或审核流程。

图片版本是被观察对象，检查范围是另一项必要身份。新折叠键由项目、Asset、精确版本和实际 request 构成：`shot_id`、原样 `review_focus`、有序 `canonical_reference_version_ids`、`regions` 及已有请求中其他字段。缺省值与其显式默认值等价，数组顺序保留；没有用词法或正则判断“背景”“手”。相同范围只留下最新成功观察；新的 null 建议会清掉该范围的旧建议，失败调用不覆盖成功观察。

改写 focus 的两段文字不能自动证明等价，因此 t52/t54、t53/t55 分别保留。它们的序号与请求范围明确可见，可由 Agent 根据本次所需内容选择，系统不擅自合并两份不同几何。无 request 的历史调用继续按默认检查范围折叠；这不承诺能补出历史从未保存的意图。

## 最小实现

仅修改 `production_manager.py` 的工作集与 QA 实体连接处：

- 私有字典使用完整范围键；模型只看到短实体 ID、来源序号及一份 `review_scope`，不重复长键文本。
- 保留当前 `recommended`、score、summary、issues、证据引用、实际布局/区域观察，以及完整 crop/edit-guide/plane 建议。它们都是原结果，不合并坐标、不自动确认已修复或已采用。
- 为每条成功观察附上现有 `ToolAttemptRef`：项目、run、工单、原 turn ID 和 Tool ID。已有 `agent.get_tool_attempt` 可直接读取原参数或完整结果；实际 Worker 的已见引用集合也包含这些地址，可加载已有读取合同。
- 总请求继续经过既有 `ModelInputCompiler`。内容过大时由原 `context_ref` / JSON pointer 和 `agent.read_context` 分页保存与回读，本次没有第二套历史副本、每轮历史回放或新的 Tool/API。

## 真实输入离线回放

采用保存的三个实际模型输入为基线，其他字段不变，仅把这张图片的旧单条投影替换为修复后的完整同工单观察。原 t44 在完整工单中确实存在，故离线回放也保留它；测试 fixture 同样包含 t44 和 t52–55 的原参数、结果及所有几何点。

| 实际 call | 原可见序号 → 新可见序号 | 原输入估计 → 重投影估计 | 增量 |
| --- | --- | --- | --- |
| model-d2a0eef6cdb1 | 52 → 44、52 | 81,132 → 83,395 | +2,263 |
| model-249105133923 | 53 → 44、52、53 | 80,949 → 85,006 | +4,057 |
| model-35006289ded8 | 54 → 44、52、53、54 | 81,312 → 87,018 | +5,706 |

这是同源完整文本用 o200k 计算的估计，包含原 instructions，与 Provider usage 不混算。新增输入是此前缺失的范围、建议和精确回读地址；t53 后 t52/t53 的建议同时逐值存在，t54 后仍然存在。不能把这次离线结果说成新一轮已节省这些 QA 的耗时；是否减少真实重复检查和总制作时间，留给新项目 E2E。

对应实际输入文件为 `20260912T010852371672-ab97397d-model.input.json`、`20260912T011227170795-4fe2e42d-model.input.json`、`20260912T011614172504-48b9c8e8-model.input.json`，均位于原 Job Trace 目录，未改写。

## 回归与交付边界

`test_asset_review_scopes.py` 覆盖四个真实历史前缀重建 → Service/Store 重启 → 实际 `LanguageSpecialistAgent.execute` 输入；每条原建议和范围逐值保留，并通过真实 Registry 的 `agent.get_tool_attempt` 回读原请求/结果。另覆盖 focus、Shot、引用版本、引用顺序和区域变化；同范围更新及 null；无 request/default 兼容；失败不冒充观察；实际 Fake 视觉 QA 两个范围后接其他 Tool；总预算外置后分页恢复全部实体及原 Tool 结果。测试只使用现有 Fake 和临时数据库。

相关 9 个测试文件共 243 项通过（133.38 秒），包括 PM、QA 建议、Worker 上下文和总请求预算。随后仅补入真实 t44 fixture 并重跑本文件，11 项通过（10.17 秒）；PM 与新测试的 Pyright 为 0 errors / 0 warnings，`git diff --check` 通过。独立只读复核核对范围键、同范围替换、原 attempt 地址及预算分页，没有发现具体阻断，未重复执行测试。

未改 HTTP/OpenAPI 合同、QA Tool effect、Provider 生成业务、审批或运行调度。没有自动推断两个改写请求的语义相同，也没有给旧观察补写新结论。
