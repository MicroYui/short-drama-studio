---
id: method.minimax_h3.handdrawn_live_video
label: MiniMax-H3：手绘实拍融合短片
description: 适合手绘实拍融合的单场景创意短片，围绕真实接触、连续变形、逃跑路线与慢半拍追拍组织画面。
roles: ["CONCEPT", "REVISION_VERIFIER", "DIRECTOR", "VIDEO_PROMPT_COMPILER", "EPISODE_REVIEWER"]
shared_role_prompt: ../../shared/official_task_method_role.md
role_source_sections: {"CONCEPT": ["../../official/minimax_h3/handdrawn-live-video-generator/SKILL.cn.md::Step 1：把用户意图理解为同语种约束", "../../official/minimax_h3/handdrawn-live-video-generator/SKILL.cn.md::Step 2：每次都重新发明创意内容"], "REVISION_VERIFIER": ["../../official/minimax_h3/handdrawn-live-video-generator/SKILL.cn.md::Step 5：交付"], "DIRECTOR": ["../../official/minimax_h3/handdrawn-live-video-generator/SKILL.cn.md::Step 4：Prompt 写作规则"], "VIDEO_PROMPT_COMPILER": ["../../official/minimax_h3/handdrawn-live-video-generator/SKILL.cn.md::Step 3：必需输出格式", "../../official/minimax_h3/handdrawn-live-video-generator/SKILL.cn.md::Step 4：Prompt 写作规则"], "EPISODE_REVIEWER": ["../../official/minimax_h3/handdrawn-live-video-generator/SKILL.cn.md::Step 5：交付"]}
provenance: UPSTREAM_OFFICIAL_FEATURED_WITH_ADAPTER
upstream_sources: ["MiniMax-H3 d21241f0a4b3acbb34c97dae47fa417b7065e438 / skills/handdrawn-live-video-generator/SKILL.cn.md", "MiniMax-H3 d21241f0a4b3acbb34c97dae47fa417b7065e438 / skills/handdrawn-live-video-generator/meta.yaml [source=official-featured; author-en=MiniMax Hub User]"]
user_overridable: true
---
# MiniMax-H3 手绘实拍融合 Skill 适配

上游 Markdown 及 `meta.yaml` 按固定版本保存；运行时只把当前角色需要的原文章节加载为参考。本文件仅负责映射现有工件和 Tool 边界。

这是单场景手绘实拍融合方法的项目适配器。它可以服务某个短片或某些镜头，但只有项目 Bible 明确采用时才影响更大范围的视觉表达，不能因 Skill 被选中就改写整部作品的 Style。
