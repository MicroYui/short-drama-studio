---
id: method.minimax_h3.paper_collage_explainer
label: MiniMax-H3：纸张拼贴讲解
description: 适合纸张拼贴讲解项目，把观点或知识拆成拼贴隐喻、构图、镜头、字卡与声音。
roles: ["CONCEPT", "WRITER", "PLANNER", "REVISION_VERIFIER", "DIRECTOR", "ASSET_REVIEWER", "VIDEO_PROMPT_COMPILER", "EPISODE_REVIEWER"]
shared_role_prompt: ../../shared/official_task_method_role.md
role_source_sections: {"CONCEPT": ["../../official/minimax_h3/paper-collage-explainer-generator/SKILL.cn.md::适用场景", "../../official/minimax_h3/paper-collage-explainer-generator/SKILL.cn.md::默认创作目标", "../../official/minimax_h3/paper-collage-explainer-generator/SKILL.cn.md::视觉隐喻"], "WRITER": ["../../official/minimax_h3/paper-collage-explainer-generator/SKILL.cn.md::脚本 / 视觉节奏轨"], "PLANNER": ["../../official/minimax_h3/paper-collage-explainer-generator/SKILL.cn.md::分镜", "../../official/minimax_h3/paper-collage-explainer-generator/SKILL.cn.md::STEP 3：建立静帧规格", "../../official/minimax_h3/paper-collage-explainer-generator/SKILL.cn.md::STEP 5：规划停格组装"], "REVISION_VERIFIER": ["../../official/minimax_h3/paper-collage-explainer-generator/SKILL.cn.md::STEP 7：质量检查"], "DIRECTOR": ["../../official/minimax_h3/paper-collage-explainer-generator/SKILL.cn.md::STEP 5：规划停格组装", "../../official/minimax_h3/paper-collage-explainer-generator/SKILL.cn.md::STEP 6：生成带拼贴音效的 B-roll 片段"], "ASSET_REVIEWER": ["../../official/minimax_h3/paper-collage-explainer-generator/SKILL.cn.md::STEP 3：建立静帧规格"], "VIDEO_PROMPT_COMPILER": ["../../official/minimax_h3/paper-collage-explainer-generator/SKILL.cn.md::STEP 6：生成带拼贴音效的 B-roll 片段"], "EPISODE_REVIEWER": ["../../official/minimax_h3/paper-collage-explainer-generator/SKILL.cn.md::STEP 7：质量检查"]}
provenance: UPSTREAM_OFFICIAL_FEATURED_WITH_ADAPTER
upstream_sources: ["MiniMax-H3 d21241f0a4b3acbb34c97dae47fa417b7065e438 / skills/paper-collage-explainer-generator/SKILL.cn.md", "MiniMax-H3 d21241f0a4b3acbb34c97dae47fa417b7065e438 / skills/paper-collage-explainer-generator/meta.yaml [source=official-featured; author-en=MiniMax Hub]"]
user_overridable: true
---
# MiniMax-H3 纸张拼贴讲解 Skill 适配

上游 Markdown 及 `meta.yaml` 按固定版本保存；运行时只把当前角色需要的原文章节加载为参考。本文件仅负责映射现有工件和 Tool 边界。

这是“内容讲解 + 纸张拼贴表达”的成套项目适配器。该上游方法未覆盖的独立教学论证责任应明确记录为能力缺口；不得用本地仿写 Skill 冒充覆盖。与其他上游 Skill 组合时也只保留一套内容结构、旁白和镜头计划。
