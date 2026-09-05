---
id: method.minimax_h3.3d_animation_short
label: MiniMax-H3：3D 动画短片
description: 适合明确采用 3D 动画制作范式的项目，覆盖角色/场景卡、镜头表、分镜、单镜与合成。
roles: ["CONCEPT", "PLANNER", "REVISION_VERIFIER", "DIRECTOR", "ASSET_REVIEWER", "VIDEO_PROMPT_COMPILER", "EPISODE_REVIEWER"]
shared_role_prompt: ../../shared/official_task_method_role.md
role_source_sections: {"CONCEPT": ["../../official/minimax_h3/3d-animation-short-generator/SKILL.cn.md::全局视觉风格锁"], "PLANNER": ["../../official/minimax_h3/3d-animation-short-generator/SKILL.cn.md::STEP 5：六列标准镜头信息表"], "REVISION_VERIFIER": ["../../official/minimax_h3/3d-animation-short-generator/SKILL.cn.md::STEP 9：最终复盘", "../../translations/minimax_h3/3d-animation-short-generator/references/qc-checklist.zh_cn.md::步骤 9：最终审核"], "DIRECTOR": ["../../official/minimax_h3/3d-animation-short-generator/SKILL.cn.md::按镜抽取（重点迭代模式）", "../../translations/minimax_h3/3d-animation-short-generator/references/storyboard-guidelines.zh_cn.md::镜头级提取（高频迭代模式）"], "ASSET_REVIEWER": ["../../official/minimax_h3/3d-animation-short-generator/SKILL.cn.md::STEP 3：角色卡", "../../official/minimax_h3/3d-animation-short-generator/SKILL.cn.md::STEP 4：场景卡"], "VIDEO_PROMPT_COMPILER": ["../../official/minimax_h3/3d-animation-short-generator/SKILL.cn.md::模型相关 prompt 形态"], "EPISODE_REVIEWER": ["../../official/minimax_h3/3d-animation-short-generator/SKILL.cn.md::STEP 9：最终复盘", "../../translations/minimax_h3/3d-animation-short-generator/references/qc-checklist.zh_cn.md::步骤 9：最终审核"]}
provenance: UPSTREAM_COMMUNITY_WITH_ADAPTER
upstream_sources: ["MiniMax-H3 d21241f0a4b3acbb34c97dae47fa417b7065e438 / skills/3d-animation-short-generator/SKILL.cn.md + references", "MiniMax-H3 d21241f0a4b3acbb34c97dae47fa417b7065e438 / skills/3d-animation-short-generator/meta.yaml [source=community; author-en=MiniMax Hub User]"]
user_overridable: true
---
# MiniMax-H3 3D 动画短片 Skill 适配

上游 Markdown 及 `meta.yaml` 按固定版本保存；运行时只把当前角色需要的原文章节及参考译文加载为参考。本文件仅负责映射现有工件和 Tool 边界，不另写一套 3D 创作方法。

这是 3D 动画制作方法的项目适配器，不是 `AI_SHORT_DRAMA`、`ANIME` 或其他格式的替代项。格式继续约束观看与叙事生产语境，本方法只在项目确实采用该 3D 路线时补充资产和镜头制作方法。
