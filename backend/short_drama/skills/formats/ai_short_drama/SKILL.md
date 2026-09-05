---
profile: AI_SHORT_DRAMA
label: AI 短剧（本地格式适配）
medium: 按项目和已选上游 Skill 声明的视觉媒介
playbook: playbooks/default.md
user_overridable: true
---
# AI 短剧本地格式适配

本文件不是 MiniMax 官方 Skill，也不再自建一套爆款、爽剧、喜剧、审稿或运镜理论。它只保留当前产品必需的 `AI_SHORT_DRAMA` 数据边界，让用户的剧本、单集、角色/场景资产、镜头、Take 和时间线仍能使用同一套 ProjectDocument 与 Tool Schema。

创作方法由用户需求和 Agent 语义选中的上游 Method 提供：MiniMax Design `drama-series` 作为通用短剧/漫剧项目主骨架，MiniMax-H3 任务方法按具体表达需要组合。具体题材、节奏、爽点、幽默与媒介仍服从 `StoryIntent`、`EndingContract`、项目 Style 和用户要求，不在这个格式适配器中另写一套固定方法。

团队运行只使用 `Leader → Worker → Verifier`：Leader 根据当前工件分解任务，Worker 使用所选上游 Skill 产出工件，Verifier 按用户要求、该 Skill 自带检查项和实际成果验收。未通过时由 Leader 将具体问题退回原 Worker；不再拆出职责重叠的多套审校与返修 Agent。

Provider 限制只从当前 Tool Schema 与 Capability 获取。上游 Skill 中属于 Hub/Canvas 的产品操作，由薄适配层映射到当前工件和 Tool，不创建本 Harness 的固定门禁或重试次数。
