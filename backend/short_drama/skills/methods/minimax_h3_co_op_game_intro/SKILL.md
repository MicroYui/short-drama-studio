---
id: method.minimax_h3.co_op_game_intro
label: MiniMax-H3：双人游戏开场
description: 适合明确制作双人合作游戏菜单开场的项目，覆盖人物身份、菜单首图、UI 文案与事件节拍。
roles: ["CONCEPT", "REVISION_VERIFIER", "DIRECTOR", "ASSET_REVIEWER", "VIDEO_PROMPT_COMPILER", "EPISODE_REVIEWER"]
shared_role_prompt: ../../shared/official_task_method_role.md
shared_role_footer: ../../shared/official_task_method_footer.md
role_source_sections: {"CONCEPT": ["../../official/minimax_h3/co-op-game-intro-generator/SKILL.cn.md::STEP 1：询问视觉风格", "../../official/minimax_h3/co-op-game-intro-generator/SKILL.cn.md::STEP 2：收集玩家与游戏信息"], "REVISION_VERIFIER": ["../../official/minimax_h3/co-op-game-intro-generator/SKILL.cn.md::STEP 7：常见失败修正"], "DIRECTOR": ["../../official/minimax_h3/co-op-game-intro-generator/SKILL.cn.md::STEP 6：回填视频 prompt，并用 Minimax H3 生成"], "ASSET_REVIEWER": ["../../official/minimax_h3/co-op-game-intro-generator/SKILL.cn.md::STEP 3：生成 GPT 首图提示词", "../../translations/minimax_h3/co-op-game-intro-generator/references/h3-confirmation-image-template.zh_cn.md::核心规则", "../../translations/minimax_h3/co-op-game-intro-generator/references/h3-confirmation-image-template.zh_cn.md::构图", "../../translations/minimax_h3/co-op-game-intro-generator/references/h3-confirmation-image-template.zh_cn.md::角色风格", "../../translations/minimax_h3/co-op-game-intro-generator/references/h3-confirmation-image-template.zh_cn.md::游戏 UI", "../../translations/minimax_h3/co-op-game-intro-generator/references/h3-confirmation-image-template.zh_cn.md::负面约束"], "VIDEO_PROMPT_COMPILER": ["../../official/minimax_h3/co-op-game-intro-generator/SKILL.cn.md::STEP 6：回填视频 prompt，并用 Minimax H3 生成", "../../translations/minimax_h3/co-op-game-intro-generator/references/h3-video-prompt-template.zh_cn.md::提示词原则", "../../translations/minimax_h3/co-op-game-intro-generator/references/h3-video-prompt-template.zh_cn.md::优先级顺序", "../../translations/minimax_h3/co-op-game-intro-generator/references/h3-video-prompt-template.zh_cn.md::参考素材的职责", "../../translations/minimax_h3/co-op-game-intro-generator/references/h3-video-prompt-template.zh_cn.md::角色身份与风格锁定", "../../translations/minimax_h3/co-op-game-intro-generator/references/h3-video-prompt-template.zh_cn.md::负面约束"], "EPISODE_REVIEWER": ["../../official/minimax_h3/co-op-game-intro-generator/SKILL.cn.md::STEP 7：常见失败修正"]}
provenance: UPSTREAM_OFFICIAL_WITH_ADAPTER
upstream_sources: ["MiniMax-H3 d21241f0a4b3acbb34c97dae47fa417b7065e438 / skills/co-op-game-intro-generator/SKILL.cn.md + references", "MiniMax-H3 d21241f0a4b3acbb34c97dae47fa417b7065e438 / skills/co-op-game-intro-generator/meta.yaml [source=official; author-en=MiniMax Hub]"]
user_overridable: true
---
# MiniMax-H3 双人游戏开场 Skill 适配

上游 Markdown 及 `meta.yaml` 按固定版本保存；运行时只把当前角色需要的原文章节及参考译文加载为参考。本文件仅负责映射现有工件和 Tool 边界。

这是双人合作游戏菜单开场的专项项目适配器。该上游方法已覆盖片头身份揭示和一次菜单交互；完整字体系统、真实多状态 UI 演示等额外目标若没有其他来源可核验的上游 Skill 覆盖，应明确记录为能力缺口，不本地补写方法。
