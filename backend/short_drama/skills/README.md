# 制片方法

这里保存当前产品使用的创作方法。用户目标、范围和风格高于方法建议；方法不决定工具白名单、固定步骤或确认次数。

## 目录职责

| 位置 | 内容与加载方式 |
| --- | --- |
| `../prompts/runtime/` | 共同原则、Leader/Worker 职责及输出协议，由运行时读取 |
| `shared/roles/` | 剧本、分镜、资产、编译和检查职责，按当前角色加载 |
| `methods/` | 以创作用途命名的 11 个方法；SKILL.md 供阅读，manifest 指定实际角色/阶段章节 |
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

目录名、Skill ID 和产品名称表达创作用途。上游归属继续保存在各方法的“来源与加载”、manifest 的 `provenance` / `upstream_sources`、[来源说明](sources/README.md)及项目 NOTICES 中。H3 专用适配保留供应商名称。

## 一次调用实际读取什么

`loader._read_markdown` 读取独立 JSON manifest；`ComposableProductionSkill.prompt_for(role, stage)` 优先选择精确阶段片段。`production_profiles` 将多个方法共用的前言和后记按来源路径只注入一次，方法中间部分包含当前角色需要的知识。

SKILL 正文统一为用途、输入、方法、交付、检查、来源与加载六部分，供维护者阅读，不会自动整篇注入所有角色。本地有效经验必须进入实际角色/阶段映射，例如短剧方法的 `references/project-adaptation.md`。产品方法页目前显示名称、简介和偏好选择，完整正文与来源详情仍待接通。

## 已清理内容与旧项目读取

- 删除 8 份独立的 `story_repair.md` / `review_router.md`，将有用内容分别合入 `concept.md` / `planner.md`；旧角色在 loader 中解析到当前文件，不再维护重复正文。
- 删除 3 份没有加载入口的旧模型选择、固定回退和镜头表译文，以及对应 3 份闲置英文参考；删除 8 份未加载的英文 SKILL 副本，保留实际使用的上游中文原文和作者元数据。
- 清除已退休 narrative 和方法目录、空的角色/参考目录等 25 个空目录，不另建 archive 副本。
- `critic`、`final_cold_read` 仍被现有审看/比较能力调用；`asset_reviewer` 仍有实际调用及旧 WorkOrder 恢复用途。这些文件不按名字判断为废弃。
- 旧方法名称仅在 [Skill ID 兼容表](../skill_ids.py)保留。项目、计划、运行和工作上下文读取时解析为新 ID；旧 Trace 文件保持原值，不批量改写实验历史。

修改方法后，核对实际片段、引用与来源；目录迁移还要验证旧项目读取、方法选择保存及打包后的资源加载。
