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

Leader 依据用户目标安排分工，Worker 使用当前需要的方法完成并保存内容。独立检查按实际需要安排，也可以由制作者自查；无需先获得 Verifier 的通过标记。发现问题时给出具体内容与影响，由负责的 Agent 定向修复。

Provider 限制只从当前 Tool Schema 与 Capability 获取。上游 Skill 中属于 Hub/Canvas 的产品操作，由薄适配层映射到当前工件和 Tool，不创建本 Harness 的固定门禁或重试次数。
