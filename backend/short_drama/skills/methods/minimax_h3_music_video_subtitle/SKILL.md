---
id: method.minimax_h3.music_video_subtitle
label: MiniMax-H3：歌词贴字 MV
description: 适合需要歌词贴字 MV 的项目，重点处理歌词时序、空间字幕、人物/场景参考和多镜连续性。
roles: ["CONCEPT", "WRITER", "PLANNER", "REVISION_VERIFIER", "DIRECTOR", "ASSET_REVIEWER", "VIDEO_PROMPT_COMPILER", "EPISODE_REVIEWER"]
shared_role_prompt: ../../shared/official_task_method_role.md
shared_role_footer: ../../shared/official_task_method_footer.md
role_source_sections: {"CONCEPT": ["../../official/minimax_h3/music-video-subtitle-generator/SKILL.cn.md::核心原则", "../../official/minimax_h3/music-video-subtitle-generator/SKILL.cn.md::STEP 2：Creative Contract"], "WRITER": ["../../official/minimax_h3/music-video-subtitle-generator/SKILL.cn.md::1.3.0 歌词锁定与歌词先行补全"], "PLANNER": ["../../official/minimax_h3/music-video-subtitle-generator/SKILL.cn.md::1.2 目标时长与多镜头拆解架构", "../../official/minimax_h3/music-video-subtitle-generator/SKILL.cn.md::1.3 音乐窗口", "../../official/minimax_h3/music-video-subtitle-generator/SKILL.cn.md::STEP 3：参考图分工与场景采样", "../../official/minimax_h3/music-video-subtitle-generator/SKILL.cn.md::STEP 6：BGM 连续性与多镜头自然衔接系统"], "REVISION_VERIFIER": ["../../official/minimax_h3/music-video-subtitle-generator/SKILL.cn.md::STEP 7：审查清单（Checklist）"], "DIRECTOR": ["../../official/minimax_h3/music-video-subtitle-generator/SKILL.cn.md::视觉与剪辑语言", "../../official/minimax_h3/music-video-subtitle-generator/SKILL.cn.md::文字包装规则", "../../official/minimax_h3/music-video-subtitle-generator/SKILL.cn.md::6.2 多镜头自然衔接系统（Natural Stitching Protocol）"], "ASSET_REVIEWER": ["../../official/minimax_h3/music-video-subtitle-generator/SKILL.cn.md::STEP 3：参考图分工与场景采样"], "VIDEO_PROMPT_COMPILER": ["../../official/minimax_h3/music-video-subtitle-generator/SKILL.cn.md::STEP 5：Prompt 结构模板"], "EPISODE_REVIEWER": ["../../official/minimax_h3/music-video-subtitle-generator/SKILL.cn.md::STEP 7：审查清单（Checklist）"]}
provenance: UPSTREAM_OFFICIAL_FEATURED_WITH_ADAPTER
upstream_sources: ["MiniMax-H3 d21241f0a4b3acbb34c97dae47fa417b7065e438 / skills/music-video-subtitle-generator/SKILL.cn.md", "MiniMax-H3 d21241f0a4b3acbb34c97dae47fa417b7065e438 / skills/music-video-subtitle-generator/meta.yaml [source=official-featured; author-en=MiniMax Hub User]"]
user_overridable: true
---
# MiniMax-H3 歌词贴字 MV Skill 适配

上游 Markdown 及 `meta.yaml` 按固定版本保存；运行时只把当前角色需要的原文章节加载为参考。本文件仅负责映射现有工件和 Tool 边界。

这是歌词贴字 MV 的专项项目适配器。该上游方法未覆盖的普通音乐驱动、纯表演或其他 MV 目标应明确记录为能力缺口；与其他上游 Skill 组合时不能形成两份 Master Audio、歌词时序或镜头节拍。
