# 镜头保存回执：区分生成参考预览与实际保存内容

## 真实问题与边界

2026-09-12 第九批调试恢复后，文字证据 Worker `work-order-a318e1e27639488fab20ba34fc3a80d8` 的 t7 `shot.save_current` 于 07:00:55.701 成功保存 `shot-yqq-03-evidence` revision 3。镜头和唯一 beat 都绑定了 `asset-version-9ed9636918b5`，usage 为 `EDITORIAL`，设计时段为 0–1.5 秒；两处 `dialogue_line_ids` 仍为 `line-yqq-02`。

保存返回的 `reference_plan_preview.selected=[]` 正确：该预览解析视频生成参考，剪辑图版不应进入 H3 输入。但它同时给出 `complexity.action_beat_count=0`，这里数的是参考计划 `action_beats`，不是已保存的 `timeline_beats`。原 Tool 描述没有说清这两个边界。

下一次真实模型输入 `model-a0996bd3d4c0`（`20260911T230057595752-523f0081-model.input.json`）只保留成功状态、revision 与生成预览；成功写入的 `output` 被正常紧凑投影移除，请求正文也只剩标识符。Agent 因“引用预览为空且未显示原时段”在 t8 调用 `artifact.read_current`，确认图版、时段和原对白。回读与保存请求的实际内容一致，差别只有 revision 增加以及运行时分配 cue ID/默认值。这不是保存失败，是成功回执缺少判断下一步所需的事实。

项目为 `project-5b8286dc63e6`，Job 为 `studio-job-20f086971171`。原始 Trace/数据库均未修改；专测夹具只裁剪这两条真实 Tool 记录，并去掉回读中的无关项目正文。第十次暂停实际完成于 07:08:25，之后才开始本补丁。此次补丁未调用任何 Provider。

## 采纳的公开方法

[Anthropic：Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) 建议工具返回与决策相关、可解释的上下文，并明确描述、控制无关 token。本次采用其“返回必要操作事实”的原则：成功保存后直接返回实际引用位置、作用时段和对白覆盖；不让 Agent 根据一个不同用途的诊断对象推断是否保存成功。不引入检查阶段、审批或额外语言调用，也不照搬其他产品架构。

## 实施

- `shot_write_receipts.py` 从保存函数已持有的实际 `ShotPlan` 生成 typed `SavedShotSummary`：shot ID/revision/duration、audio_intent、原始对白 IDs、各 beat 的时间/对白 IDs/sound，以及每个实际 cue 的 scope/beat index/time_range/完整引用字段与精确 Unicode 正文片段。不复制整镜 visual、action、continuity 或整份分镜。
- `shot.save_current` 返回 `saved_shot`，并在显式 `ToolWriteReceipt.current_state` 保留同一摘要；同时明确 `reference_plan_preview_scope=GENERATION_ONLY`。描述解释 EDITORIAL 不进入 selected，以及参考计划动作数量与实际分镜时段数量的区别。预览异常仍只报告诊断问题，不把已完成保存变成失败。
- PM 第一轮通过现有成功写回执投影保留摘要。同值摘要不再额外复制到 entities；下一 Tool 后按 Shot ID 保留为 `saved_shot` 实体。该 Shot 成功写入时先清旧摘要，用实际新输出替换；历史成功输出缺少摘要则保持缺失，不从请求意图补造保存事实。相同或更新 revision 的完整 Shot 读取可替代该摘要，更新 revision 同时清旧生成预览；较旧 pinned 来源不复活旧正文。其他 Shot 和失败保存不受影响。
- 所有摘要仍经过现有 ModelInputCompiler 预算处理；外置后用已有 context_ref/JSON pointer 读回。未建立新的状态存储或历史缓存。

`time_range` 是分镜设计中的作用范围：整镜 cue 为 0–duration，beat cue 采用实际 beat 时窗。它不表示图片已经放进剪辑时间线，也不证明图片质量、对白已实际发声或成片已完成。GENERATION/EDITORIAL 的生成筛选与引用绑定逻辑未改。

## 验证

专测 `test_shot_saved_content_receipt.py` 与既有 `test_shot_write_reference_receipt.py`、`test_asset_worker_shot_context.py` 联合通过 26 项（6.09 秒）：

- 真实 t7/t8 形态：空生成预览与真实一个 1.5 秒 beat、两处 EDITORIAL cue、连续对白可同时正确表达。
- 实际 Registry 保存：回执使用运行时分配的 cue ID、原始精确版本与 Unicode span；混合用途的摘要保留两类 cue，而生成 preview/bindings 不包含 EDITORIAL 图。
- 实际 LanguageSpecialistAgent 下一次模型输入及重开 Store：第一轮只在 receipt 保留一次，另一 Tool 后摘要仍在，不恢复整镜正文，原始持久 Tool 记录不变。
- 同镜空引用、后续修改、同/新 revision 完整读取、较旧 pinned 读取、失败保存与其他镜头范围；历史没有新字段时不凭请求制造摘要。
- 小预算外置后用精确 snapshot pointer 回读等值，最终 token 估算对应实际渲染输入。

既有 `test_harness_tool_context.py`、`test_worker_input_references.py`、`test_model_context_budget.py` 另通过 27 项（3.62 秒）；两批共 53 项。三个修改源文件 Pyright strict 为 0 errors / 0 warnings，`git diff --check` 通过。

上述真实镜头的紧凑 JSON 摘要为 1,536 字符、o200k 估算 698 tokens；完整 Shot 为 2,398 字符、1,276 tokens。数字仅比较两种正文投影，既不是 Provider usage，也不是新增真实整轮耗时收益。本补丁增加了之前缺失的必要事实；减少额外回读的实际效果需下一次受控加载后验证。

只新增内部 Registry typed 输出与回执字段，不改变现有保存请求合同；HTTP Tool 通道仍为 generic output，不宣称新增专属 REST schema。
