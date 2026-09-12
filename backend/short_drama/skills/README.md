# 制片方法

这里保存当前产品使用的创作方法。用户目标、范围和风格高于方法建议；方法不决定工具白名单、固定步骤或确认次数。

## 目录职责

| 位置 | 内容与加载方式 |
| --- | --- |
| `../prompts/runtime/` | 共同原则、Leader/Worker 职责及输出协议，由运行时读取 |
| `shared/roles/` | 剧本、分镜、资产、编译和检查职责，按当前角色加载 |
| `methods/` | 以用途命名的 27 个方法；SKILL.md 供阅读，manifest 指定实际角色/阶段章节 |
| `formats/` | 短剧、动画、口播和通用视频的必要补充；Style 仍属于项目 |
| `providers/minimax_h3/` | H3 专用输入模式、参数、引用顺序与提示词编译 |
| `sources/` | 实际复用的方法原文、相关参考及译文；保留来源与作者归属，不作为额外方法重复展示 |
| `workflows/` | 跨阶段制作的参考说明；不决定用户任务的停止点 |

## 可选方法

| 目录与说明 | 当前 Skill ID |
| --- | --- |
| [短剧与连续剧集](methods/drama_series/SKILL.md) | `method.drama_series` |
| [广告与 TVC](methods/advertising/SKILL.md) | `method.advertising` |
| [音乐视频](methods/music_video/SKILL.md) | `method.music_video` |
| [3D 动画短片](methods/animation_short/SKILL.md) | `method.animation_short` |
| [品牌宣传片](methods/brand_promo/SKILL.md) | `method.brand_promo` |
| [双人游戏开场](methods/co_op_game_intro/SKILL.md) | `method.co_op_game_intro` |
| [手绘与实拍融合](methods/handdrawn_live/SKILL.md) | `method.handdrawn_live` |
| [极简产品广告](methods/minimalist_product_ad/SKILL.md) | `method.minimalist_product_ad` |
| [歌词音乐视频](methods/lyric_video/SKILL.md) | `method.lyric_video` |
| [纸张拼贴讲解](methods/paper_collage_explainer/SKILL.md) | `method.paper_collage_explainer` |
| [纸艺定格讲解](methods/papercraft_stop_motion/SKILL.md) | `method.papercraft_stop_motion` |
| [动漫与游戏宣传片](methods/anime_game_promo/SKILL.md) | `method.anime_game_promo` |
| [Blender 建模与动画](methods/blender_workflow/SKILL.md) | `method.blender_workflow` |
| [After Effects 动效与合成](methods/compositing_workflow/SKILL.md) | `method.compositing_workflow` |
| [达人口播与产品测评](methods/creator_video/SKILL.md) | `method.creator_video` |
| [商品组图与电商视觉](methods/ecommerce_images/SKILL.md) | `method.ecommerce_images` |
| [教学与科普视频](methods/educational_video/SKILL.md) | `method.educational_video` |
| [Photoshop 精修与排版](methods/image_editing_workflow/SKILL.md) | `method.image_editing_workflow` |
| [Houdini 程序化场景](methods/procedural_3d/SKILL.md) | `method.procedural_3d` |
| [TouchDesigner 实时视觉](methods/realtime_visuals/SKILL.md) | `method.realtime_visuals` |
| [创作方法编写](methods/skill_authoring/SKILL.md) | `method.skill_authoring` |
| [创作方法检查](methods/skill_review/SKILL.md) | `method.skill_review` |
| [影视片头与标题设计](methods/title_sequence/SKILL.md) | `method.title_sequence` |
| [产品界面与交互动效](methods/ui_motion/SKILL.md) | `method.ui_motion` |
| [视频拆解与重制](methods/video_deconstruction/SKILL.md) | `method.video_deconstruction` |
| [动态字体与视觉包装](methods/visual_packaging/SKILL.md) | `method.visual_packaging` |
| [声音参考与音色复用](methods/voice_reference/SKILL.md) | `method.voice_reference` |

目录名、Skill ID 和产品名称表达用途；产品概览与运行章节不重复展示来源品牌。原始声明和归属集中保存在 sources、manifest 元数据及项目 NOTICES 中；实际 Provider 标识和旧 ID 兼容不改写。

本次读取的 19 项资料对应 16 个新增方法及广告、MV、H3 三项更新。当前 27 项包括 17 项创作、1 项拆解、1 项图片、1 项声音、5 项软件工作流和 2 项方法维护。软件、声音克隆和运行文件安装的执行条件通过 `requirements` 同时提供给 Leader、Worker 与产品详情；未接入的工具不宣称可执行。

## 一次调用实际读取什么

`loader._read_markdown` 读取独立 JSON manifest；`ComposableProductionSkill.prompt_for(role, stage)` 优先选择精确阶段片段。`production_profiles` 将多个方法共用的前言和后记按来源路径只注入一次，方法中间部分包含当前角色需要的知识。

27 个方法的全部默认角色和阶段映射均指向本地运行章节，保留各方法的摄影、表演、资产、视觉、声音和剪辑知识；来源中的私有 Tool、固定确认轮次和不适用的时长/画幅要求不进入当前运行正文。原有 11 项检查保持；新增方法同样验证实际角色与阶段加载。体裁补充也遵循当前工作范围、明确选择的可用素材和当前编辑时间线，不要求先读取整份项目或取得素材批准标签。

27 个方法的 `POST.DIRECTOR` 都有明确映射；后期导演使用 `shared/roles/post_director.md`，不回退加载分镜导演职责或体裁中的分镜补充。短剧 `VIDEO_PROMPT_COMPILER` 与 `VISUAL_GEN.DIRECTOR` 使用本地 `references/video-compilation.md` 的叙事、声音与真实边界方法；Design 的 H3 固定中文骨架保留在来源目录，但不再作为当前 API 的实际编译格式注入。当前 H3 Provider Skill 是唯一输出语法来源。

SKILL 正文说明用途、输入、方法、交付与检查；有外部依赖时列出执行条件。正文供阅读，不会自动整篇注入所有角色。本地有效经验必须进入实际角色/阶段映射，例如短剧方法的 `references/project-adaptation.md`。产品方法页提供分类、方法概览、实际角色/阶段章节及执行条件；打开详情不改变方法选择。完整来源资料与运行内容分开保存。

## 已清理内容与旧项目读取

- 删除 8 份独立的 `story_repair.md` / `review_router.md`，将有用内容分别合入 `concept.md` / `planner.md`；旧角色在 loader 中解析到当前文件，不再维护重复正文。
- 删除 3 份没有加载入口的旧模型选择、固定回退和镜头表译文，以及对应 3 份闲置英文参考；删除 8 份未加载的英文 SKILL 副本，保留实际使用的上游中文原文和作者元数据。
- 清除已退休 narrative 和方法目录、空的角色/参考目录等 25 个空目录，不另建 archive 副本。
- `critic`、`final_cold_read` 仍被现有审看/比较能力调用；`asset_reviewer` 仍有实际调用及旧 WorkOrder 恢复用途。这些文件不按名字判断为废弃。
- 旧方法名称仅在 [Skill ID 兼容表](../skill_ids.py)保留。项目、计划、运行和工作上下文读取时解析为新 ID；旧 Trace 文件保持原值，不批量改写实验历史。

修改方法后，核对实际片段、引用与来源；目录迁移还要验证旧项目读取、方法选择保存及打包后的资源加载。
