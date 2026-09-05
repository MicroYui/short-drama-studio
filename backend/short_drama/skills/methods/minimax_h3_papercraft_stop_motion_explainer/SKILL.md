---
id: method.minimax_h3.papercraft_stop_motion_explainer
label: MiniMax-H3：纸艺定格讲解
description: 适合纸艺定格讲解项目，把学习目标、纸艺隐喻、布景、镜头、旁白与声音组织成片。
roles: ["CONCEPT", "PLANNER", "REVISION_VERIFIER", "DIRECTOR", "ASSET_REVIEWER", "VIDEO_PROMPT_COMPILER", "EPISODE_REVIEWER"]
shared_role_prompt: ../../shared/official_task_method_role.md
shared_role_footer: ../../shared/official_task_method_footer.md
role_source_sections: {"CONCEPT": ["../../official/minimax_h3/papercraft-stop-motion-explainer/SKILL.cn.md::画面层次与纸艺定格优先级", "../../official/minimax_h3/papercraft-stop-motion-explainer/SKILL.cn.md::STEP 3：提出 3-5 个创意方向"], "PLANNER": ["../../official/minimax_h3/papercraft-stop-motion-explainer/SKILL.cn.md::STEP 12：为已选时长创建分镜", "../../official/minimax_h3/papercraft-stop-motion-explainer/SKILL.cn.md::STEP 13：定义剪辑节奏", "../../official/minimax_h3/papercraft-stop-motion-explainer/SKILL.cn.md::STEP 15：定义转场规则", "../../official/minimax_h3/papercraft-stop-motion-explainer/SKILL.cn.md::STEP 16：定义声音设计"], "REVISION_VERIFIER": ["../../official/minimax_h3/papercraft-stop-motion-explainer/SKILL.cn.md::STEP 19：执行审核清单"], "DIRECTOR": ["../../official/minimax_h3/papercraft-stop-motion-explainer/SKILL.cn.md::STEP 14：定义运镜规则", "../../official/minimax_h3/papercraft-stop-motion-explainer/SKILL.cn.md::STEP 15：定义转场规则"], "ASSET_REVIEWER": ["../../official/minimax_h3/papercraft-stop-motion-explainer/SKILL.cn.md::STEP 4：设计纸艺角色", "../../official/minimax_h3/papercraft-stop-motion-explainer/SKILL.cn.md::STEP 5：设计纸片场景", "../../official/minimax_h3/papercraft-stop-motion-explainer/SKILL.cn.md::STEP 6：规划分层纸雕布景", "../../official/minimax_h3/papercraft-stop-motion-explainer/SKILL.cn.md::STEP 7：规划道具资产库"], "VIDEO_PROMPT_COMPILER": ["../../official/minimax_h3/papercraft-stop-motion-explainer/SKILL.cn.md::STEP 9：撰写单图提示词", "../../official/minimax_h3/papercraft-stop-motion-explainer/SKILL.cn.md::STEP 10：撰写系列图提示词", "../../official/minimax_h3/papercraft-stop-motion-explainer/SKILL.cn.md::STEP 18：提供反向提示词"], "EPISODE_REVIEWER": ["../../official/minimax_h3/papercraft-stop-motion-explainer/SKILL.cn.md::STEP 19：执行审核清单"]}
provenance: UPSTREAM_OFFICIAL_WITH_ADAPTER
upstream_sources: ["MiniMax-H3 d21241f0a4b3acbb34c97dae47fa417b7065e438 / skills/papercraft-stop-motion-explainer/SKILL.cn.md", "MiniMax-H3 d21241f0a4b3acbb34c97dae47fa417b7065e438 / skills/papercraft-stop-motion-explainer/meta.yaml [source=official; author-en=MiniMax Hub]"]
user_overridable: true
---
# MiniMax-H3 纸艺定格讲解 Skill 适配

上游 Markdown 及 `meta.yaml` 按固定版本保存；运行时只把当前角色需要的原文章节加载为参考。本文件仅负责映射现有工件和 Tool 边界。

这是“知识讲解 + 纸艺定格制作”的成套项目适配器。该上游方法未覆盖的独立教学论证责任应明确记录为能力缺口；不得用本地仿写 Skill 冒充覆盖。与其他上游 Skill 组合时也只保留一套学习目标、内容结构和镜头计划。
