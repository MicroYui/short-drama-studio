# AI 原生短剧的创作形态、观众行为与 Skill 落地

更新日期：2026-09-01

## 范围修正

本项目中的“短剧”指当前兴起的 AI 原生短剧生产，而不是把传统高完成度真人微短剧交给 AI 辅助，也不等于 anime。

公开案例同时包含 AI 生成的写实人物、二维/三维作品、动态漫、真人主演与 AI 结合，以及更自由的混合表达。中国新闻网的行业报道直接区分了 AI 仿真人剧、二维/三维动画和动态漫等形态；21 世纪经济报道也区分全 AI 制作与真人主导的人机协同。`SOURCE-REPORTED`

这些名称只用于说明市场中已经存在多种表达，不进入代码枚举。项目自己的视觉与节奏由 Agent 根据用户目标写入 Bible 的 Style，自由描述并允许后续修改。

## 本轮结论

1. `AI_SHORT_DRAMA` 是内容消费与生成式生产 Skill，不是视觉风格。
2. 写实生成真人是被完整支持的一种可能，但系统不预设真实皮肤、电影感、演员调度或任何固定审美。
3. 视觉呈现、运动语言、叙事载体和节奏是可组合的创作维度，不是封闭子类型。
4. 共享流程可以详细说明工件、Tool、输入输出与返工责任；创作结果只给判断维度，把选择留给 Agent。
5. 资产、Route、Prompt Compiler 和 Reviewer 必须服从当前 Bible Style，不能把 Provider 偏好反向写成作品风格。
6. 观众行为研究只帮助定位观看风险，不生成固定秒数、镜头数、反转数或统一质量分。

## 产业实现给出的共同生产边界

人民日报海外版、中国新闻网和香港浸会大学 DataStory 的材料都显示，AI 短剧把生产单位从传统剧组现场转向可生成、可筛选和可返修的数字工件。火山引擎 Dramart 等产品也把剧本、角色、场景、分镜和视频组织为连续资产链。`SOURCE-REPORTED`

据此，本项目抽象出的共享链路是：

`目标与来源 → 内容/剧本 → Bible Style 与资产 → 场次/镜头职责 → 生成输入 → 视频候选 → 实际观察与局部返修 → 声音/字幕/时间线 → 单集与系列复核`

这条链路描述工件依赖，不要求 Agent 严格逐节点执行。Production Manager 可以按当前项目跳过、并行、重排或回到前一步；只有持久化恢复和 Provider 真实任务状态所需的顺序留在 Harness 中。

## Style 不是工作流参数表

当前项目使用 `ProjectBible.style` 保存创作选择：

- `medium`：作品如何呈现，可以是任何自然语言描述。
- `cinematography`：构图、视点、运动与剪辑倾向。
- `color`、`texture`：当前作品要保持的视觉关系。
- `forbidden`：用户或创作方案明确不希望出现的偏差。

这些字段不应该由 Profile 自动填入“写实”“动漫”“真实皮肤”等词。用户已说明时直接继承；用户没有说明时，Agent 可以围绕故事和观看体验提出有理由的原创方案。临时生成图、模型默认输出和审核者个人偏好都不能静默覆盖 Style。

## 供 Agent 使用的创作维度

以下维度用于启发和解释选择，不是检查表：

### 视觉呈现

角色和世界可以写实、风格化、平面、立体、实拍、生成或混合。Agent 需要回答的是“这种表达如何服务本故事”，而不是先把项目归入固定桶。

### 运动语言

连续表演、局部动作、姿势变化、图层运动、摄影机运动、稳定画面和蒙太奇都可能成立。当前节拍需要观众看见什么变化，决定需要多少运动。

### 叙事载体

人物对白、旁白、内心音、动作、构图、环境变化、声音、字幕和画面文字可以自由分工。声音与画面应互相证明、补充或形成有意反差，不机械重复。

### 节奏

分别观察故事状态、画面状态与声音状态的变化。它们可以同步，也可以错开形成期待、停顿、反差和回收；快慢、疏密、静动都由段落目标决定。

### 连续性

项目可能更重视角色身份、造型语言、空间关系、道具状态、声音身份、动作边界或媒介切换规则。Reviewer 只检查当前 Style 和故事真正要求保持的内容。

## 观众行为研究如何使用

短视频观看时长与参与预测研究支持把观看过程拆成分段续看、相对观看比例、快速滑走、延迟跳出和重播等不同信号；推荐曝光和视频长度会影响观测结果。`PAPER-REPORTED`

