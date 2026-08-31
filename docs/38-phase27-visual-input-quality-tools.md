# Phase 27：生成前视觉输入质量 Tools

## 结果

Phase 27 已把 Phase 26 的生成前策略实现为三个独立 Agent Tool，而不是新的固定 workflow：

1. `ReferenceVisualRerankerTool`：从当前项目、当前批准资产谱系和当前 `ReferencePlan` 召回候选，再用一次多模态相对比较选择最小充分参考集；
2. `KeyframeCompareTool`：把同一镜头的多张首帧、尾帧或空间参考放在一次比较中，允许明确返回 `NONE_ACCEPTABLE`；
3. `InputCompatibilityCheck`：先观察每张输入实际可见状态，再比较首尾帧、参考图、前镜边界、动作计划和 H3 路线是否相容。

三个 Tool 均返回 `advisory_only=true`、`proceed_allowed=true`，只保存报告，不自动批准或拒绝图片、不修改 `ReferencePlan`、不选择 H3 Route、不提交媒体生成任务。用户或上层 Agent 可以采用、修改或忽略建议。H3 的 4–15 秒整数、Frame/Reference 互斥和媒体数量等技术合同仍由 Provider Adapter 在真正提交时校验。

本阶段没有调用 MiniMax-H3、GPT Image 2 或其他付费媒体 Provider。

## Tool 合同

### 1. ReferenceVisualRerankerTool

入口：

`POST /api/short-drama/projects/{project_id}/shots/{shot_id}/agent-tools/rerank-references`

行为：

- 只读取目标项目中的 `ReferencePack`、批准 `AssetVersion` 和 `ReferencePlan`；未知或其他项目的 entry ID 直接作为调用合同错误返回；
- 过期主体谱系、非当前批准版本、非图片或媒体缺失的 entry 不进入自动候选；
- 默认候选窗口由当前 requirement 数量、参考输入预算和可用候选共同决定；Agent 也可显式传入候选 entry 和单次比较预算；
- 结构化 priority、role、angle 和 framing 只负责初筛，多模态模型负责比较真实可见身份、服装、机位、构图、空间、道具、冲突和冗余；
- 每个入选参考必须说明自己的职责，不要求三视图，不要求填满 H3 的参考槽位；
- 所有候选存在严重矛盾时返回 `NONE_ACCEPTABLE`。

`max_candidates<=32` 是单次多模态上下文的工程上限，`max_selected<=9` 对齐当前 H3 Reference 图片技术上限；它们不是“每镜必须生成多少张”的创作门控。

### 2. KeyframeCompareTool

入口：

`POST /api/short-drama/projects/{project_id}/shots/{shot_id}/agent-tools/compare-keyframes`

输入包含 2–12 个项目内图片版本和 `START / END / TOPOLOGY_REFERENCE` 位置。Tool 同时读取：

- 当前 ShotPlan；
- 当前 ReferencePlan 与编译后的目标关键帧 brief；
- 当前批准角色、场景和道具参考；
- 可用时的前镜实际尾部抽帧。

模型必须先分别描述候选图片，再判断身份、场景拓扑、道具状态、摄影机方向、动作阶段、接触点、前镜边界和“是否适合继续视频化”。它不能因为 Prompt 要求某个答案就假定图片已经满足，也不能用漂亮构图抵消明确状态矛盾。全部候选都不适合时返回 `NONE_ACCEPTABLE`；报告不会改变任何 `AssetVersion.approval_status`。

### 3. InputCompatibilityCheck

入口：

`POST /api/short-drama/projects/{project_id}/shots/{shot_id}/agent-tools/check-input-compatibility`

返回建议之一：

- `COMPATIBLE`；
- `CHANGE_KEYFRAME`；
- `CHANGE_ROUTE`；
- `SPLIT_SHOT`；
- `NEEDS_REVIEW`。

它不增加门、把手、区域穿越等场景专用 schema。检查只使用通用可见维度：身份、服装、空间拓扑、道具状态、构图、动作边界、接触、摄影机和参考冲突。调用者给出非法 H3 输入组合时，报告记录 `technical_warnings`，但本 Tool 本身仍保持 advisory；真正提交时才由 H3 Adapter 拒绝非法请求。

## 持久化与项目隔离

新增单一 `visual_input_reports` 表，以 `project_id + shot_id + kind + created_at` 索引三类报告。它只保存类型化 JSON 报告，不建立第二项目状态源，也不把媒体复制到跨项目目录。

读取入口：

`GET /api/short-drama/projects/{project_id}/agent-tools/visual-input-reports`

应用重启后可以继续查看：

- reference rerank 历史；
- keyframe comparison 历史；
- input compatibility 历史。

视觉候选解析始终从目标 `ProjectDocument` 开始，显式传入未知或其他项目的 `AssetVersion` / reference entry 会失败，因此不会从另一个剧本误取角色、场景或关键帧。

## AI-native 边界

这些能力不是 PlanningRun 的固定阶段：

- 无人值守 Agent 可以在高风险或候选不明确时调用；
- 用户可以在资产页、关键帧页或自然语言修改后直接调用某一个 Tool；
- 低风险镜头已有清楚批准首帧时可以跳过；
- Agent 可把报告交给后续 Director/Repair Planner，但不能把建议升级成用户不可绕过的门；
- 后续“把角色所有镜头衣服改色”等语义修改仍通过 `ImpactPlan` 找到受影响资产、关键帧和 Take，再按需调用这些 Tool，不新增换衣服专用流程。

## 实现位置

- `backend/short_drama/visual_input_quality.py`：三个 Tool 的请求、报告、观察优先 Prompt、候选解析和输出校验；
- `backend/short_drama/repository.py`：类型化报告持久化；
- `backend/short_drama/service.py`：项目/镜头解析、语言模型边界和报告保存；
- `backend/_routes/short_drama.py`：薄 API；
- `frontend/features/short-drama/api.ts`：生成类型之上的前端调用入口；
- `backend/tests/test_visual_input_quality_tools.py`：Fake 多模态 Provider、持久化、`NONE_ACCEPTABLE`、项目隔离和可覆盖语义。

## 验证

本阶段首先完成以下定向验证：

- Phase 27 新测试：4/4；
- Phase 27 + Reference Planner + Take Comparison：24/24；
- 后端全量回归：772/772；
- 全量 Python Pyright：0 error；
- TypeScript：通过；
- Vite 前端、Electron main 和 preload 生产构建：通过；
- OpenAPI JSON/TypeScript：重复生成通过；
- `git diff --check`：通过。

所有测试使用 Fake Provider 和本地合成图片，没有生成新视频或新图片。构建仅提示本地 `caniuse-lite` 数据较旧，不影响产物生成。

## 下一步

Phase 28 已优先完成 Production Skill 的 constitution、角色 rubric、上下文经验分层和全链路注入收敛。生成后问题时间定位、独立 `RepairPlannerTool` 与轻量 H3 Capability Playbook 顺延到 Phase 29；Phase 27 的报告仍将作为输入之一，但不会变成固定流程或创意硬门。
