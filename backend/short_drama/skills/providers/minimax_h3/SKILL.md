---
provider: minimax_h3
label: MiniMax H3 提示词编译技能
prompt_references: ["references/common_compilation.md", "references/base_and_keyframe_modes.md", "references/reference_mode.md"]
---
# MiniMax H3 提示词编译技能

## 这项技能解决什么问题

把已经确定的 `ShotPlan`、`ReferencePlan`、对白、连续性状态和媒体绑定翻译成 MiniMax H3 能执行的 Prompt IR。它位于导演决策之后、Provider 请求提交之前。

它不负责构思剧情，不重写对白，不替导演改变镜头，也不执行网络请求。格式 Skill 决定“这类视频应该怎样讲和怎样拍”，本技能只决定“既定镜头如何用 H3 的语言表达”，提交、轮询、下载和失败恢复仍由 Tool 与任务状态负责。

## 输入权威顺序

当信息冲突时，按以下顺序处理：

1. 用户明确锁定的事实和当前项目版本；
2. `locked_shot` 与 `locked_dialogue`；
3. 已批准的 `route_media_bindings` 与 `visible_assets`；
4. `structured_action_plan` 和 `previous_shot_terminal_state`；
5. 当前格式 Skill 提供的表达重点；
6. `source_prompt` 中不与以上事实冲突的修辞建议。

低优先级内容只能补充表达，不能覆盖高优先级事实。项目 Bible 用于消歧，不得把本镜未声明可见的人物或道具带入画面。

## 当前项目支持的 H3 路线

- `DIRECT`：编译为 T2VA，从文字完整建立起始画面、动作、摄影机和声音。
- `FRAME_FIRST`：编译为 I2VA，严格从批准首帧开始发展。
- `FRAME_FIRST_LAST`：编译为 FL2VA，在批准首尾帧之间形成连续、可观察的变化路径。
- `REFERENCE`：编译为 Ref2VA，用图片、视频或音频锚定明确的身份、外观、运动、空间或声音。

MiniMax 官方还描述了尾帧引导等模式，但当前项目没有对应的 `VideoGenerationRoute`。编译器不得凭文档自行制造未注册路线；需要新增路线时，应先扩展类型模型、路线 Tool、请求适配和测试。

## 输出合同

- 只输出可直接进入请求的 Prompt IR，不输出解释、Markdown、代码块或备选方案。
- 严格使用 `required_prompt_schema` 指定的字段名和顺序。
- 描述字段使用自然、具体的英文；锁定对白、歌词和画面内文字保留原语言。
- 包含精确时长、目标画幅和实际绑定的参考标签。
- 不添加未声明人物、道具、动作、对白、切镜、场景转换或画面文字。
- 最终文本不得超过当前适配器规定的字符上限；无法忠实容纳时应返回上游重新设计镜头，而不是暗中删减锁定事实。

## 编译完成的判断

完成不是“写得像电影”，而是以下条件同时成立：

- 所有锁定事实都能在 Prompt 中找到且没有改变语义；
- 起始状态、动作发展、结束状态和声音按播放顺序可执行；
- 路线、字段结构、媒体标签与 Tool 即将提交的请求一致；
- 参考素材的保留范围和不应复制的内容都已说清；
- 单镜计划没有被擅自扩展为多镜，画外音没有误导画面人物开口；
- 下游验证器可以机械检查结构、时长、画幅、对白和引用标签。
