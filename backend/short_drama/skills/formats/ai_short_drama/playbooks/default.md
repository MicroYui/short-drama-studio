# AI 短剧本地工件映射

这不是另一套创作流程，只说明当前后端如何承接 Agent 选中的上游 Skill。

1. **Leader 对齐目标与工件**

   读取用户要求、ProjectDocument、已批准工件和紧凑的上游 Skill Catalog，选择能覆盖当前任务的最小方法集，并将一个可独立验收的目标交给 Worker。当前收录 Skill 未覆盖的方法如实记录为能力缺口。

2. **Worker 按上游 Skill 产出**

   Worker 只加载当前职责需要的上游 Markdown 章节，并将结果写入现有 ScriptVersion、AssetVersion、ProductionDocument、ShotPlan、GenerationTask、Take 或 TimelineManifest。Hub/Canvas 动作只映射到当前真实存在的 Tool。

3. **Verifier 验收并退回原 Worker**

   Verifier 使用用户要求、当前工件、所选 Skill 自带检查项和实际视频/音频/图像证据判断是否完成。未通过时返回具体工件、可观察问题与期望结果，由 Leader 退回原 Worker；不新增职责重叠的审校环节或固定复审次数。
