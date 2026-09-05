# MiniMax 官方 H3 Prompt Writing Skill 中文直译

> 上游原文：`MiniMax-AI/MiniMax-H3` commit `d21241f0a4b3acbb34c97dae47fa417b7065e438` 的 `skills/h3-prompt-writing/SKILL.md`。以下仅做中文翻译，不加入本项目的 Tool、路由或状态机规则。

## H3 提示词编写

### 工作流程

1. 识别输入模式：T2VA、I2VA、FL2VA、L2VA 或全参考 Ref2VA。
2. 对于基础文本/关键帧模式，读取 `references/base-en.txt` 并遵循其最终提示词结构。
3. 对于全参考模式，读取 `references/ref-en.txt` 并遵循其六段式重写格式。
4. 保留所选指南中的精确字段名、段落顺序、标签和时间记法。

### 基础模式

- T2VA：从文本构建完整的声画时间线。
- I2VA：从首帧开始，并由此向前发展。
- FL2VA：描述首帧与尾帧之间的连续路径。
- L2VA：推断合理的开场，并收敛到已提供的尾帧。

按 `references/base-en.txt` 中展示的顺序使用 `integrated_multimodal_description`、`overall_soundscape` 和 `non_diegetic_music`。

### 全参考模式

Ref2VA 重写按照以下顺序使用 `subject_definitions`、`summary`、`retention_analysis`、`detailed_description`、`overall_soundscape` 和 `non_diegetic_music`。参考标签在所有段落中始终保持一致。

官方 `ref-en.txt` 区分四类标签：

- `<Subject N>`：从参考资产中抽象出来、可在目标视频中复用或修改的可见内容；
- `<Picture N>`：作为具体目标帧、关键帧或构图锚点的参考图片；
- `<Video N>`：用于编辑、续写或提供整段时序结构的参考视频；
- `<Audio N>`：被复制或参考的音频信号。

如果图片只用来定义角色、场景、服装或风格，不应为它创建独立的 `<Picture N>` 内容项；应定义 `<Subject N>`，并在定义中引用它来自的 `<Picture N>`。图片本身是首帧、关键帧、尾帧、编辑帧或构图锚点时，才直接使用 `<Picture N>`。

一个 `<Subject N>` 可以由多份参考资产共同定义，并需要说明每份资产分别提供什么；同一份参考资产也可以提供多个独立主体。

阅读 `references/ref-en.txt` 以了解标签规则、保留分析和完整示例。

### 输出规则

- 使用英文编写重写段落；对白、歌词和画面内可见文字保留其原始语言。
- 按构图、主体、环境、动作、摄影机、声音以及被引用内容出现的精确时点来描述每个镜头。
- 避免剧情摘要、未解决的参考标签，以及与请求时长不匹配的时间描述。

### 获得更好结果的建议

- 始终使描述的总时长与所请求的视频时长（4–15 秒）一致。
- 在每个段落中保持参考标签一致，例如 `<Picture 1>`、`<Video 1>`、`<Audio 1>`。
- 优先使用具体的视觉和听觉细节，而不是“cinematic”或“beautiful”这类抽象词。
- 使用关键帧（I2VA / FL2VA / L2VA）时，清楚说明首帧和/或尾帧如何与时间线连接。
