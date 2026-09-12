---
provider: minimax_h3
label: H3 提示词编译技能
provenance: OFFICIAL_TRANSLATION_WITH_ADAPTER
upstream_sources: ["https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/SKILL.md", "https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/references/base-en.txt", "https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/references/ref-en.txt", "sources/design_library/3.0.14/h3-prompt-expert/meta.yaml"]
prompt_references: ["references/official_h3_prompt_writing_zh_cn.md", "references/common_compilation.md", "references/base_and_keyframe_modes.md", "references/reference_mode.md", "references/reference-intent-and-preservation.md"]
---
# H3 提示词编译适配层

> 官方 `h3-prompt-writing` 的忠实中文直译单独保存在 `references/official_h3_prompt_writing_zh_cn.md`。本文仅负责把官方规则接到当前项目的 ShotPlan、Route、Tool 和状态合同，不得冒充上游原文。

## 这项技能解决什么问题

把已经确定的 `ShotPlan`、`ReferencePlan`、对白、连续性状态和媒体绑定翻译成 H3 能执行的 Prompt IR。它位于导演决策之后、Provider 请求提交之前。

它不负责构思剧情，不重写对白，不替导演改变镜头，也不执行网络请求。格式 Skill 决定“这类视频应该怎样讲和怎样拍”，本技能只决定“既定镜头如何用 H3 的语言表达”，提交、轮询、下载和失败恢复仍由 Tool 与任务状态负责。

## 输入权威顺序

当前完整分镜以 `GenerationUnit` 为视频执行边界。`route.compile_h3_prompt` 可接收精确的 GenerationPlan document/version/unit、当前 Agent 选择的 `method_skill_ids` 和有序 `media_inputs`。此时以 `locked_generation_unit`、`locked_shots`、逐镜台词与 source window 为输入权威；每镜的可见名单限制该镜内容，整组媒体清单仅表示本组可用参考。单 Shot 调用仍兼容下方既有输入。

当信息冲突时，按以下顺序处理：

1. 用户明确锁定的事实和当前项目版本；
2. `locked_shot` 与 `locked_dialogue`；
3. 当前明确选中且可用的 `route_media_bindings` 与 `visible_assets`；
4. `structured_action_plan`、明确标为计划的 `previous_*_planned_terminal_state`，以及已提供的 `actual_previous_media_boundary`；
5. 当前格式 Skill 提供的表达重点；
6. `source_prompt` 中不与以上事实冲突的修辞建议。

计划终态不能证明真实采用媒体中已发生该动作。实际边界只用于当前镜头确实依赖的状态，未观察部分仍为未知。

低优先级内容只能补充表达，不能覆盖高优先级事实。项目 Bible 用于消歧，不得把本镜未声明可见的人物或道具带入画面。

## 当前项目支持的 H3 路线

- `DIRECT`：编译为 T2VA，从文字完整建立起始画面、动作、摄影机和声音。
- `FRAME_FIRST`：编译为 I2VA，严格从本次选中的首帧开始发展。
- `FRAME_LAST`：编译为 L2VA，根据本次选中的尾帧推导相容的前置状态，并让动作与构图在结尾精确收敛。
- `FRAME_FIRST_LAST`：编译为 FL2VA，在本次选中的首尾帧之间形成连续、可观察的变化路径。
- `REFERENCE`：编译为 Ref2VA，用图片、视频或音频锚定明确的身份、外观、运动、空间或声音。

五种模式均必须来自上游已选定的 `VideoGenerationRoute`；编译器不得为了追求画面而自行换路线。

## 输出合同

- 只输出可直接进入请求的 Prompt IR，不输出解释、Markdown、代码块或备选方案。
- 严格使用 `required_prompt_schema` 指定的字段名和顺序。
- 描述字段使用自然、具体的英文；锁定对白、歌词和画面内文字保留原语言。
- 包含精确时长、目标画幅和实际绑定的参考标签。
- `REFERENCE_IMAGE` 的实际上传标签保留为 `<Picture N>`。身份、外观等可复用内容使用 `<Subject N>`；`image_reference_role=COMPOSITION/LAYOUT_GUIDE` 的具体构图、动作初态或关键帧图仍使用 `<Picture N>`，按本次 `prompt_label` 编译。
- 同一语义主体的多张参考图共享一个 `<Subject N>`，在 `subject_definitions` 中合并说明每个源 `<Picture N>` 分别提供的视图或特征；不得把同一角色的正、侧、背面写成三个人。
- `FIRST_FRAME`、`LAST_FRAME` 及其他具体关键帧/构图锚点继续直接使用 `<Picture N>`，不转换为 `<Subject N>`。
- REFERENCE 中的构图锚是软参考，可以与人物或场景参考并用；API 的 `first_frame/last_frame` 是独立路线，不能混入任何 reference 输入。选择硬首帧时，画面本身应已包含需要的身份、站位与物件初态，不能靠另附人物卡弥补。
- 不添加未声明人物、道具、动作、对白、切镜、场景转换或画面文字。
- 最终文本不得超过当前适配器规定的字符上限；无法忠实容纳时应返回上游重新设计镜头，而不是暗中删减锁定事实。

生成单元的来源事实不受最终请求的 7000 字符限制：编译工具直接读取该组完整分镜，避免先把重复字段拼成超限请求再拒绝编译。最终 Prompt 必须保留每条 `required_timing_marker`、每次发声的逐字台词及全部参考标签。同组相邻镜头承载同一句时，以派生的 `request_dialogue_spans` 为完整声音范围：起始镜写一次完整 `<d>`，后续 `continuing_dialogue` 只承接声音，画面切换不要求重念。返回值中的 `compiled_prompt` 与有序 `media_inputs` 共同交给 `route.plan_h3` 的对应 generation-unit directive；Executor 和传输重试沿用已保存的 Prompt 与媒体顺序。

已选 Design Method 提供逐镜叙事、空间和声音连续性方法；最终 API 输出使用公开 H3 Prompt IR。安装包 Workflow 的固定中文骨架、Hub 参数和示例摄影风格不能覆盖当前 API 格式及用户声明的视觉方向。没有独立配音文件不代表静音；保留本片段已声明的原生对白、环境和动作声，另配音、BGM 与字幕按项目制作方向决定。

## 编译完成的判断

完成不是“写得像电影”，而是以下条件同时成立：

- 所有锁定事实都能在 Prompt 中找到且没有改变语义；
- 起始状态、动作发展、结束状态和声音按播放顺序可执行；
- 路线、字段结构、媒体标签与 Tool 即将提交的请求一致；
- 参考素材的保留范围和不应复制的内容都已说清；
- 单镜计划没有被擅自扩展为多镜，画外音没有误导画面人物开口；
- 下游验证器可以机械检查结构、时长、画幅、对白和引用标签。