抖音微短剧评论研究和创作者访谈还表明，观众会讨论人物选择、行为合理性、剧情理解和情绪体验，团队会结合评论、转发、梗和完成率调整后续内容。它们研究的并不全是 AI 原生短剧，因此这里只把方法迁移为诊断视角，不把样本结论升级成产品定义。`PAPER-REPORTED`、`INFERRED`

有真实平台数据时，Agent 可以把观看变化与具体内容位置对齐，形成待验证原因假设；没有数据时，只做观看风险预判。点赞、评论、收藏、分享和下一集进入代表不同意图，不能相加为单一创作分数。

## 当前代码落点

| 位置 | 责任 |
| --- | --- |
| [`skills/formats/ai_short_drama/SKILL.md`](../backend/short_drama/skills/formats/ai_short_drama/SKILL.md) | 定义 AI 原生短剧目标、非视觉枚举边界和 Agent 自主性 |
| [`references/ai_native_forms.md`](../backend/short_drama/skills/formats/ai_short_drama/references/ai_native_forms.md) | 描述可组合的 Style 与节奏维度，并明确不写死具体审美 |
| [`references/audience_behavior_and_rhythm.md`](../backend/short_drama/skills/formats/ai_short_drama/references/audience_behavior_and_rhythm.md) | 把平台信号转成可定位的诊断问题 |
| [`playbooks/default.md`](../backend/short_drama/skills/formats/ai_short_drama/playbooks/default.md) | 说明详细工件、Tool 与返工责任，不规定创作答案 |
| `roles/*.md` | 让专业 Agent 只读取与自身判断有关的参考 |
| [`production_planner.py`](../backend/short_drama/production_planner.py) | 让 Planner 在 `style.medium` 中写入项目自己的视觉媒介 |
| [`asset_inspection.py`](../backend/short_drama/asset_inspection.py) | 资产生成和审核服从 Bible Style，不再强制真人写实 |

`AI_SHORT_DRAMA` 与 `ANIME` 的边界是产品目标，而不是画面文件类型：连续 AI 短剧采用动画画面时仍可使用 AI 短剧 Skill；用户要创作以动画视觉语言本身为核心的独立作品时，使用 `ANIME` Skill。

## 明确没有采用的做法

- 不把写实生成真人当默认，也不把 AI 短剧等同于动画。
- 不新增 photoreal、2D、3D、motion-comic 等代码枚举或固定分支。
- 不写死真实皮肤、某类脸型、电影感、正反打、完整人体运动或固定画风。
- 不规定前几秒必须发生什么，也不规定镜头、冲突、反转、旁白或对白数量。
- 不把调研中的热门题材变成选题白名单。
- 不把某个 Provider 当前能力写成长期创作原则。
- 不新增审批门、冻结层、SHA 锁定或证据链。

## 后续普通实验方法

当用户提供发布数据或允许比较版本时，采用最小可比较设计：控制受众、长度和分发条件，每轮只改变一个主要创作变量，把结果对齐到具体时间段，并区分观察、原因假设和结论。只有跨项目稳定复现的经验才适合进入长期 Skill；当前项目的局部经验由 AgentRuntime 记忆按需召回。

## 直接来源

- [中国新闻网：AI 微短剧的仿真人、二维/三维与动态漫形态](https://www.chinanews.com/cul/2026/06-17/10642242.shtml)
- [21 世纪经济报道：全 AI 与真人主导的人机协同制作](https://www.21jingji.com/article/20250626/herald/735a763497ffaea5fd0b3e8524c352ae.html)
- [人民日报海外版：AI 改变微短剧生产方式](https://paper.people.com.cn/rmrbhwb/pad/content/202605/11/content_30155877.html)
- [香港浸会大学 DataStory：AI 短剧产业与生产变化](https://datastory.hkbu.edu.hk/2026/ai-shortdrama/)
- [火山引擎 Dramart](https://www.volcengine.com/product/dramart)
- [可灵 AI 2.0：面向连续叙事的生成能力](https://ir.kuaishou.com/zh-hans/news-releases/news-release-details/kling-ai-advances-20-era-empowering-everyone-tell-great-stories)
- [SWaT: Statistical Modeling of Video Watch Time through User Behavior Analysis](https://arxiv.org/abs/2408.07759)
- [Delving Deep into Engagement Prediction of Short Videos](https://www.ecva.net/papers/eccv_2024/papers_ECCV/papers/07122.pdf)
- [Warm realism in Chinese microdramas](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0348845)
- [Audience in the Loop](https://arxiv.org/abs/2602.14045)
