# H3 全参考模式

## 固定字段结构

REFERENCE / Ref2VA 使用六个字段，并严格保持此顺序：

1. `subject_definitions:`
2. `summary:`
3. `retention_analysis:`
4. `detailed_description:`
5. `overall_soundscape:`
6. `non_diegetic_music:`

实际上传的图片、视频、音频标签必须与 `route_media_bindings` 一一对应，依次使用 `<Picture N>`、`<Video N>`、`<Audio N>`。不得引用请求中不存在的编号，也不得遗漏已声明需要使用的媒体。

Prompt 中还要区分“上传的源文件”和“从源文件提取的可复用可见内容”：

- `REFERENCE_IMAGE` 上传后仍由 `<Picture N>` 追踪实际文件，但角色、场景、道具、服饰或风格等可复用内容必须定义为 `<Subject N>`，并说明它从哪个 `<Picture N>` 提取。
- 只有图片本身就是当前镜头的首帧、关键帧、尾帧、编辑目标帧或构图锚点时，才在时间线中直接把 `<Picture N>` 当作具体画面。
- `<Video N>` 和 `<Audio N>` 继续表示实际视频或音频源。当前 `REFERENCE_VIDEO` binding 仅把视频用作已声明的运动、时序、机位或连续性参考；需要稳定人物、物件或场景身份时，应另外绑定经批准的 `REFERENCE_IMAGE` 来建立 `<Subject N>`。

## subject_definitions

先给每个可复用内容一个稳定的 `<Subject N>` 和简洁描述，回答“目标视频实际要复用谁或什么”，并保留源 `<Picture N>` 以便追溯。例如：

`@男主 = <Subject 1>; <Subject 1> is the reusable visible character content for the male lead extracted from its reference images: <Picture 1> provides the front view; <Picture 2> provides the profile view; <Picture 3> provides the back view.`

主体可以是人物、地点、物件、视觉风格、运动特征或声音，但同一 `<Subject N>` 不能在后文改变身份。一张 `<Picture N>` 可提供多个主体，一个主体也可以综合多份参考。因此，同一角色的正面、侧面、背面或服饰细节图应共享一个 `<Subject N>`，每张实际源图仍保留独立 `<Picture N>` 供 Provider 上传与追溯。当 binding 显式具有同一 `subject_asset_id` 时按该主体合并；未显式声明时，只在它们解析到同一 Asset 时合并，不根据名称或提示词猜测。

定义应只包含参考真正负责的特征。例如角色图负责面部、发型和服装，不自动负责图中的背景；场景图负责空间布局和材质，不自动把图中路人带入镜头；音频负责声线或声音质感，不自动复制其语义内容。

## summary

用短句概括目标视频的总时长、画幅、类型、核心动作和实际主体。这里是生成目标摘要，不是重新讲一遍项目背景，也不应引入未在镜头中出现的角色或道具。

## retention_analysis

对每份参考明确三件事：

- 保留什么：身份、服装、场景结构、道具外观、声音质感或运动方式；
- 如何转移：转移到哪个主体、哪个时间段、哪个动作或哪个声音；
- 不复制什么：参考背景、无关人物、构图、原视频事件、原音频文案、字幕、水印等。

保留范围越明确，越能避免“参考图变成额外镜头”或“参考视频剧情被照搬”。不同参考发生冲突时，不在 Prompt 中同时要求两套事实，应返回上游重新选择或修复输入。

## detailed_description

按通用编译方法写完整播放时间线，并在需要生效的位置引用标签。不要只在开头列出参考后便不再说明用途。

典型表达逻辑是：

1. 开场画面与主体位置；
2. `<Subject N>` 表示的角色或环境如何出现在当前镜头；仅当图片是具体帧或构图锚点时才在此使用 `<Picture N>`；
3. `<Video N>` 的运动、表演或镜头特征在哪个动作阶段被采用；
4. `<Audio N>` 的声线或声音素材由谁、在何时使用；
5. 当前镜头自己的动作、摄影机和声音如何连续发展；
6. 结束状态如何满足 `locked_shot` 并衔接下一镜。

参考素材只负责其声明的关系。不得因为某张图细节丰富，就把图中所有物体都写入目标视频。

## 声音与音乐

对白、动作声、环境声和参考声音的实际使用写入 `overall_soundscape`。若参考音频只用于声线，应明确不复制原文案；若用于具体音效，应说明其出现时机和画面来源。非叙事音乐单独写入 `non_diegetic_music`，无音乐时写 `N/A`。

## 提交前专项检查

- 六个字段齐全且顺序正确；
- 每个实际参考标签都被定义和使用；
- `REFERENCE_IMAGE` 生成的 `<Subject N>` 已与源 `<Picture N>` 明确关联，后续镜头不再把源图整幅当成角色或场景；
- `retention_analysis` 同时说明保留、转移与不复制项；
- 没有把参考背景、字幕、无关角色或原视频事件带进来；
- 参考用途与 `route_media_bindings` 描述一致；
- 对白原文、说话人和声音来源没有变化；
- 总时长、9:16 画幅、开始状态和结束状态明确。
