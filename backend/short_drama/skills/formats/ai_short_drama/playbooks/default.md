# AI 短剧本地工件映射

这不是另一套创作流程，只说明当前后端如何承接 Agent 选中的上游 Skill。

1. **Leader 对齐目标与工件**

   读取用户要求、ProjectDocument、现有可用工件和紧凑的上游 Skill Catalog，选择能覆盖当前任务的最小方法集，并将一个可独立验收的目标交给 Worker。当前收录 Skill 未覆盖的方法如实记录为能力缺口。

2. **Worker 按上游 Skill 产出**

   Worker 只加载当前职责需要的上游 Markdown 章节，并将结果写入现有 ScriptVersion、AssetVersion、ProductionDocument、ShotPlan、GenerationTask、Take 或 TimelineManifest。Hub/Canvas 动作只映射到当前真实存在的 Tool。

3. **按需要检查实际结果**

   作者可以自查，也可以请求检查分工。根据用户要求和实际结果判断是否完成；发现缺陷时说明具体位置及影响，再决定修复。保存、检查和采用不设固定顺序，用户明确要求审阅时才等待该项选择。
