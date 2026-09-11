# 近景资产 Worker 的上下文效率修复

日期：2026-09-12。范围为本轮真实视频前调试的三个近景请求；没有调用 Provider，没有改运行数据库或原始 Trace。本批在后续合适停点加载，当前调试继续由主任务控制。

## 1. 真实输入为什么有 44k–48k

原始文件位于 output/quality-e2e-2026-09-12/runtime/outputs/short-drama-projects/project-5b8286dc63e6/studio-trace/studio-job-20f086971171/。

| 调用 / 原始 input 文件 | o200k 文本输入估算 | Provider 实际 input / output | 原调用耗时 |
| --- | ---: | ---: | ---: |
| 赵衡，model-4fc5808585c7；20260911T204509308576-2b305de2-model.input.json | 44,182 | 44,192 / 280 | 8.934753s |
| 许岚，model-e689ae5ea09e；20260911T204516025138-e1b623e8-model.input.json | 45,689 | 45,699 / 1,143 | 25.752439s |
| 赵衡，model-9a9eed14fc4a；20260911T204518348595-29046806-model.input.json | 48,165 | 48,175 / 1,197 | 20.738872s |

估算包含 instructions 和实际紧凑 input_text；Provider usage 来自相同 call_id 的 provider.language_generation 记录。这三次 image_count 都为 0，是决定图像操作的语言请求，不能将文字中的两张媒体引用当作本次视觉 token。封装差异仍以 Provider 报告为准。

实际分解如下，单字段编码存在边界差异，不能简单相加替代完整请求计数：

| 输入字段 | 赵衡第一次 | 许岚 | 赵衡第二次 |
| --- | ---: | ---: | ---: |
| instructions | 7,565 | 7,565 | 7,565 |
| working_set | 14,610 | 14,029 | 14,610 |
| tool_schemas | 9,135 | 11,123 | 13,118 |
| available_tools | 7,869 | 7,869 | 7,869 |
| context | 2,000 | 2,021 | 2,000 |
| work_order | 1,022 | 1,101 | 1,022 |
| goal | 1,139 | 1,139 | 1,139 |
| response_schemas | 810 | 810 | 810 |

这不是重新注入了整份项目或全部历史。get_asset_work_context 的实际切片包含一个人物 item、两个相关 work（当前近景及被复用的母图依赖）、两张精确媒体、两个 ReferencePack。赵衡带本组五个镜头的索引和选定近景正文；许岚带两个相关镜头。场景仍有当前 20 个镜头 ID 的简短索引，没有 20 份镜头正文。ProductionSpec 的风格、声音与用户目标也是制作判断需要的事实。

model-4fc5808585c7 的真实输出是请求 image.advance、qa.inspect_asset_version、artifact.review_version、artifact.add_reference_entry、shot.save_current、shot.preview_reference_plan 的 schema。后面的 model-9a9eed14fc4a 使用 image.enqueue。前一次是正常的按需发现，并非 JSON 纠正；model-e689ae5ea09e 也直接返回有效 image.enqueue。此修复不把每个发现回合当作错误。

## 2. 采用的公开方法

Anthropic 的 [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) 建议从真实任务和 Trace 评估 Tool，用明确职责、可理解的参数和相关返回内容减少机械负担。本例已有局部字段编辑能力，问题是初始给 Builder 的却是整个计划编辑合同，因此优先改变实际初始输入和职责说明，复用原有服务操作。

Anthropic 的 [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) 建议保留必要事实，以轻量引用和按需读取组织其他内容。本次保留选定工作正文，只对已在同次输入出现的相同引用正文做共享；原内容仍由既有快照和 JSON pointer 精确读取。没有用全局 last-N、字符串截断或整个项目回灌代替当前工作集。

## 3. 实施

### Builder 初始加载小型字段合同

agent_runtime/tool_context.py 将 ASSET_BUILDER 的默认 artifact.patch_asset_plan 改为已有 artifact.patch_asset_plan_fields；ASSET_PLANNER 保持原合同。当前字段 Tool 的参数为顶层 project_id、base_version_id、patches、origin 等，原完整 patch 仍使用 request.field_patches。skills/shared/roles/asset_builder.md 的候选登记段同步真实参数，明确 Agent origin，避免只有热 schema 改了、正文还要求旧入口。

字段 Tool 内部仍转发同一个 service.patch_asset_plan：先合并和验证，再原子保存。候选指针与已采用指针语义不变。需要计划结构变更时仍可发现完整 Tool，没有新增白名单、自动审批或固定流程。

三份历史输入中的完整 patch 描述和输入 schema 约 6,085 tokens，当前字段版约 849 tokens；在相同其他输入下，默认合同替换节省 5,235 tokens。新候选登记说明为本例 instructions 增加 54 tokens。

旧 Worker 已发现的完整 schema **不删除**。恢复时仍保留其缓存并按当前 Registry 取定义，同时加入新的初始字段 Tool。不能把新 Worker 的全部收益当作这两个旧 Worker 下一次恢复就必然获得的收益。

### 目录只传发现所需的紧凑内容

production_manager.py 的 _compact_tool_catalog_entry 保留 id、简短 description、effect，去掉每项重复传入的 version、category、idempotent、tags。完整 Registry 描述、Tool 校验和标签筛选均不变。AgentToolSchemaRequest 仍使用 available_tools 的精确 ID，目录中的全部 133 个 ID 及原用途文本保留。

此函数由 Leader 和 Worker 共用，因此相关规划发现测试也覆盖本改动。三份原请求各节省 3,400 tokens，没有删掉 Tool 的实际输入 schema 或能力。

### 相同引用正文只保留一份

project_retained_read_results 在原有保留读取投影完成后，检查 reference_pack / shot_reference_plan 实体与 read_observations.result.output 中对应集合的正文。仅在以下条件成立时，用已有 context_ref 和 JSON pointer 替换 entity.state：

- 完整 JSON 值相同；对象键顺序不影响比较，但媒体版本、来源字段、数组顺序和标量类型都保留区别。
- 同 run / WorkOrder / source_sequence 的既有读取快照存在，其目标 pointer 内容也与正文相同。
- 替换后的引用比原正文更小。

实体 ID、kind、source_tool、source_sequence 不变；每个 READ 的 scope、对象、视图和分页选择不变。最新正文仍直接出现在原 read_observation，无需为这次共享再发一次读取请求。若最终请求预算又把该 READ 外置，entity 的持久快照引用继续有效，能够跨重启和分页回读。用户正文、最新交接和历史 SpecialistTurn 不改写。

本例两份 ReferencePack 的重复正文可共享，分别净省 578 / 562 / 578 tokens。几个简短 shot_reference_plan 的实体正文比引用标记还小，因此继续原样保留；新实现也覆盖实际较大的同值 shot_reference_plan。没有为小读取额外建立快照，没有引入第二套内容状态。

## 4. 三份真实请求的离线对照

复现工具为 backend/tests/replay_asset_worker_context_efficiency.py：

    cd backend
    .venv/bin/python -m tests.replay_asset_worker_context_efficiency

脚本从原始 model.input 和 Provider 事件读取数据，用 SQLite mode=ro 复制所需的已有 context snapshot 到临时 Store。保留其他实际输入和 schema，只替换上述目录、实体视图、默认合同和候选登记段，再经真实 ModelInputCompiler.prepare 渲染和计数。原始 read_observations 逐值不变，输出前验证共享 pointer；没有改写原 Trace 或生产数据库。脚本需要本地原始调试数据，独立单测使用可随仓库运行的小型 fixture。

| 调用 | 原估算 | 默认 schema 替换对照 | 减少 | 旧 Worker 缓存保留对照 | 减少 |
| --- | ---: | ---: | ---: | ---: | ---: |
| model-4fc5808585c7 | 44,182 | 35,023 | 9,159（20.73%） | 41,113 | 3,069 |
| model-e689ae5ea09e | 45,689 | 36,546 | 9,143（20.01%） | 42,636 | 3,053 |
| model-9a9eed14fc4a | 48,165 | 39,006 | 9,159（19.02%） | 45,096 | 3,069 |

“默认 schema 替换对照”保留历史请求中的其他已知内容，用新 Builder 默认的小合同替代旧默认的大合同；它是受控离线对照，并非新项目首轮输入的预测。旧 Worker 对照保留已发现的大合同，另加入新字段合同。两者都包含完整 instructions 和最终实际渲染 input_text，均未触发额外预算整理。

这些是 **o200k 离线输入估算**，不是新的 Provider usage。尚未据此声称真实时延、返工次数、图像质量或完整新项目端到端已改善。后续真实运行还会加载其他同期已更新 schema 和职责说明，不能把那次输入与本表简单相减来归因。

## 5. 验证与加载边界

- 初版专测与 Harness、引用投影、上下文预算、Skill loader：59 passed / 5.34s。
- Leader/Worker schema 发现、已有引用工作集、实际 AssetPlan 字段 Tool、并行候选与字段保存：11 passed / 13.44s。
- 最后增加“小读取不为共享新建快照”后，独立专测：10 passed / 1.52s。覆盖默认/恢复发现、完整值差异、范围保留、最新匹配、快照不匹配、预算外置和真实 Worker 重开 Store。
- 四个 Python 源/测试文件 Pyright：0 errors、0 warnings；未安装依赖。
- Boyle 已独立只读复核最终三个代码切面与专测，未发现具体缺陷；未重复运行测试。
- 未更改 API / 持久模型字段，不需要重新生成 OpenAPI。主任务选择下次停点重启加载。

专测路径：backend/tests/test_asset_worker_context_efficiency.py。本轮不继续削减 ProductionSpec、当前动作正文或制作专业方法，也不把精确字段 Tool 推广成通用批处理框架。

本批收尾按主任务要求同步了 Builder 原 image.crop 段：当前候选已有 crop_suggestion 时可复用其精确 source_asset_version_id 和 region，结合 reason 与用途决定，避免对同一区域再次视觉读取；建议本身不代表裁切结果通过验收。这一同期能力说明未包含在上述三项输入优化的受控对照中。

## 6. 同期有限补齐

QA 为 EXTERNAL Tool，不进入 READ observations。为使 QA 后执行另一 Tool 时仍保留可复用裁切建议，PM 的现有 asset_review 实体增加可选 crop_suggestion，继续以实际 version_id 区分版本，不改变 Tool effect 或增加历史正文。两个专测覆盖有/无建议，经 QA→其他 READ→重开 Store→实际 Worker 输入后，精确 source、region 和 reason 仍在，原 SpecialistTurn 不变。裁切能力与原始观察见 [26](26-visual-crop-handoff.md)。

对实际事件 4720–4727 的相同 RUNNING 状态写入，AgentRuntime.set_run_status 现在仅在状态与其完成时间一致且没有变化时直接返回。真实状态转换继续保存和记事件；终态缺失完成时间、非终态遗留完成时间仍会修正。阻塞、错误原因、当前目标由各自原操作保存，不因状态相同被跳过。9 个专测覆盖全部状态、恢复、原因修改/清理和历史时间修复；原事件测试也通过。

这批重复事件同时暴露了真实失败计划的调度死循环，不能只用事件去重掩盖；其具体根因、失败 Future 续接和引用合同修复见 [28](28-failed-plan-continuation.md)。
